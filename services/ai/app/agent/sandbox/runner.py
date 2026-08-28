"""
Running a script the gate has already approved.

The second of three layers. `gate.py` is the one that has to be right, because
it is a proof: it decides what the script can *say*. This one is containment,
and it exists because a proof about a syntax tree is a proof about a syntax
tree - `python-pptx` is a large library, the interpreter is larger, and a
containment layer costs little and covers what static reasoning cannot.

What it does, and why each part:

* **A separate process.** The parent is holding a database session, decrypted
  provider keys and other tenants' material. The script never runs in it.
* **A scrubbed environment.** Not `{**os.environ}`. `preview_generator.py`
  passes the whole environment to LibreOffice, which is fine for LibreOffice
  and would be handing `ZHIPU_API_KEY`, `DATABASE_URL` and
  `SETTINGS_ENCRYPTION_KEY` to agent-written code.
* **`setrlimit` for CPU, memory, file size and process count.** A limit the
  kernel enforces, applied in the child before `exec`, so a runaway is killed
  rather than negotiated with. `RLIMIT_NPROC` is what makes a fork bomb a
  failed `fork()`.
* **A wall clock on top of the CPU limit.** CPU time does not advance while a
  process sleeps, and a script that sleeps forever is as stuck as one that
  spins.
* **No network.** `unshare -rn` where the kernel allows it, and where it does
  not, the container's own isolation. Reported either way rather than assumed,
  because a sandbox whose network isolation silently did not apply is worse
  than one that never claimed it.
* **One writable directory.** The work dir, which the parent creates, fills
  and deletes, and which is the child's working directory - so the deck is
  simply `deck.pptx` and the gate can refuse every path that is not
  relative.

The shape follows `PreviewGenerator.convert_to_pdf`: an argv list rather than
a shell string, a hard timeout, and the *artefact* verified rather than the
exit code trusted.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from app.observability.logging import get_logger

logger = get_logger(__name__)

# What the script gets, and no more. Generous enough that a chart renders,
# mean enough that nothing runs away.
CPU_SECONDS = 10
WALL_SECONDS = 30
ADDRESS_SPACE_BYTES = 1_024 * 1024 * 1024      # 1 GiB
OUTPUT_FILE_BYTES = 64 * 1024 * 1024           # 64 MiB
MAX_PROCESSES = 64                              # enough for threads, not a bomb
MAX_OUTPUT_CHARS = 8_000                        # of stdout or stderr, kept

# The deck's name inside the work directory. The script runs *in* that
# directory, so this is the whole path it needs - and the gate refuses any
# path that is not relative, so the two agree by construction rather than by
# a prefix both have to remember.
DECK_NAME = "deck.pptx"

# The whole environment the script gets. Everything else is dropped, which is
# the point: no provider key, no database URL, no encryption key.
#
# `MPLBACKEND` because matplotlib picks an interactive backend by default and
# then fails on a process with no display. `HOME` because several libraries
# write a cache and crash if they cannot.
def _environment(work_dir: str) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": work_dir,
        "TMPDIR": work_dir,
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": work_dir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        # No site-packages from the user's home, and no current directory on
        # the path - a script must not be able to shadow `pptx` with a file
        # it wrote.
        "PYTHONNOUSERSITE": "1",
    }


@dataclass(frozen=True)
class Result:
    """What running a script produced."""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    # Why it did not run, or did not finish, in a sentence the model can act
    # on. Empty when it ran to completion.
    error: str = ""
    # Files the script left in the work dir, by name, read back by the caller.
    outputs: dict[str, bytes] = field(default_factory=dict)
    networked: bool = False

    @property
    def failure(self) -> str:
        """The whole story of a failure, for handing back to the model.

        The traceback line and the reason together, because a script that
        failed on line 12 and a script that was killed at 30 seconds need
        different fixes and "it did not work" distinguishes neither.
        """
        parts = [self.error.strip(), self.stderr.strip()]
        return "\n".join(part for part in parts if part)


def run(source: str, *, deck: Optional[bytes] = None,
        wants: tuple[str, ...] = ()) -> Result:
    """Run ``source`` on a copy of ``deck``, and return what it produced.

    ``wants`` names the files to read back out of the work dir. The deck is
    always read back when one was supplied, because editing it is the point.

    The caller is expected to have run `gate.check` first. This does not
    re-run it: a runner that silently re-checked would let a caller forget to,
    and a caller that forgot would be relying on containment alone.
    """
    work_dir = tempfile.mkdtemp(prefix="ts_sandbox_")
    try:
        os.chmod(work_dir, 0o700)
        script_path = os.path.join(work_dir, "script.py")
        with open(script_path, "w", encoding="utf-8") as handle:
            handle.write(source)

        if deck is not None:
            with open(os.path.join(work_dir, DECK_NAME), "wb") as handle:
                handle.write(deck)

        argv, networked = _argv(script_path, work_dir)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=WALL_SECONDS,
                cwd=work_dir,
                env=_environment(work_dir),
                preexec_fn=_drop_privileges,   # noqa: PLW1509 - the whole point
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("sandbox_timed_out", seconds=WALL_SECONDS)
            return Result(
                ok=False, networked=networked,
                error=(f"The script was still running after {WALL_SECONDS} "
                       "seconds and was stopped. Work the numbers out before "
                       "drawing, and do not wait on anything."),
            )
        except (OSError, ValueError) as exc:
            logger.error("sandbox_could_not_start", error=str(exc))
            return Result(ok=False, networked=networked,
                          error=f"The script could not be started ({exc}).")

        stdout = (completed.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (completed.stderr or "")[:MAX_OUTPUT_CHARS]

        if completed.returncode != 0:
            logger.info("sandbox_script_failed", code=completed.returncode)
            return Result(
                ok=False, stdout=stdout, stderr=stderr, networked=networked,
                error=_why(completed.returncode),
            )

        names = tuple(wants) + ((DECK_NAME,) if deck is not None else ())
        outputs = {}
        for name in names:
            path = os.path.join(work_dir, os.path.basename(name))
            if os.path.exists(path) and os.path.getsize(path) <= OUTPUT_FILE_BYTES:
                with open(path, "rb") as handle:
                    outputs[name] = handle.read()

        logger.info("sandbox_script_ran", outputs=sorted(outputs),
                    networked=networked, chars=len(source))
        return Result(ok=True, stdout=stdout, stderr=stderr,
                      outputs=outputs, networked=networked)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _argv(script_path: str, work_dir: str) -> tuple[list[str], bool]:
    """The command to run, and whether the network could be taken away.

    ``unshare -rn`` puts the child in a new network namespace with nothing in
    it but loopback. It needs either root or unprivileged user namespaces, so
    it is *tried* rather than assumed - and when it is unavailable the caller
    is told, so "no network" is never a claim this makes without having done
    it.
    """
    # `-I` is isolated mode: no user site-packages, no `PYTHONPATH`, and the
    # script's own directory is not put on `sys.path`, so a file the script
    # wrote cannot shadow `pptx`. Not `-S` as well: that removes
    # site-packages altogether, and `pptx` - the library the whole tool
    # exists to give the script - lives there.
    inner = [sys.executable, "-I", script_path]
    if _can_unshare():
        return ["unshare", "--map-root-user", "--net", "--"] + inner, True
    logger.warning("sandbox_network_not_isolated",
                   detail="unshare unavailable; relying on the container")
    return inner, False


_UNSHARE: Optional[bool] = None


def _can_unshare() -> bool:
    """Whether this host lets us drop the network, decided once and cached."""
    global _UNSHARE
    if _UNSHARE is not None:
        return _UNSHARE
    if shutil.which("unshare") is None:
        _UNSHARE = False
        return False
    try:
        probe = subprocess.run(
            ["unshare", "--map-root-user", "--net", "--", "true"],
            capture_output=True, timeout=10, check=False,
        )
        _UNSHARE = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _UNSHARE = False
    return _UNSHARE


def _drop_privileges() -> None:
    """Applied in the child, between fork and exec.

    Everything here is irreversible for that process, which is why it belongs
    after the fork: `setrlimit` cannot be raised again by the process it was
    applied to, and a new session detaches it from the parent's terminal so it
    cannot signal anything but itself.
    """
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS,
                       (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE,
                       (OUTPUT_FILE_BYTES, OUTPUT_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    # No core dump: a crash inside a 1 GiB address space would otherwise
    # write a 1 GiB file into the work dir.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)


def _why(code: int) -> str:
    """What a non-zero exit means, in words worth handing back."""
    if code < 0:
        signal_number = -code
        if signal_number == 9:
            return ("The script was killed - it asked for more memory or more "
                    "time than a slide's worth of work is given.")
        if signal_number == 24:   # SIGXCPU
            return (f"The script used more than {CPU_SECONDS} seconds of CPU "
                    "and was stopped.")
        if signal_number == 25:   # SIGXFSZ
            return "The script tried to write a file larger than it is given."
        return f"The script was killed by signal {signal_number}."
    return f"The script exited with status {code}."

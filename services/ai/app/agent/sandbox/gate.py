"""
Whether a script the agent wrote is one this service will run.

The primary control, and a deterministic one. Nothing dangerous is *rejected
at runtime* here - nothing dangerous can be **expressed**. The script is
parsed, every node is walked, and anything outside a stated allowlist refuses
the whole script with the reason named. A gate that decides by pattern
matching on source text is a gate that a differently-spelled string gets past;
a gate that walks the tree knows what the code is.

`app/connections/catalog.py` refused arbitrary execution once, in writing:
*"letting a tenant name a process to execute is arbitrary code execution in a
process that is holding other tenants' data and decrypted API keys."* That
sentence is still true, and it is why the import allowlist has no `app.*` in
it and never will: `app.db` is the tenant database and `app.config` holds the
decrypted keys. Everything this gate does follows from wanting a script to
draw a chart without being able to name either.

Three layers guard the execution, and this is the first. The second is a
privilege-dropped subprocess with no network and a tmpfs it cannot escape
(`runner.py`); the third is the container itself. This one is the layer that
has to be right, because it is the only one that is a proof rather than a
containment.

**Modelled on two things this codebase already does.** `mermaid.check()` is a
pure `Optional[str]` "why not", called before anything runs, so a refusal is
a sentence the model can act on rather than a stack trace. And
`importer.FORBIDDEN_TAGS` refuses a whole subtree rather than stripping the
offending node, because a partially sanitised input is one nobody can reason
about.

**What the allowlist governs.** What the *script* may name, not what those
libraries do internally. `python-pptx` reaching for `lxml` is fine, because
the script cannot write `lxml` itself: the gate is about the names the
author's code can reach, and a library's own imports are not names the author
controls.
"""

from __future__ import annotations

import ast
from typing import Optional

# Top-level modules a script may import, and everything under them. `pptx` is
# here because the whole point is a script that edits the deck: without it the
# code could draw a figure and not place it. `matplotlib` and `PIL` are here
# because a script that computes something usually wants to draw it.
#
# What is deliberately absent, and why:
#   os, sys, subprocess, shutil, pathlib, socket, http, urllib, requests
#       - the process, the filesystem and the network.
#   importlib, builtins, ctypes, pickle, marshal, gc, inspect
#       - ways to reach a name the allowlist does not contain.
#   app
#       - this service's own code: the tenant database and the decrypted
#         provider keys are two attribute lookups from `app`.
ALLOWED_IMPORTS = frozenset({
    "pptx",
    "math", "statistics", "decimal", "fractions", "random",
    "json", "csv", "re", "textwrap", "string", "datetime", "calendar",
    "itertools", "functools", "operator", "collections", "typing", "enum",
    "dataclasses", "copy", "uuid", "base64", "io", "colorsys", "bisect",
    "heapq", "unicodedata", "numbers", "array", "zlib",
    "matplotlib", "numpy", "PIL",
})

# Names that reach code or state by string, so an allowlist over imports
# cannot see them. `__import__` is the obvious one; `eval`, `exec` and
# `compile` turn data into code; `globals`, `locals` and `vars` hand back the
# namespace the allowlist was defining; `input` blocks forever on a process
# with no stdin.
FORBIDDEN_NAMES = frozenset({
    "__import__", "eval", "exec", "compile", "globals", "locals", "vars",
    "breakpoint", "input", "exit", "quit", "help", "memoryview",
})

# Attribute names that walk out of the object graph into the interpreter.
# Refused wherever they appear rather than only on suspicious receivers,
# because the receiver is often computed: `type(x).__subclasses__()` reaches
# every class in the process from any object at all.
FORBIDDEN_ATTRIBUTES = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__", "__globals__",
    "__code__", "__closure__", "__func__", "__self__", "__dict__",
    "__builtins__", "__loader__", "__spec__", "__reduce__",
    "__reduce_ex__", "__getattribute__", "__init_subclass__",
    "__subclasshook__", "gi_frame", "cr_frame", "f_globals", "f_locals",
    "f_back",
})

# Statements with no legitimate use in a script that draws a figure, each one
# a way to reach something the rest of this gate has excluded.
FORBIDDEN_NODES: tuple[tuple[type, str], ...] = (
    (ast.Global, "`global` reaches state outside the script"),
    (ast.Nonlocal, "`nonlocal` reaches state outside the script"),
)

# The deck, as the script sees it. The script runs *in* its work directory,
# so the file is simply there under its own name.
#
# It is deliberately not an absolute path. An earlier version held every path
# to a `/work/` prefix, which reads well and cannot be made to work: a bind
# mount at a fixed path needs a mount namespace, a mount namespace needs
# CAP_SYS_ADMIN, and the container hardening this sandbox depends on drops
# every capability. Two runs also cannot share one absolute path.
#
# Relative is the stronger rule anyway. "No absolute path, no `..`, no `~`"
# is decidable by looking at the string, needs no privilege, and is the same
# rule whatever directory the parent hands over.
DECK_NAME = "deck.pptx"

# A script long enough to hide something in is a script nobody reviewed. This
# is generous for a figure and mean for a program.
MAX_CHARS = 20_000

# Bounds that make an accidental runaway a refusal rather than a timeout. The
# runner enforces real limits with `setrlimit`; these catch the shape of the
# thing before it costs a process.
MAX_LOOP_DEPTH = 3


def check(source: str) -> Optional[str]:
    """Why this script will not be run, or None when it will.

    A sentence, not a stack trace: the model gets to try again while it still
    has the context to, which is what `draw_svg` says a refusal is for.
    """
    text = (source or "").strip()
    if not text:
        return "No code was given."
    if len(text) > MAX_CHARS:
        return (f"That script is {len(text)} characters and the limit is "
                f"{MAX_CHARS}. A script that long is doing more than one "
                "slide needs.")

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return f"That script does not parse: {exc.msg} on line {exc.lineno}."

    for node in ast.walk(tree):
        problem = _node_problem(node)
        if problem:
            line = getattr(node, "lineno", None)
            return f"{problem}" + (f" (line {line})" if line else "")

    depth = _loop_depth(tree)
    if depth > MAX_LOOP_DEPTH:
        return (f"That script nests loops {depth} deep and the limit is "
                f"{MAX_LOOP_DEPTH}. Work the data out before drawing it.")
    return None


def _node_problem(node: ast.AST) -> Optional[str]:
    for kind, reason in FORBIDDEN_NODES:
        if isinstance(node, kind):
            return reason

    if isinstance(node, ast.Import):
        for alias in node.names:
            if not _import_allowed(alias.name):
                return _refuse_import(alias.name)
        return None

    if isinstance(node, ast.ImportFrom):
        # A relative import has no module name to check and resolves against
        # a package this script is not in, so there is nothing it can
        # legitimately mean here.
        if node.level:
            return "A relative import has nothing to resolve against here."
        if not _import_allowed(node.module or ""):
            return _refuse_import(node.module or "")
        return None

    if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
        return f"`{node.id}` is not available to a script."

    if isinstance(node, ast.Attribute):
        if node.attr in FORBIDDEN_ATTRIBUTES:
            return f"`.{node.attr}` reaches outside the script."
        # Any other dunder is refused too. Naming them all is a list that
        # goes stale with each Python release; the rule is that a script
        # drawing a figure has no business in the interpreter's own
        # namespace, and that rule does not go stale.
        if node.attr.startswith("__") and node.attr.endswith("__"):
            return f"`.{node.attr}` reaches outside the script."

    if isinstance(node, ast.Call):
        problem = _call_problem(node)
        if problem:
            return problem
    return None


def _call_problem(node: ast.Call) -> Optional[str]:
    name = _called_name(node)
    if name == "getattr" and len(node.args) > 1 and not _is_literal(node.args[1]):
        # A literal `getattr(x, "width")` is an ordinary attribute read and
        # the attribute rules above already cover it. A computed one is the
        # allowlist being addressed by string, which is the one thing an
        # allowlist cannot see.
        return "`getattr` with a computed name is a way past this check."
    if name in ("setattr", "delattr") and len(node.args) > 1 \
            and not _is_literal(node.args[1]):
        return f"`{name}` with a computed name is a way past this check."
    if name == "open":
        return _open_problem(node)
    return None


def _open_problem(node: ast.Call) -> Optional[str]:
    """`open` is allowed, in the script's own directory, on a stated path."""
    if not node.args:
        return "`open` needs a path, and it has to be one this script states."
    target = node.args[0]
    if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
        return ("`open` needs a literal filename. A computed path is one this "
                f"check cannot read. The deck is {DECK_NAME}, in the "
                "directory the script runs in.")
    path = target.value
    if path.startswith("/") or path.startswith("~") or ".." in path \
            or "\\" in path:
        return (f"`open` may only reach the script's own directory, and "
                f"{path!r} leaves it. The deck is {DECK_NAME}, beside the "
                "script - no path in front of it.")
    return None


def _import_allowed(name: str) -> bool:
    return bool(name) and name.split(".", 1)[0] in ALLOWED_IMPORTS


def _refuse_import(name: str) -> str:
    root = (name or "?").split(".", 1)[0]
    return (f"`{root}` is not a module a script may import. Available: "
            f"{', '.join(sorted(ALLOWED_IMPORTS))}.")


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _loop_depth(tree: ast.AST) -> int:
    """The deepest nesting of loops in this script."""

    def walk(node: ast.AST, depth: int) -> int:
        deepest = depth
        for child in ast.iter_child_nodes(node):
            inner = depth + 1 if isinstance(child, (ast.For, ast.While,
                                                    ast.AsyncFor)) else depth
            deepest = max(deepest, walk(child, inner))
        return deepest

    return walk(tree, 0)

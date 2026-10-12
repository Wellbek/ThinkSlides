"""
Whether the deck a script handed back is one this service will keep.

The script never sees the live `Presentation`. That would not be a sandbox at
all: it is the same object graph the renderer, the database session and the
decrypted keys live beside, and an object reference is not a boundary. What
crosses instead is a file - written out, edited, handed back - and a file can
be checked before it is believed.

So this is the third deterministic gate, after the AST and the process. It
asks only questions with mechanical answers:

* does it open as a presentation at all;
* is it within a size a deck can be;
* does it carry a macro part, an OLE object or an embedded executable;
* does it reach out to anything on the network;
* is its slide count within the bound the caller declared.

A file that fails is discarded with the reason named, not repaired. A
partially trusted deck is one nobody can reason about, which is the same
argument `importer.FORBIDDEN_TAGS` makes about a partially sanitised SVG.
"""

from __future__ import annotations

import io
import zipfile
from typing import Optional

from app.observability.logging import get_logger

logger = get_logger(__name__)

# A deck larger than this is not a deck a script edited, it is a script
# writing a file. The renderer's own output is a small fraction of it.
MAX_BYTES = 48 * 1024 * 1024

# Parts that carry code, or a container for it. A `.pptm` is a macro-enabled
# deck; `vbaProject.bin` is the macro payload itself; an OLE object is an
# arbitrary embedded file that PowerPoint will offer to open.
FORBIDDEN_PART_MARKERS = (
    "vbaproject.bin",
    "/embeddings/",
    "activex",
)

# Extensions that have no business inside a deck this service produced.
#
# Not `.bin`. Every deck python-pptx writes carries
# `ppt/printerSettings/printerSettings1.bin`, which is an ordinary OOXML
# part - a blanket `.bin` rule refused every real deck, including the ones
# this service produces itself. The parts that actually carry code are named
# above, specifically.
FORBIDDEN_EXTENSIONS = (
    ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".js", ".vbs", ".ps1",
    ".sh", ".jar",
)

# Relationship types that make a deck reach outside itself when a reader
# opens it: an embedded OLE object, an attached template fetched from a path,
# a linked external workbook. A hyperlink is not one of them - a deck citing a
# real page is the point of citing it - so `hyperlink` is what an external
# target is allowed to be, and nothing else is.
FORBIDDEN_RELATIONSHIP_TYPES = frozenset({
    "oleobject", "package", "attachedtemplate", "externallinkpath", "frame",
    "aFChunk", "xmlmaps",
})

# The one relationship a deck may point outside itself with.
ALLOWED_EXTERNAL_TYPES = frozenset({"hyperlink"})


def problem(data: bytes, *, max_slides: int = 200) -> Optional[str]:
    """Why this file will not be adopted, or None when it will."""
    if not data:
        return "The script produced no deck."
    if len(data) > MAX_BYTES:
        return (f"The deck came back at {len(data) // (1024 * 1024)}MB and the "
                f"limit is {MAX_BYTES // (1024 * 1024)}MB.")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            broken = archive.testzip()
            if broken:
                return f"The deck's {broken} part is corrupt."
            found = _part_problem(names)
            if found:
                return found
            found = _relationship_problem(archive, names)
            if found:
                return found
    except zipfile.BadZipFile:
        return "The file the script saved is not a presentation."

    # Opened last, because it is the expensive check and the cheap ones have
    # already ruled out the files that would make it throw in interesting
    # ways.
    try:
        from pptx import Presentation

        deck = Presentation(io.BytesIO(data))
        count = len(deck.slides)
    except Exception as exc:
        return f"The deck the script saved will not open ({exc})."

    if count < 1:
        return "The deck came back with no slides in it."
    if count > max_slides:
        return (f"The deck came back with {count} slides and this run has at "
                f"most {max_slides}.")
    return None


def _part_problem(names: list[str]) -> Optional[str]:
    for name in names:
        lowered = name.lower()
        for marker in FORBIDDEN_PART_MARKERS:
            if marker in lowered:
                return (f"The deck carries {name}, which is a macro or an "
                        "embedded object. A deck this service produced has "
                        "neither.")
        if lowered.endswith(FORBIDDEN_EXTENSIONS):
            return (f"The deck carries {name}, which is an executable part. "
                    "A deck this service produced has none.")
    return None


def _relationship_problem(archive: zipfile.ZipFile,
                          names: list[str]) -> Optional[str]:
    """Anything the deck would fetch or launch when a reader opens it.

    The relationship's *type* is the last segment of its Type URI, and it has
    to be read as one. Matching the URI as a substring looked equivalent and
    was not: every OOXML relationship's namespace is
    `.../package/2006/relationships/...`, so a rule looking for "/package"
    refused every real deck, including the ones this service writes itself.
    """
    import xml.etree.ElementTree as ET

    for name in names:
        if not name.lower().endswith(".rels"):
            continue
        try:
            root = ET.fromstring(archive.read(name))
        except (KeyError, ET.ParseError):
            return f"The deck's {name} could not be read."
        for relationship in root:
            kind = str(relationship.get("Type") or "").rsplit("/", 1)[-1]
            external = str(relationship.get("TargetMode") or "") == "External"
            if kind.lower() in FORBIDDEN_RELATIONSHIP_TYPES:
                return (f"The deck declares a {kind} relationship, which is a "
                        "document reaching outside itself when a reader "
                        "opens it.")
            if external and kind.lower() not in ALLOWED_EXTERNAL_TYPES:
                return (f"The deck points at {relationship.get('Target')!r} "
                        f"as an external {kind}. A deck may link to a page "
                        "and fetch nothing.")
    return None

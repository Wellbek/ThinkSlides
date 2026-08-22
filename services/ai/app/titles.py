"""
What register a slide's headline is in, and whether it earns it.

Slide titles name the topic. In the reference deck they state the finding:

    Applied AI recorded the highest innovation score of all 14 trends, while
    clean energy drew the most interest and investment.

A reader who reads only that deck's headlines has read its argument. A reader
who reads only ours has read a table of contents. This is the standard
consulting convention - the action title - and it changes what a deck is for.

The reference is not uniform about it, and that matters. Its per-chapter pages
use a fixed interrogative series ("Why should leaders pay attention?"), and the
action-title form appears on exhibits and summaries. Both are deliberate. So
this is not "always write a claim"; it is **know which register the page is in
and write a claim when the page has one**:

* ``claim``    - the page carries a finding, and the headline states it
* ``question`` - the chapter grammar, repeated verbatim across chapters
* ``label``    - a section break, a divider, a cover

The obvious failure is a model told to write claims writing confident claims
from thin data. So a claim has to be *supported by content on its own slide*,
and a slide with nothing to point at falls back to a label rather than
inventing a finding. That is the same failure #78 recorded, where deep mode
charted whatever search returned and the title outran the data.

This module is the single definition of all of that. The validator rejects
against it, the content designer writes against it, and the style-fidelity
harness scores against it - which makes the harness's title metric a
conformance check rather than an independent judgement, and it is documented
as such there. The other three substance metrics stay independent.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

CLAIM = "claim"
QUESTION = "question"
LABEL = "label"
REGISTERS = frozenset({CLAIM, QUESTION, LABEL})

# Verbs that turn a topic into a statement. Curated rather than exhaustive:
# the point is that the rule can be explained to whoever hits it, and a list
# somebody can read beats a parser nobody can predict. The morphological
# fallback below covers what the list misses.
CLAIM_VERBS = frozenset("""
is are was were has have had will would can could should must does do did
grow grows grew rise rises rose fall falls fell drive drives drove lead leads
led gain gains gained lose loses lost cut cuts doubled tripled halved
outpace outpaces outpaced outperform outperformed shift shifts shifted
record records recorded reach reaches reached remain remains remained stay
stays stayed account accounts accounted expand expands expanded contract
contracted deliver delivers delivered enable enables enabled require requires
required depend depends exceed exceeds exceeded lag lags lagged trail trails
trailed add adds added drop drops dropped climb climbs climbed slow slowed
sped overtook widen widened narrow narrowed hold holds held sit sits sat
rank ranks ranked accelerate accelerated flatten flattened soften softened
tighten tightened loosen loosened concentrate concentrated shrink shrank
represent represents make makes made take takes took see sees saw
turn turns turned become becomes became emerge emerges emerged
provide provides provided need needs needed face faces faced
show shows showed find finds found build builds built bring brings brought
buy buys bought continue continues continued expect expects expected
rely relies relied prefer prefers preferred upend upends upended
rewire rewires rewired optimize optimizes optimized protect protects protected
create creates created serve serves served meet meets met beat beats
""".split())

# A claim needs something to weigh: a comparison or a quantity. "Revenue
# increased" is a statement and still not a finding.
COMPARATIVES = frozenset("""
more less higher lower faster slower bigger smaller greater fewer better worse
most least highest lowest fastest slowest largest smallest best worst than
versus vs while whereas despite although but only first second third leading
dominant majority minority twice half double triple
""".split())

_QUANTITY_RE = re.compile(
    r"(\d|\bhalf\b|\bthird\b|\bquarter\b|\bdouble\b|\btriple\b|\btwice\b|%|\$|€|£)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]+")

# A headline shorter than this cannot be a claim whatever words are in it.
MIN_CLAIM_WORDS = 4
# ...and one longer than this has stopped being a headline.
MAX_CLAIM_WORDS = 30

# Content that can back a claim. A slide with none of it has nothing to state
# a finding *from*.
EVIDENCE_CONTENT_TYPES = frozenset({
    "chart", "table", "metrics", "statCallout", "rowBand", "accentCard",
})


def normalize_register(value: Any, default: str = LABEL) -> str:
    """Map a caller-supplied register onto a known one."""
    text = str(value or "").strip().lower()
    return text if text in REGISTERS else default


def has_finite_verb(title: str) -> bool:
    """
    Whether a headline contains a verb that makes it a sentence.

    Every entry in ``CLAIM_VERBS`` is a finite form, which is the point: a
    finite verb asserts something ("Employees turn to AI"), where a participle
    only modifies a noun ("Emerging markets"). That distinction is what lets a
    qualitative finding count as a claim without letting a dressed-up topic
    label in behind it.
    """
    words = [w.lower() for w in _WORD_RE.findall(title or "")]
    return any(word in CLAIM_VERBS for word in words)


def has_verb(title: str) -> bool:
    """
    Whether a headline contains something doing work.

    The curated list first, then a morphological fallback: a word ending in
    -ed or -ing, anywhere but the first position, is a verb in a headline in
    practice. Excluding the first position is what keeps "Advanced
    connectivity" and "Leading indicators" out.
    """
    words = [w.lower() for w in _WORD_RE.findall(title or "")]
    if has_finite_verb(title):
        return True
    return any(
        len(word) >= 5 and (word.endswith("ed") or word.endswith("ing"))
        for word in words[1:]
    )


def has_comparison(title: str) -> bool:
    words = [w.lower() for w in _WORD_RE.findall(title or "")]
    return any(word in COMPARATIVES for word in words)


def has_quantity(title: str) -> bool:
    return bool(_QUANTITY_RE.search(title or ""))


def states_a_claim(title: str) -> bool:
    """
    True when a headline states a finding rather than naming a topic.

    A finite verb is enough on its own: it makes the headline assert
    something, and at four words or more that assertion has a subject and a
    predicate. "Employees turn to AI for things that humans can't provide" is
    a finding with no number in it, and requiring one classified every
    qualitative assertion in a publication-grade deck as a bare topic label.

    The comparison-or-quantity test still guards the weaker evidence. A
    participle is not a verb doing work - "Advanced connectivity in emerging
    markets" is a noun phrase - so a headline resting only on the
    morphological fallback has to bring something to weigh as well.
    """
    words = _WORD_RE.findall(title or "")
    if not MIN_CLAIM_WORDS <= len(words) <= MAX_CLAIM_WORDS:
        return False
    if has_finite_verb(title):
        return True
    return has_verb(title) and (has_comparison(title) or has_quantity(title))


def is_question(title: str) -> bool:
    return str(title or "").rstrip().endswith("?")


def classify(title: str) -> str:
    """The register a headline is actually in, whatever it was labelled."""
    text = str(title or "").strip()
    if not text:
        return LABEL
    if is_question(text):
        return QUESTION
    return CLAIM if states_a_claim(text) else LABEL


def claim_problems(title: str) -> list[str]:
    """
    Why a headline declared as a claim is not one - in words its author can
    act on, rather than "invalid".
    """
    text = str(title or "").strip()
    if not text:
        return ["the title is empty"]
    if is_question(text):
        return ["it is a question, so its register is 'question', not 'claim'"]

    words = _WORD_RE.findall(text)
    problems: list[str] = []
    if len(words) < MIN_CLAIM_WORDS:
        problems.append(
            f"it is {len(words)} words - a topic label, not a finding "
            f"(a claim runs to at least {MIN_CLAIM_WORDS})"
        )
    if len(words) > MAX_CLAIM_WORDS:
        problems.append(
            f"it is {len(words)} words, which is a sentence rather than a "
            f"headline (keep it under {MAX_CLAIM_WORDS})"
        )
    if not has_verb(text):
        problems.append("it has no verb, so it names a topic rather than stating one")
    if not (has_comparison(text) or has_quantity(text)):
        problems.append(
            "it carries no figure and no comparison, so there is nothing in it "
            "a reader could disagree with"
        )
    return problems


# --------------------------------------------------------------------------
# Whether a slide can back a claim at all
# --------------------------------------------------------------------------

def evidence_on(slide_data: dict) -> list[str]:
    """
    What a slide has that a finding could be stated from.

    **The headline itself does not count.** A title carrying "12%" is not
    evidence that the slide has data; it is the assertion being checked, and
    letting it vouch for itself would make the guard vacuous - every
    confident-sounding headline would validate its own confidence.

    Returns the kinds of evidence present, so a caller can say *why* a slide
    cannot carry a claim rather than only that it cannot.
    """
    found: list[str] = []
    seen_title = False
    for item in (slide_data or {}).get("contents") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind in EVIDENCE_CONTENT_TYPES:
            found.append(kind)
            continue
        if kind == "text":
            if not seen_title:
                # The content designer emits the title first, so the first
                # text block is the headline.
                seen_title = True
                continue
            for run in item.get("runs") or []:
                if isinstance(run, dict) and has_quantity(str(run.get("text") or "")):
                    found.append("figures")
                    break
    return found


def supports_a_claim(slide_data: dict) -> bool:
    """
    Whether a slide has anything a claim could be made *from*.

    This is the guard the register needs. Told to write claims, a model will
    write confident claims from nothing; a headline asserting a finding on a
    slide carrying no figure, no chart and no comparison is the deck lying
    about its own evidence.
    """
    return bool(evidence_on(slide_data))


def register_for(slide_data: dict, declared: Any = None) -> str:
    """
    The register a slide should actually render in.

    A declared ``claim`` on a slide with nothing to point at is demoted to a
    label. Demoting is the safe direction: a topic label on a slide that had a
    finding is a missed opportunity, and a fabricated finding is a defect.
    """
    register = normalize_register(
        declared if declared is not None else slide_data.get("titleRegister"),
        default=LABEL,
    )
    if register == CLAIM and not supports_a_claim(slide_data):
        return LABEL
    return register


def slide_title(slide_data: dict) -> str:
    """A slide's headline: the first text block's runs, joined."""
    for item in (slide_data or {}).get("contents") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return " ".join(
                str(run.get("text", "")) for run in item.get("runs") or []
                if isinstance(run, dict)
            ).strip()
    return ""


def validate_title(slide_data: dict) -> tuple[list[str], list[str]]:
    """
    Check a slide's headline against the register it declared.

    Returns ``(errors, warnings)``. A claim that is really a label is an
    error, because the deck is claiming to state a finding and does not - and
    that is exactly the failure this register exists to catch.
    """
    declared = slide_data.get("titleRegister")
    if declared is None:
        return [], []

    register = normalize_register(declared, default="")
    if not register:
        return [], [
            f"unknown titleRegister {str(declared)!r} - expected one of "
            f"{', '.join(sorted(REGISTERS))}"
        ]

    title = slide_title(slide_data)
    errors: list[str] = []
    warnings: list[str] = []

    if register == CLAIM:
        if not supports_a_claim(slide_data):
            errors.append(
                "the title is declared a claim, but the slide carries no chart, "
                "table, figures or comparison to state a finding from - use the "
                "'label' register rather than asserting one"
            )
        problems = claim_problems(title)
        if problems:
            errors.append(
                f"the title {title[:60]!r} is declared a claim but " +
                "; ".join(problems)
            )
    elif register == QUESTION and title and not is_question(title):
        warnings.append(
            f"the title {title[:60]!r} is declared a question but does not ask one"
        )
    return errors, warnings


def registers_in(slides: Iterable[dict]) -> dict[str, int]:
    """How many slides sit in each register - used by the planner's own QA."""
    tally = {register: 0 for register in sorted(REGISTERS)}
    for slide in slides or []:
        if isinstance(slide, dict):
            tally[register_for(slide)] = tally.get(register_for(slide), 0) + 1
    return tally

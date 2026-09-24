# Claude Presentation Workflow: What Is Observable, and What It Means for ThinkSlides

Status: planning phase, iteration 1.
Last updated: 2026-09-24.
Sources are cited by their IDs in `docs/RESEARCH.md`.

## 1. Purpose and method

The goal is to understand why Claude's presentation output is strong, well enough to reproduce the engineering principles with our own implementation and cheaper models.
It is not to copy Anthropic's implementation.

Three surfaces were examined:

1. **The public `pptx` skill** (R-101): full source, instructions and git history, cloned and read.
2. **The Claude Slides artifact type** (R-102): its published `SKILL.md` (type release `1790097191-2423`).
3. **Claude for PowerPoint** (R-106): official capability documentation only.

Supporting context came from the Skills documentation (R-103), the `frontend-design` and `theme-factory` skills (R-104), and Anthropic's engineering writeups (R-201, R-202, R-203).

No hosted Claude generation was run for this document, and no claims below come from measuring Claude's output.
Every statement is labelled:

- **Confirmed**: stated in a primary source or visible in source code.
- **Strong inference**: follows directly from confirmed facts, with little room for another reading.
- **Weak inference**: plausible and consistent with the evidence, but other explanations exist.
- **Speculation**: not supported by public evidence; recorded only so it is not mistaken for fact later.

## 2. The headline finding

Anthropic ships at least two distinct presentation workflows, and they make opposite choices about verification.

| | `pptx` skill (R-101) | Slides type (R-102) |
| --- | --- | --- |
| Representation | a `pptxgenjs` program, or edited OOXML for templates | declarative per-slide HTML in a closed CSS subset, plus a `deck.json` index |
| Layout engine | none: the program states coordinates | flex and grid flow on a fixed 1920x1080 canvas |
| Fit strategy | render, look, fix | arithmetic before writing ("0.6 × font-size per character"), and "Split the slide; nothing shrinks" |
| Verification | mandatory structural validation plus visual QA of every slide | "NEVER VERIFY UNLESS THE USER ASKED" |
| Deck generation | iterative | "decide once ... then write every slide in one pass" |

**Strong inference:** the verification loop is needed in proportion to how much geometry the model controls.
When the model writes coordinates, it must look at the result.
When a layout engine owns geometry and the model only supplies structured content within known capacities, a single pass is usually good enough, and the product chooses speed.

This is the most important lesson for ThinkSlides, because ThinkSlides already made the right representational choice ("no model ever emits a coordinate", README) and then still spends most of its validation budget on geometry defects (R-001), which suggests the capacity model is not yet tight enough to make single-pass reliable.

## 3. The public `pptx` skill in detail

### 3.1 Structure (confirmed)

```
skills/pptx/
  SKILL.md                     238 lines: approach table, scripts, gotchas, design ideas, QA
  scripts/thumbnail.py         labelled grid of slides for template inspection
  scripts/add_slide.py         duplicate a slide or layout with package bookkeeping
  scripts/clean.py             remove orphaned slides, media and relationships
  scripts/office/validate.py   schema, relationships, content types, charts, slide checks
  scripts/office/soffice.py    LibreOffice wrapper with a per-invocation profile
  scripts/office/validators/   base, pptx, docx, redlining validators
  scripts/office/helpers/      pptx_chart, pptx_slide, pptx_theme helpers
  scripts/office/schemas/      ISO/IEC 29500 and Microsoft XSDs
```

About 4,200 lines excluding schemas, of which about 1,300 are validators.
**Strong inference:** most of the engineering effort in the skill went into deterministic checking, not into instructions.

### 3.2 Workflow (confirmed from `SKILL.md`)

1. Choose the approach by task: create (write a `pptxgenjs` script), edit or template (unzip, edit slide XML, rezip), read (`markitdown`, `thumbnail.py`).
2. For templates, thumbnail the template and map each content section to a template slide, varying layouts ("don't put every section on the same title-and-bullets slide").
3. Do structural work (add, delete, reorder) before content edits.
4. Build the deck.
5. File QA: `validate.py`, with `--original` for template-derived decks so inherited errors are baselined away.
6. Content QA: `markitdown` the output and grep for leftover placeholder text.
7. Visual QA: LibreOffice to PDF, `pdftoppm` to JPEG at 150 dpi, inspect every slide "fresh", ideally with a subagent.
8. Fix, "re-render only the slides you changed, and stop".

### 3.3 How it evolved (confirmed from git history)

| Date | Change | What it tells us |
| --- | --- | --- |
| 2025-12 | first public version: HTML per slide, `html2pptx.js`, `inventory.py`, `replace.py` for templates | started from a declarative, browser-laid-out representation |
| 2026-02 | replaced `html2pptx` with direct `pptxgenjs`; added editing and pptxgenjs guides; shared OOXML tooling | moved to direct programmatic generation once the model knew the library well |
| 2026-07 | trimmed docs "to the guidance that earns its place"; safe-font list for QA reliability; chart-corruption gotchas; validator hardening; "never accent lines under titles"; "don't default to cream" | knowledge accumulates as **failure-mode lists**, learned from observed defects |

**Weak inference:** the gotchas list reflects real defects encountered at scale, since each item names a specific corruption or rendering failure and its fix.

### 3.4 Design guidance (confirmed)

The skill encodes design as short, checkable heuristics rather than taste:

- A content-specific palette with one dominant colour (60 to 70% visual weight), one or two supports and one accent.
- A dark/light "sandwich": dark title and conclusion, light content slides.
- One repeated visual motif per deck.
- "Every slide needs a visual element".
- A size table: titles 36 to 44 pt bold, section headers 20 to 24 pt, body 14 to 16 pt, captions 10 to 12 pt.
- Margins of at least 0.5 inch; 0.3 or 0.5 inch gaps used consistently.
- An explicit avoid list: repeated layouts, centred body text, weak size contrast, default blue, accent lines under titles, decorative edge stripes, cream backgrounds, text-only slides, low contrast, overflow.
- Fonts chosen from a list whose LibreOffice substitutes are metric-compatible, so QA previews are trustworthy.

### 3.5 Charts (confirmed)

"Keep charts native."
Images only for chart types PowerPoint cannot express (Sankey, network, chord).
Default charts must be dressed: title, data labels, palette colours, quiet gridlines, no legend for a single series.

## 4. The Claude Slides type in detail

### 4.1 Representation (confirmed)

- `project/deck.json`: title, slide `order`, `sections` (each one sentence plus its first slide, which is the outline), font `faces`, installed `designSystems`.
- `project/slides/<id>.html`: exactly one `<section>` per file, inline styles only from a closed subset (px lengths, hex colours, flex or grid, no classes, no margins, no `z-index`), at most 200 elements, speaker notes in a final `<aside>`.
- Special elements: `<x-shape>`, `<x-icon>`, `<x-connector>`, `<svg>` up to 52 KB, `<table>`, pinned `<x-embed>`.
- Design systems are installed as `tokens.json` plus font files, so theme and text-style menus operate on named tokens rather than hexes.

### 4.2 Workflow (confirmed)

1. Resolve the design system first (a default one wins; otherwise ask, or use your own look).
2. Decide once: outline, length, audience, repeated layouts, typefaces and palette.
3. For a thin brief, read `questions.md` (the content of which we could not read).
4. State assumptions in one line.
5. Write every slide in one pass; missing figures become bracketed placeholders listed in the reply.
6. Publish all files in one call.
7. Do not verify unless asked.

### 4.3 Design checklist (confirmed)

- "You are a presentation designer, not a web designer."
- One dark, one light, one or two accents; toned whites and blacks; 4.5:1 contrast (3:1 at 44 px and above).
- One type scale of four or five sizes and one to three typefaces; "emphasize with weight, italic or color, not a new size".
- "One idea per slide"; "turn lists into tables, card rows, big numbers or quotes"; titles "in one grammar throughout".
- Vertical budget of 824 px inside margins, with explicit estimates for headings, table rows, cards and character widths.
- A footer band as one pinned 24 px row.
- Rhythm: "slides of one kind share markup; repeated elements keep their places".
- Images are the user's files only.

### 4.4 What we could not see

The reference pages (`craft.md`, `layout.md`, `styles.md`, `diagrams.md`, `diagram-recipes.md`, `questions.md`, `format.md`, `fonts.md`, `images.md`) exist but require creating a deck to read.
Their names alone are a **weak inference** that diagram construction and clarifying questions are treated as separate, recipe-driven concerns.

## 5. Claude for PowerPoint (confirmed capabilities, unknown internals)

- Reads the slide master, layouts, fonts and colour scheme and generates within them.
- Edits a selected slide without regenerating the deck.
- Converts bullets into native charts and diagrams, "not static images".
- Pulls context through connectors and applies Skills.
- Persistent per-app instructions for brand rules.
- Documented as not suitable for "final client deliverables without human review" or "replacing your judgment on design and narrative flow".

**Speculation:** it likely uses the same OOXML knowledge as the `pptx` skill through the Office.js API rather than raw XML; no public evidence either way.

## 6. Stage-by-stage analysis

| # | Stage | Observable in Claude workflows | Label |
| --- | --- | --- | --- |
| 1 | Input understanding | Slides: decide audience and length once; thin brief triggers `questions.md` | Confirmed |
| 2 | Content/source selection | Slides: "Images are the user's files only"; no figure without a source | Confirmed |
| 3 | Research | Neither skill does research itself; research happens in the surrounding Claude conversation (web search, connectors) | Strong inference |
| 4 | Source extraction | `pptx`: `markitdown` for decks; `pdf`/`docx` skills for other inputs | Confirmed |
| 5 | Narrative planning | Slides: outline as one sentence per section | Confirmed |
| 6 | Presentation structure | Slides: `order`, `sections`, `cover` in `deck.json` | Confirmed |
| 7 | Slide decomposition | Slides: "One idea per slide"; split rather than shrink | Confirmed |
| 8 | Slide purpose | Implicit in "one idea per slide" and "titles introduce the topic, in one grammar" | Strong inference |
| 9 | Design system selection | Slides: design system first, tokens installed; `pptx`: content-informed palette chosen before building | Confirmed |
| 10 | Layout selection | `pptx`: map sections to template slides and vary layouts; Slides: "layouts to repeat" decided once | Confirmed |
| 11 | Visual asset selection | Slides: user files only; `pptx`: icons from `react-icons` rasterised | Confirmed |
| 12 | Chart/diagram planning | `pptx`: native charts, dressed; Slides: `diagrams.md` and `diagram-recipes.md` exist | Confirmed (existence); content unknown |
| 13 | Programmatic generation | `pptx`: pptxgenjs program; Slides: declarative HTML | Confirmed |
| 14 | Rendering | `pptx`: LibreOffice to PDF to JPEG; Slides: the runtime renders | Confirmed |
| 15 | Structural validation | `pptx`: `validate.py` (XSD, relationships, content types, chart faults) | Confirmed |
| 16 | Visual inspection | `pptx`: every slide, "fresh", optionally by a subagent; Slides: none by default | Confirmed |
| 17 | Critique | `pptx`: a defect checklist (overflow first); `frontend-design`: "plan, review against the brief, build, critique" | Confirmed |
| 18 | Revision | `pptx`: fix and re-render only changed slides; Slides: revise only the named slides | Confirmed |
| 19 | Re-rendering | `pptx`: rerun conversion after fixes | Confirmed |
| 20 | Final validation | `pptx`: validator plus placeholder grep | Confirmed |
| 21 | Export | Slides: PowerPoint or PDF export from the runtime; `pptx`: the file itself | Confirmed |

Stages that do not exist as separate steps in either workflow: a separate "slide purpose" artefact, an automated quality score, and a bounded multi-round critique loop.
The `pptx` loop ends at "stop" after one fix round by instruction, not by a convergence metric.

## 7. Behaviour table

| Claude/skill behaviour | Evidence | Confidence | Why it matters | Equivalent in our system | Gap |
| --- | --- | --- | --- | --- | --- |
| Decide outline, audience, length and repeated layouts once, then write all slides in one pass | R-102 | High | Removes per-slide re-planning and makes slides consistent | Plan once (`plan_presentation_structure`), then a separate ReAct episode per slide | Drafting re-derives context per slide in up to 6 turns with an 8.5k-token prompt |
| Model never controls geometry in the fast path; a flow layout engine does | R-102 | High | Eliminates overlap and most overflow by construction | Pack archetypes and `design/select.py` cost-based binding | Still produced overflow and overlap in 3 of 5 runs (R-001) |
| Fit arithmetic before writing; split, never shrink | R-102 | High | Prevents the defect class our validation loop fails to fix | Capacity per slot exists in authored packs; `text_fit.py`; density budget | Capacity is not enforced as a hard constraint at compile time |
| Structural validation of the package every time | R-101 | High | Catches files PowerPoint refuses but LibreOffice opens | `validate_rendered_output` checks package integrity, LibreOffice geometry | No XSD or relationship validator; LibreOffice is treated as ground truth |
| Visual QA of every slide, fresh eyes, then one targeted fix round | R-101 | High | Catches what code cannot | Per-slide vision pass up to 12 slides, up to 3 rounds | The vision prompt excludes chart semantics and content; rounds did not converge |
| Native, dressed charts | R-101, R-106 | High | Editability is a core enterprise expectation | matplotlib PNG charts | Charts are not editable; chart semantics are not checked |
| Named anti-default list (accent rules, cream, edge stripes, repeated layouts) | R-101, R-104 | High | Cheap, deterministic, and aimed at the look of generated decks | Some layout-variety checks in `output_qa.py` | Our output shows accent rules under titles and cream-plus-serif (R-002) |
| QA-safe fonts with metric-compatible substitutes | R-101 | High | Makes LibreOffice-based fit checks trustworthy | Fonts chosen by authored languages | Unknown whether authored fonts are metric-safe in the renderer container |
| Missing figures become visible placeholders, never invented values | R-102 | High | Hallucinated numbers are the worst failure for trust | Prompt rules against invented figures; `grounding/check.py` reports unsupported figures | Detection only; charts with fabricated or zero-filled data still ship (R-002) |
| Design system tokens as installed data | R-102, R-104 | High | Consistency and re-theming | `DesignSystemBundle`, authored `Language`, packs | Comparable; ours is richer |
| Progressive disclosure of knowledge | R-103 | High | Keeps context small and relevant | One monolithic system prompt with every rule for every phase | No per-phase or per-format knowledge loading |
| Revise only named slides | R-101, R-102 | High | Revisions are cheap and do not disturb approved slides | `slides_needing_revision` walks back to drafting per slide | Comparable in intent; each revision re-renders and re-validates the whole deck |
| Template inspection by thumbnail grid plus text extraction | R-101 | High | Maps content to real template layouts visually | XML parsing into `DesignSystemBundle`; vision for PDF templates | No visual inventory of template layouts |
| Subagent for visual QA | R-101 ("a subagent works well") | Medium | Independent critique avoids confirmation bias | Vision call in validation is separate from the drafter | Comparable in spirit |

## 8. Model intelligence versus system capability

Attributing Claude's quality is the key question for a cheap-model strategy.
This is inference, since no controlled experiment was run.

| Quality driver | Mostly model | Mostly system | Reasoning |
| --- | --- | --- | --- |
| Narrative, argument, choice of what to say | yes | | Neither skill encodes narrative craft beyond "one idea per slide" and the outline; it relies on the model |
| Title quality (assertions versus topics) | yes | partly | Slides asks for "one grammar"; no check enforces assertions |
| Layout correctness (no overlap, no overflow) | | yes | Flow layout plus fit arithmetic (Slides) or render-inspect-fix (`pptx`) |
| Visual consistency | | yes | Tokens, one type scale, repeated markup |
| Avoiding generated-design tells | | yes | Explicit avoid lists |
| Chart correctness and dressing | partly | yes | Native charts plus a dressing checklist; the model chooses what to chart |
| File validity | | yes | Validators with fix instructions |
| Grounded figures | partly | yes | Placeholder rule plus the conversation's research tools |
| Research depth | yes | partly | The surrounding conversation and tools, not the skill |

**Strong inference:** the parts of Claude's quality that are hardest to buy cheaply are narrative judgement and deciding what each slide should argue.
The parts that look most like "Claude makes good slides" to a casual viewer (clean layout, consistency, no overflow, no generated-design tells) come mostly from deterministic structure and explicit rules, which cheap models can follow if the system enforces them.

This supports routing narrative planning to a stronger model once per deck, and slide compilation to a cheap model under hard constraints.

## 9. Once per deck, once per slide, parallelism, model strength

| Work | Frequency | Parallelisable | Model strength needed | Basis |
| --- | --- | --- | --- | --- |
| Clarify brief, audience, format | once per deck | no | medium | Section 8 |
| Research queries and reading | once per deck, many calls | yes, fully | cheap for query generation and extraction; medium for synthesis | R-203 |
| Storyline and slide intents (action titles, exhibit per slide) | once per deck | no | strong | Section 8, R-401 ablation |
| Design system | once per deck | the three directions, yes | medium; deterministic gates | existing `design/authoring.py` |
| Slide compilation into typed content | once per slide | yes, after the storyline | cheap, under schema and capacity constraints | R-102, R-402 |
| Layout binding | once per slide | yes | deterministic | existing `design/select.py` |
| Render | once per deck, plus changed slides | per slide, yes | none | R-101 |
| Structural validation | once per render | yes | none | R-101 |
| Visual critique | once per changed slide | yes | vision-capable, cheap to medium | R-101 |
| Targeted revision | per flagged slide | yes | cheap, escalate on repeat failure | R-101, R-407 |

## 10. Comparison table

| Capability | Claude/public skill evidence | Current system | Desired system |
| --- | --- | --- | --- |
| Deck representation | Declarative HTML subset (Slides) or pptxgenjs program (`pptx`) | Typed slide JSON plus pack archetypes, rendered through python-pptx and XML cloning | Keep typed slide spec and archetypes; make capacity a hard compile-time constraint |
| Outline | One sentence per section, decided once | `plan_presentation_structure` once; reviewed with user | Storyline artefact with an action title, purpose, evidence references and exhibit spec per slide; validated deterministically (count, coverage) |
| Drafting | All slides in one pass | Per-slide ReAct episode, sequential, up to 6 turns each | One structured call per slide, run in parallel, with a shared cached prefix |
| Layout | Flow engine plus fit arithmetic | Cost-based archetype binding plus monotone repair | Same engine, fed by measured capacities; split pages that do not fit before render |
| Charts | Native, dressed, image only when no native form | matplotlib PNG | Native where fidelity allows (to be benchmarked), with chart semantic lint either way |
| Diagrams | Recipes (content unknown) | `build_diagram`, Mermaid, SVG, primitives | A diagram grammar with typed node and edge specs (section 11 of the plan) |
| Structural validation | XSD, relationships, content types, chart faults | Package integrity inside `validate_rendered_output` | Dedicated validator with fix messages, run before any visual check |
| Visual QA | Every slide, fresh eyes, one fix round | Vision per slide up to 12, geometry, up to 3 rounds | Deterministic checks first; vision on changed slides only; semantic chart and title checks; stop when no improvement |
| Anti-default lint | Explicit list | Partial (`output_qa.py` variety checks) | Deterministic deck lint encoding the lists in R-101 and R-104 |
| Missing data | Visible placeholders | Prompt rules; unsupported figures reported after the fact | Evidence-bound values only; unsupported figures blocked from charts and flagged in copy |
| Knowledge packaging | Skills with progressive disclosure | One monolithic system prompt | Per-format presentation skills loaded per phase |
| Research | Surrounding conversation tools | Native provider search, 320-character snippets | Parallel search, page fetch, chunked evidence store with span-level attribution |
| Revision scope | Only changed slides | Per slide, but whole deck re-rendered and re-validated | Per slide end to end |
| Templates | Thumbnail grid plus text inventory | XML-derived bundle | Add a visual layout inventory for mapping content to template layouts |
| Editing after delivery | Slide-scoped edits (PowerPoint add-in) | Chat revision triggers the pipeline | Slide-scoped edit path as a first-class operation (later iteration) |

## 11. Implications for our architecture

1. **Keep the representation, tighten the constraint.**
   ThinkSlides' choice that no model emits geometry is the same bet as the Slides type and SeaSlides (R-406).
   The failure is that capacity is advisory: overflow is detected after rendering and sent back to a model that rewrites prose, which did not converge in R-001.
   Capacity should be checked at compile time from font metrics, and a page that does not fit should be split or re-bound before rendering.

2. **Compile slides, do not converse them.**
   After the storyline exists, each slide is a pure function of (storyline entry, evidence slice, design pack).
   That is one structured-output call per slide, run in parallel, not a tool-choosing conversation.

3. **Put the intelligence where the model matters.**
   A stronger model writes the storyline once; cheap models compile slides; deterministic code binds layout, checks fit, lints and validates.

4. **Encode design knowledge as data and checks.**
   Anti-default lists, type scales, chart dressing rules and diagram grammar belong in versioned files and deterministic linters, loaded per format, not in a 34,000-character system prompt.

5. **Make the critic look for what actually goes wrong.**
   Our vision pass was told to ignore content and chart meaning; the worst defects in R-002 were semantic (incommensurable bars, zero-filled categories, topic titles).
   Most of those are detectable without vision, from the typed chart spec.

6. **One fix round, targeted, measured.**
   Match the `pptx` skill: fix the specific slides, re-render only those, and stop unless a measurable blocking defect remains.

## 12. Gaps in the current system, ranked by expected impact

The ranking is argued in `PROJECT_PLAN.md` section 5; this is the Claude-comparison view of it.

1. Semantic chart and content defects ship because nothing checks chart meaning (R-002).
2. Geometry defects are detected late and fixed by redrafting prose, which does not converge (R-001).
3. Drafting is sequential and conversational, which dominates runtime after search (R-001).
4. Research evidence is too thin (short snippets from an LLM's own answer text) to ground specific figures (R-003).
5. Generated-design tells are present (R-002 against R-101 and R-104).
6. Knowledge is not packaged or loaded per phase, which inflates every call (R-003, R-103).
7. Charts are images, not editable (R-101, R-106).

## 13. How we will compare against Claude

The benchmark in `PROJECT_PLAN.md` section 12 includes Claude workflows as comparators where practical:

- **`pptx` skill through the API** (code execution plus `skill_id: "pptx"`): reproducible and scriptable; requires an Anthropic API key and credits, which we do not have configured, so this stops for user approval.
- **Claude Slides and Claude for PowerPoint**: interactive products; usable only for a small manual comparison set, subject to terms of service.
- **Our pipeline driven by Claude models**: separates model capability from system capability by holding our system fixed and swapping the model.

The attribution design is a 2x2: {our system, Claude workflow} x {cheap model, frontier model}, where the Claude workflow's model axis is only available for the API skill.
Differences along the model axis with the system fixed are model capability; differences along the system axis with the model fixed are system capability.

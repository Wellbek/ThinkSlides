# How ThinkSlides decides what goes where

This is the reference for every automated decision the design engine makes between "the model has written a slide" and "a page is rendered".
It states each rule, the parameters it uses with their current values, why it exists, and the run or issue that motivated it, so it can be cited in a paper or in user documentation.

It is a living document.
Every change to a decision rule, weight, threshold or fallback updates it in the same pull request.
Values are quoted from the code as of the date below; the code is authoritative when the two disagree, and a disagreement is a bug in this document.

Last updated: 2026-09-25.

The flow chart in [`diagrams/design-selection.svg`](diagrams/design-selection.svg) shows the same process at a glance.

## Contents

1. [Principles](#1-principles)
2. [Stage I: one design language for the deck](#2-stage-i-one-design-language-for-the-deck)
3. [Compiling a language into page archetypes](#3-compiling-a-language-into-page-archetypes)
4. [The gate: what a language must do before it is offered](#4-the-gate-what-a-language-must-do-before-it-is-offered)
5. [From a slide to material](#5-from-a-slide-to-material)
6. [Stage II: choosing each page's archetype](#6-stage-ii-choosing-each-pages-archetype)
7. [Giving an exhibit a space it can be read in](#7-giving-an-exhibit-a-space-it-can-be-read-in)
8. [Splitting a page](#8-splitting-a-page)
9. [The fallback chain](#9-the-fallback-chain)
10. [Binding material into frames](#10-binding-material-into-frames)
11. [Laying out a table](#11-laying-out-a-table)
12. [Repair: fixing a composed page in code](#12-repair-fixing-a-composed-page-in-code)
13. [Validation and the revision ladder](#13-validation-and-the-revision-ladder)
14. [What drafting is told](#14-what-drafting-is-told)
15. [How a run is checked afterwards](#15-how-a-run-is-checked-afterwards)
16. [Parameter reference](#16-parameter-reference)
17. [Evidence log](#17-evidence-log)

## 1. Principles

Four principles run through every rule below.

- **No model emits a coordinate.**
  The model writes what a page says and which exhibits it needs.
  Where anything goes is decided in code, from measurements, and can be audited when a page comes out wrong.
- **Measure, do not estimate.**
  Text is measured with the real font's advance widths at the real measure, not with a characters-per-line constant.
  The character-count estimate this replaced got 49.2% of line counts right against 128 rendered blocks; the font measurement gets all 128 (`text_fit.estimate_wrapped_lines`).
- **Losing content is never an option.**
  An arrangement that would drop material is infeasible, not merely expensive; a page too full for any arrangement is split, never truncated.
- **Degrade one page, never the deck.**
  Each fallback affects the smallest unit it can: a slot, then a page, and only as a last resort the renderer for the whole deck.

## 2. Stage I: one design language for the deck

A deck is composed against one *pack*: a set of page archetypes, a grid, a type ramp and colour roles.
The pack is chosen once, before any copy is written, because it governs how every page is composed (`app/agent/loop.py`, `_resolve_pack`).

The pack comes from one of three places, in this order.

1. **An uploaded template.**
   The customer was promised their own template, so the template-faithful XML-clone renderer is used and no pack is installed.
2. **An authored design language the user chose.**
   The run asks a model for three candidate languages for this brief (`app/design/author_run.py`), gates each one (section 4), shows the user a rendered preview of each, and installs the chosen one.
3. **A shipped pack.**
   When there is no authored language, the shipped pack that carries the planned deck at the lowest total selection cost is used (`run.for_deck`).
   It is measured with the same `choose` a page uses, so "which house style fits this material" is answered by the fit function rather than by a guess about the topic.
   A pack that cannot carry a planned page is charged 10.0 for it rather than disqualified, since the page can be split.

The authoring prompt is retried once (`MAX_ATTEMPTS = 2`) with the specific reasons the first set failed.
A model told "try again" returns the same set; told what is wrong, it changes it.
A set is also rejected when its languages differ only superficially (`authoring.differs_enough`).

## 3. Compiling a language into page archetypes

An authored language names page kinds and, for each, an ordered list of slots with a kind (`heading`, `standfirst`, `prose`, `figure`, `statistic`, `quote`, `caption`, `footnote`) and a column span.
It never states geometry.
`authored.to_pack` compiles it into archetypes (`app/design/authored.py`).

**Arrangements.**
Each page kind becomes up to three archetypes: the kind as declared, a *stacked* variant (every slot full width) and a *paired* variant (the flowing slots, figures and prose, at half width, side by side).
Identical variants are deduplicated.
Offering several arrangements of one kind is what lets the selector respond to the material: a portrait chart and a letterbox chart can get different pages.

**Rows.**
Consecutive slots whose spans fit the grid width share a row (`_rows`), so `heading 6 / figure 3 / prose 3` becomes a headline over a figure and its commentary side by side.

**Heights.**
Each slot has a natural height from the type it is set in (`_natural`): a heading two lines, a standfirst three, prose six (scaled by density: airy 0.7, balanced 1.0, dense 1.45), a statistic two, a quote three, a caption or footnote two.
A figure's natural height is 0.34 of the content box.
A row is as tall as its tallest slot.

**Filling the page (`_fill`).**
The natural heights rarely add up to the page.
- When there is room to spare, it is given to the rows that flow (prose and figures) in proportion to their natural height; a page with neither keeps its white space, as a section divider should.
- When the page asks for more than it has, **prose gives room back first**, down to 35% of its natural height (`PROSE_SHRINK_FLOOR`), and figures keep theirs.
  Only if that is not enough does everything shrink in proportion.
  Shrinking everything in proportion from the start took a stacked page's figure band from a third of the page to a fifth of an inch, and every table set in it came out as a sliver (#351, #355).
  Prose that loses room is measured and split later (section 8); an exhibit that loses room is simply drawn smaller, which is why it is protected.

**Capacities.**
Each text slot's `max_chars` is measured from its face, size and final geometry (`_capacity`), so the selector's overflow check reads the page rather than a string length.

**Openers.**
A pack with no cover, section or closing page gets one composed from its own grid, ramp and colours (`app/design/vocabulary.py`, `ensure`).
These are laid out upward from a baseline (cover 0.93, section 0.62 of the page) so each block is exactly as tall as its own type.
They carry `frequency = 0` and a note saying they were composed, so nothing downstream mistakes them for measured pages.

## 4. The gate: what a language must do before it is offered

A language is offered to the user only if it passes every check below (`author_run._problem`, `compile_problem`).
Each refusal names what to change, and that sentence is put into the retry prompt.

1. **It derives.**
   `to_pack` produces archetypes and type roles without raising.
2. **It has somewhere to put an exhibit.**
   At least one content page declares a figure slot.
   A language without one failed at the first chart with a message about paragraph counts.
3. **Every arrangement is sound geometry.**
   Each archetype's bare frames pass the same geometry check the renderer runs.
   A cover whose frames overlapped at equal depth was refused at render and shipped nothing (#306).
4. **An exhibit can be read somewhere.**
   Some content arrangement must show a probe table (a header and five rows of four short columns, the median comparison table in the benchmark runs) and a probe chart legibly, by the rule in section 7.
   The refusal quotes the measured numbers, for example "its tallest figure slot is 1.0in by 7.7in, and a five-row table needs about 1.7in of height at that width".
5. **A deck of every page kind composes on the language's own pages.**
   A probe deck (cover, section, prose page, table page, chart page, closing) must compose and pass the geometry check without any page needing the plain fallback of section 9.

Against the 17 languages authored in the recorded benchmark runs, the gate refuses 2, both for check 4.

## 5. From a slide to material

A drafted slide is read into a *material shape* (`run.material_from`, `material.shape_from`): an ordered list of items, each with a kind.

- The first single-line text block is the **heading**.
- Further text is **body** paragraphs.
- `chart`, `table`, `diagram` and `metrics` / `statCallout` blocks are **exhibits**; an exhibit whose figure declares an aspect ratio (an SVG's view box) carries it, so shape can be matched before placement.
- `image` blocks are **figures**.
- Block types with their own payloads (accent cards, row bands, icon cards, banners, image-card rows) contribute their copy as body text rather than being dropped.

The page's **role** is what the slide declares, or else: first slide cover, last slide closing, everything else content.
A pull quote is offered only when the body has enough copy for a highlight to be a highlight.

## 6. Stage II: choosing each page's archetype

Every archetype in the pack is fitted to the page's material, and the cheapest feasible one wins (`select.choose`, `select.fit`).
Ties break toward the archetype the source publication used more often.

### 6.1 Feasibility

An archetype is not an option at all when:
- its role does not match a cover, section or closing page (a content page may use a content or exhibit archetype, never an appendix page);
- any required slot is left empty;
- **any item is left unplaced.**
  A page that silently dropped five of its seven paragraphs used to score better than one that placed them all, because dropped material costs nothing to measure.

### 6.2 The assignment

Items are assigned to slots as a whole, not greedily, by solving the rectangular assignment problem exactly (`app/design/assign.py`, Jonker-Volgenant shortest augmenting path, O(n^3)).
Binding the biggest item to the biggest slot first put the chart that needed the wide slot in the tall one because it was considered first.

Body paragraphs are grouped into as many runs as the archetype has prose slots (`_grouped`), because a prose column holds a column of prose, not one paragraph.

### 6.3 The cost of one item in one slot (`pair_cost`)

A pair is infeasible when the slot does not accept the item's kind (a `figure` slot takes any exhibit or image), when the frame's role forbids it (a body paragraph in a headline frame), or when an exhibit falls too far short of its legibility floor (section 7).
Otherwise its cost is the sum of:

| Term | Weight | What it measures |
| --- | --- | --- |
| capacity strain | 2.0 | How badly text over- or under-fills the slot, measured by typesetting it. Overrun costs `min(4, 4(f-1)^2 + (f-1))` at fill `f > 1`; underfill below 0.72 costs `(0.72 - f)^2`. Two-sided on purpose: penalising only overrun makes the roomiest archetype always safest, and a deck of half-empty pages is what the previous system produced. |
| aspect | 6.0 | How far the slot's shape is from the exhibit's declared shape, `min(1, abs(ln(a_item / a_slot)) / ln 4)`. 6.0 is the smallest weight at which the arrangement tracks the exhibit; the shipped packs' choices do not change at any weight from 1 to 12. |
| legibility | 12.0 | How far short of readable the slot leaves an exhibit (section 7). Weighted like overflow, doubled because pair costs are averaged over a page. |
| prominence | 0.4 | Mismatch between the item's importance and the slot's prominence. |
| frame hint | -2.0 | A bonus when the material is the kind the frame is for (a heading in a headline frame). A heading set as a body paragraph is penalised. |
| sequence | 2.2 | Preference for the k-th paragraph to land in the k-th text slot in reading order, so a page reads as a page rather than three fragments. |

A required slot pulls material into itself with a bonus of 5.0, because the assignment has no concept of "this column must be used".

### 6.4 The cost of an archetype

| Term | Weight | What it measures |
| --- | --- | --- |
| slot mismatch | 3.0 | Share of optional slots left empty: an archetype used at a fraction of its shape. |
| capacity | 1 | The mean pair cost over placed items. |
| overflow | 6.0 | Copy that runs past its frame. Not one cost among several: a page whose copy runs off the bottom is broken, and averaging its strain in let an archetype win on its other slots while one was unreadable. |
| density | 2.5 | How far the page's volume of copy is from what the archetype held on the page it was measured from, in characters. |
| rhythm | 0.8 | Using the same archetype as the previous page. A deck that picks its single best fit every time is a deck of identical pages. |

## 7. Giving an exhibit a space it can be read in

This is the rule that decides whether a table, chart or diagram may go in a slot (`select.exhibit_floor_in`, `legibility_shortfall`, `readable_somewhere`).

**The floor.**
Each exhibit has a minimum slot height:
- a **table** is measured, not estimated: its uncompressed height at the slot's width, wrapped as it will be drawn, at 10pt body type (`table_layout.needed_height_in`, `LEGIBLE_CELL_PT`).
  It is measured in both the default face and the pack's body face, and the taller is kept, because the table is drawn in the design's data-visualisation face, which the pack does not carry.
  A row count alone said nothing about a six-column table whose cells each break onto three lines in a half-width column;
- a **chart** needs 1.6in, a **diagram** 1.0in, a **metrics** row 0.6in (`MIN_EXHIBIT_IN`).

**The shortfall** is `max(0, floor - height) / floor`, from 0 (fits) to 1.

**The decision.**
- A shortfall above 0.25 (`LEGIBLE_TOLERANCE`) makes the slot **infeasible**.
  As a cost, it was outbid by the text overflow of every other arrangement, and a five-row table was set in a slot 0.2in tall with its rows printed over each other (#351).
- A shortfall within tolerance is charged at weight 12.0, so a slot that fits wins over one that nearly does.
- If no content arrangement in the pack has a slot the exhibit could be read in (`readable_somewhere` is false), splitting the prose around it cannot help, because legibility depends on the slot and not on the prose.
  The page is then placed cramped straight away, with the floor relaxed and a `exhibit_placed_below_legible_size` warning, rather than split pointlessly (section 8).

**Margins.**
A slot that bleeds off the page keeps its bleed for a photograph and loses it for an exhibit (`bind._fill`).
A table, chart or diagram carries text that has to sit inside the margins the rest of the page keeps; a language whose figure slot bled set every exhibit hard against the page edge.

## 8. Splitting a page

When no archetype can carry a page's material, the page is split and each part placed in turn (`run._place`, `_split`).

- **What is divided.**
  The body paragraphs are halved, and so are the page's *visuals* (exhibits and images together, since they compete for the same figure slots).
  A page with one paragraph and two visuals still divides, at the visuals, with the paragraph beside the first.
  Dividing only prose left both exhibits on the first part forever: every arrangement in most packs has one exhibit slot, so a slide with a diagram and a table could never be placed (#351).
- **The heading is repeated** on every continuation.
  Dropping it left the continuation with nothing for a required headline slot, so a page that had to be split could not be placed at all; repeating it is also what a publication does.
- **Bounded.**
  A page is split at most `max(4, number of non-figure items)` times, and a part with fewer than two paragraphs and fewer than two visuals is not divisible.
- **When not to split.**
  A page whose exhibit no slot could show legibly is not split for that (section 7).

Splitting adds pages the plan did not count, so drafting is told the design's exhibit capacity up front (section 14).

## 9. The fallback chain

Each step is tried only when the one before it fails, and each affects the smallest unit it can.

1. **The cheapest feasible archetype** for the page (section 6).
2. **A split** of the page into parts that each fit (section 8).
3. **A cramped placement**, with the legibility floor relaxed, when an exhibit cannot be shown legibly anywhere or nothing is left to divide (section 7).
4. **The page's previous arrangement**, when the failure came from a `replace` verdict's avoid list (section 13).
   The arrangement it was on held it a render ago, and placing it as before beats downgrading it because a revision was asked for.
5. **The pack's plain page**, for that page only (`vocabulary.plain_content`, #322).
   A headline, one full-width column and a full-width figure frame, composed from the pack's own grid, type and colours.
   It is never offered alongside the pack's archetypes, because it would win on capacity and flatten every deck; it is used only after all of them have refused a page.
   Every other page keeps its arrangement.
6. **The stock renderer for the whole deck**, only if even the plain page refuses a page.
   Before #322 this was step 2's failure mode, and one unplaceable slide put a six-slide technical deck on stock placeholders (#351).

Previews are different: a specimen page no archetype can carry is skipped, since three preview cards showing two pages each are still a comparison a user can make.

Every step past the first is logged, and steps 5 and 6 are recorded on the render's trace span and named by the run checks (section 15).

## 10. Binding material into frames

Placed items are copied into their archetype's frames (`app/design/bind.py`).
A frame nothing was bound to is dropped, unless it carries a ground or scrim, and the rows below it close up (`_compact`), so a figure slot on a page with no figure does not leave a third of the page white.

## 11. Laying out a table

A table is laid out from its content, not split evenly (`app/renderer/table_layout.py`, `plan_table`), and on the composed path drawn as an image that follows that plan exactly (`chart_renderer.render_table_to_png`).

- **Face.**
  The table is planned in the face it is drawn in, bold for the header and total rows.
  Measuring in one face and drawing in another is how planned rows came out too short for the lines drawn in them.
  The image is drawn from the font file fontconfig resolves for the face, the same file LibreOffice uses; asked by name, matplotlib silently substituted its own, far wider DejaVu Sans for Arial.
- **Columns.**
  Widths are proportional to each column's longest cell, with a floor of 8% of the table, and **no column narrower than its longest unbroken word** (a point larger and bold in the header).
  Proportional widths alone squeezed a column of one-word labels beside a column of sentences below its own words, and "Strategy" was set as "Stra / tegy".
- **Size.**
  Body sizes 14, 13, 12, 11, 10, 9 and 8pt are tried in turn, largest first; a size at which some word cannot be set whole is skipped.
  The first size whose rows fit the band is used.
- **Rows.**
  Each row is as tall as its tallest wrapped cell, and the image draws each row at that height and each cell on the lines the plan measured.
  Drawing every row at an equal share and every cell on one line made cells run into their neighbours.
- **Numbers.**
  A column of figures (ignoring blanks and placeholders such as "-" and "n/a") is right-aligned; a last row labelled Total, Sum or similar gets the total treatment.
- **Last resort.**
  If the table is still too tall at 8pt, its rows are compressed proportionally.
  Section 7's floor exists so that a layout never asks for this.

## 12. Repair: fixing a composed page in code

After composition, each page is repaired in code before rendering (`app/design/repair.py`).
A finding names a frame and a patch names an operation on that frame, from a closed vocabulary: `set_columns`, `set_type_role`, `set_fit`, `drop_content`, `set_ground`, `set_z`.
Nothing outside it can be applied, so a hallucinated repair is rejected at parse time.

The loop runs at most 3 rounds (`MAX_ROUNDS`) and **keeps a round only if the page's score falls**, so it can make a page better or leave it alone and has no path to making it worse.
Text within 2% of its frame (`OVERFLOW_TOLERANCE`) is not a finding.
Repair runs in code rather than as a tool because the validator it replaced was a tool the model could decline to call, and it was called zero times across every observed run.

## 13. Validation and the revision ladder

A rendered deck is checked in two ways (`validate_rendered_output`): geometry, read from the rendered PDF, and a vision check of each page for two classes of defect only, damage (overlap, spill, illegible type, low contrast) and arrangement (a large empty area beside a crowded one, an exhibit in a shape that fights it, something oddly placed).
Colours, typefaces and wording are explicitly not defects.

Each finding carries the **smallest fix** that would work, and the fix decides the rung:

| Rung | Meaning | What the loop does |
| --- | --- | --- |
| `nudge` | The page is right; one thing is in the wrong place or size. | Re-render with the same arrangement. **Known gap (#359):** nothing in composition reads the finding and composition is deterministic, so the re-render currently reproduces the identical page. |
| `replace` | The page needs a different arrangement of the same material. | Record the archetype the page was placed on and re-compose the page with that archetype ruled out. The copy is untouched. |
| `redraft` | The copy itself is the problem. | Send the slide back to drafting, with the findings and the exhibits it already carries. |

Rules of the ladder:
- A page draws at most **2 layout revisions** (`LAYOUT_REVISION_CAP`), per page rather than per run, so one stubborn page cannot spend the budget of every other flagged page.
- When one page draws both verdicts, `replace` wins over `nudge`.
- An avoid set that would leave no archetype is ignored rather than obeyed: placing a page the way it was placed before beats refusing to place it.
- The placements a `replace` rules out come from the render result, not from shared state.
  The loop runs each tool as its own task, in a copy of the context, so a value the renderer set in a context variable never reached the loop and every `replace` ruled out nothing (#344).
- **Rebuilt exhibits replace their predecessors.**
  During a revision round, a rebuilt exhibit replaces the blocks of its type and region built before that round unless its slot names one of them, and slot names match regardless of case and separators.
  A model redrafting a page names its slots afresh each time, and a Kubernetes deck shipped three stacked copies of one table (#312).
- A deck is validated at most **3 times** (`MAX_VALIDATION_ATTEMPTS`); only a completed check spends an attempt.
  When 60% or more of the deck is flagged (`FULL_REWRITE_ISSUE_RATIO`), the revision is no longer restricted to the flagged slides.
  Nothing is scheduled at the cap, since it would be rendered and shipped without a validation after it.

A validator turning a draft down (`validate_slide_json` rejecting a slide) is recorded as a verdict, `rejected`, not as a failed tool call; a call the validator could not even read stays a failure (#345).

## 14. What drafting is told

Some decisions are made by telling the model a constraint, because the cheapest fix is a draft that never needs fixing.

- **Exhibit capacity.**
  Each drafting and revision turn states how many charts, tables, diagrams or images, counted together, a page of the design can hold (`run.exhibits_per_page`): the most figure slots any content arrangement has.
  Material that needs more is more than one slide.
  A draft with more exhibits than any arrangement holds is otherwise split onto a continuation page the plan never counted.
- **The exhibits a revised slide already carries**, with their slot names, so a rebuild replaces the right one (#312).
- **The plan's content type** for the slide (chart, table, dashboard, image) and the deck's format vocabulary.

## 15. How a run is checked afterwards

The headless runner scores every run with deterministic checks (`app/research/checks.py`).
The ones that report on the decisions above:

| Check | Fails when |
| --- | --- |
| `text_within_the_page` (blocking) | The rendered geometry shows overflow past the page or overlapping text. |
| `headlines_within_the_ramp` | A content headline is set above 48pt. |
| `body_legible` | Body copy on a page measures below 14pt. |
| `rendered_in_its_design` | The last render went through the stock renderer or the whole-deck fallback. Slides placed on the plain page pass, and are named. |
| `validation_converged` | Validation ended with findings still open. |
| `shipped_what_was_validated` | The file changed after the last validation. |
| `no_failed_tool_calls` | A tool failed, raised or never finished. Validator rejections are reported separately as `draftsRejected`. |

## 16. Parameter reference

| Parameter | Value | Where |
| --- | --- | --- |
| `W_SLOT` | 3.0 | `design/select.py` |
| `W_CAPACITY` | 2.0 | `design/select.py` |
| `W_ASPECT` | 6.0 | `design/select.py` |
| `W_LEGIBLE` | 12.0 | `design/select.py` |
| `W_DENSITY` | 2.5 | `design/select.py` |
| `W_RHYTHM` | 0.8 | `design/select.py` |
| `W_SEQUENCE` | 2.2 | `design/select.py` |
| `W_OVERFLOW` | 6.0 | `design/select.py` |
| `REQUIRED_BONUS` | 5.0 | `design/select.py` |
| `COMFORTABLE_FILL` | 0.72 | `design/select.py` |
| `ASPECT_SATURATION` | 4.0 | `design/select.py` |
| `LEGIBLE_TOLERANCE` | 0.25 | `design/select.py` |
| `MIN_EXHIBIT_IN` | chart 1.6, diagram 1.0, metrics 0.6 | `design/select.py` |
| `LEGIBLE_CELL_PT` | 10.0 | `renderer/table_layout.py` |
| table body sizes | 14 to 8pt | `renderer/table_layout.py` |
| `_MIN_COL_FRACTION` | 0.08 | `renderer/table_layout.py` |
| `MIN_SPLITS` | 4 | `design/run.py` |
| figure natural height | 0.34 of the box | `design/authored.py` |
| `PROSE_SHRINK_FLOOR` | 0.35 | `design/authored.py` |
| `DENSITY_SCALE` | airy 0.7, balanced 1.0, dense 1.45 | `design/authored.py` |
| `PLAIN_COLUMN_SHARE` | 0.34 | `design/vocabulary.py` |
| `COVER_BASELINE`, `SECTION_BASELINE` | 0.93, 0.62 | `design/vocabulary.py` |
| repair `MAX_ROUNDS` | 3 | `design/repair.py` |
| `OVERFLOW_TOLERANCE` | 0.02 | `design/repair.py` |
| `LAYOUT_REVISION_CAP` | 2 per page | `agent/loop.py` |
| `MAX_VALIDATION_ATTEMPTS` | 3 | `agent/loop.py` |
| `FULL_REWRITE_ISSUE_RATIO` | 0.6 | `agent/loop.py` |
| authoring `MAX_ATTEMPTS` | 2 | `design/author_run.py` |

## 17. Evidence log

The runs that motivated rules in this document.
Each is reproducible at no cost by replaying its cassette with the headless runner (`python -m app.research run <task> --mode replay --cassette <run>/cassette.jsonl`).

| Issue | Run | What it showed | Rule |
| --- | --- | --- | --- |
| #306 | 2026-09-24 live runs | An authored cover whose frames overlapped at equal depth was refused at render, and the run shipped nothing. | Gate check 3 (section 4). |
| #312 | `technical-http-caching/20260924-191957-live` | A revision rebuilt a diagram under a new slot name and the page carried it twice. | Rebuilt exhibits replace their predecessors (section 13). |
| #344 | `technical-http-caching/20260924-152618-claude-code` | Three `replace` verdicts re-placed slide 1 identically; `avoided={}` on every recomposition. | Placements travel in the render result (section 13). |
| #345 | `technical-k8s-rollouts/20260924-203834-live` | Seven rejected drafts reported as seven failed tool calls. | Rejections are verdicts (section 13). |
| #348, #312 | `technical-k8s-rollouts/20260924-203834-live` | Slide 2 shipped three stacked copies of its comparison table, text run off the page, body at 9.8pt. | Rebuilt exhibits replace their predecessors. |
| #351 | `technical-k8s-rollouts/20260924-203834-live`, replayed as `20260925-091641-replay` | Two slides with a diagram and a table could not be placed in a one-exhibit-slot language; the whole deck fell back to stock. Once composed, tables were drawn as slivers against the page edge, with cells overprinting. | Splitting divides visuals (section 8); legibility floor (section 7); prose yields first (section 3); no bleed for exhibits; table face, columns and rows (section 11); the render-path check. |
| #359 | found while writing this document | A `nudge` verdict re-renders the identical page, since composition never reads the finding. | Documented as a known gap (section 13). |
| #322 | the 17 languages authored in the recorded runs | A language could derive and still be unable to show a table legibly or compile a cover. | The compile gate (section 4); the plain-page fallback (section 9). |

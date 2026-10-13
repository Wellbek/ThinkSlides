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
A derived variant whose bare frames fail the geometry check is dropped, and the kind as declared is kept: four slots stacked full width can run a caption off the bottom of the page, and the compile gate (section 4) used to refuse the whole language for it, naming a frame the model never wrote.
Five of the nine languages authored in the #376 probe runs were refused that way.
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
- When the page asks for more than it has, the room is taken back in three steps:
  1. **prose gives room back first**, down to 35% of its natural height (`PROSE_SHRINK_FLOOR`), while figures keep theirs;
  2. if that is not enough, every flowing band, figures included, shrinks in proportion;
  3. if even emptying the flowing bands is not enough, every band shrinks in proportion, so the page is never taller than the page.
  Starting at step 2 took a stacked page's figure band from a third of the page to a fifth of an inch, and every table set in it came out as a sliver (#351, fixed in #355).
  Figures are protected only by step 1, which is why a figure can still be drawn small on an overfull arrangement (#350).
  Prose that loses room is measured and split later (section 8); an exhibit that loses room is simply drawn smaller, which is why it is protected.

**The rung a slot is set in.**
Each slot kind is set in one rung of the ramp (`ROLE_FOR_SLOT`), except an opener's heading, which is display type (`_rung_for`).
That one answer sizes the slot's band, measures its capacity and is written on its frame for the renderer.
The band and capacity of a cover heading used to be worked out at headline size while the frame was drawn at display size, a third larger, so a cover advertised room for an eighty-character title in a band one display line tall (#361).

**Capacities.**
Each text slot's `max_chars` is how many characters of ordinary prose its lines hold, found by setting a sample passage in the slot (`setting.capacity`, `app/design/setting.py`):
- in the face, weight, case and tracking the renderer draws that rung in, the same rule `compile` applies;
- across the slot's whole lines (the frame height divided by the rung's leading, floored), so word-wrap loss is in the number;
- set from 12 starting words of the sample (`CAPACITY_STARTS`), keeping the fewest characters any of them fits.
The last point matters because drafting is told a capacity as a limit (section 14).
At display sizes a line holds three or four words, so where the lines happen to break moves the count by up to a quarter, and copy written to the typical count overran by a line.
Before #361 the capacity was characters-per-line times lines, measured in Arial whatever the face and always at regular weight.
On a language set in Georgia and Verdana, text as long as the advertised capacity needed 3.4 times a cover heading's band and 1.5 times every content headline's; after it, every slot of the same language holds its capacity.
Measured packs take the same measurement, in the face each role is drawn in.
Their capacities used to be traced from each block before its frame was snapped to the grid and grown to its line box, so once a pack's frames are final - at the end of a build, and when a shipped pack is loaded - every text slot is measured again from its frame (`vocabulary.remeasured`).
The number it replaces is kept as the slot's `typical_chars`, since for a measured pack it was also the volume the page was built for, which the density term (section 6.4) reads; selection by fullness is unchanged while the ceiling becomes true.

**Measuring copy.**
Everything that asks whether copy fits - the selector's strain and overflow (section 6), repair's overflow findings (section 12), the writer's per-field budgets (section 13) and the capacities above - sets it through `setting.set_copy`, which draws the text the way `compile` will: the pack's face for the role, bold when the role is semibold or bold, capitals when its case is upper or small, the role's tracking, the role's space before and after between paragraphs, and the column gap `compile` sets (none when the block names none).
Each of these used to leave out weight and case, and all of them paragraph spacing, so a bold headline was measured five to ten percent narrower than it is drawn and a spaced block of several paragraphs shorter.
Which face a role is drawn in is one rule (`pack.face_of`), read by the pack, the authored compiler and the measured-pack builder alike.

**One frame, one ground (#396).**
A frame that carries copy or an exhibit and declares no ground of its own is drawn in ink chosen for the ground under it, so it must sit on one ground.
When a shipped pack is loaded, before its capacities are measured again, each top-level frame with more than `STRADDLE_SHARE` of its area on a second ground is seated on the ground it mostly covers (on a tie, the one painted first): it is clipped to that ground's rectangle and set a grid gutter in from every edge it was cut at, and it is placed by fraction from then on (`app/design/grounds.py`).
What is clipped is the frame's place, not its bled rectangle, so an edge it bled past and was not cut at still bleeds, and an exhibit bound to it, which loses its bleed (section 10), stays inside the margins.
A frame with a ground of its own, such as a pull-quote on its own card across a panel edge, is exempt; a scrim is not a ground of its own, since a wash at under half opacity leaves the two grounds different beneath it.
The rule moved four frames across the shipped packs: WTI's split-canvas figure, snapped to a grid column that starts half an inch left of its navy panel, and the line traced along that page's foot across both halves; one column on WTI's exhibit page; and a figure on EY's card row.
On the WTI specimen deck the chart was drawn throughout in the white half's dark ink over the navy, and its title, legend, values and years were close to unreadable.

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
   Some content arrangement must show a probe table and a probe chart legibly, by the rule in section 7.
   The probe table is the median of the 12 tables in the recorded runs: a header and five rows, four columns, cells of about 14 characters.
   The refusal quotes the measured numbers, for example "its tallest figure slot is 1.0in by 7.7in, and a five-row table needs about 1.7in of height at that width".
5. **A deck of every page kind composes on the language's own pages.**
   A probe deck (cover, section, prose page, table page, chart page, closing) must compose and pass the geometry check without any page needing the plain fallback of section 9.

Against the 29 distinct languages authored in the evidence runs, the gate refuses 5, all for check 4: their tallest figure slots are 1.0 to 1.7in tall, and the median table needs about 1.71in at 10pt.
Refusing them costs a retry, whose prompt states the height needed; accepting them would set every typical table in the deck below 10pt, which is the defect section 7 exists to prevent.
`python -m app.research.gate_audit ../../benchmark/evidence` reproduces this, and `tests/test_gate_audit.py` pins it, so a change to the gate fails until this sentence is updated.

## 5. From a slide to material

A drafted slide is read into a *material shape* (`run.material_from`, `material.shape_from`): an ordered list of items, each with a kind.

- The **heading** is the slide's own `heading` or `title` field, or else its first single-line text block.
- Further text is **body** paragraphs.
- `chart`, `table`, `diagram` and `metrics` / `statCallout` blocks are **exhibits**; an exhibit whose figure declares an aspect ratio (an SVG's view box) carries it, so shape can be matched before placement.
- `image` blocks are **figures**.
- Block types with their own payloads (accent cards, row bands, icon cards, banners, image-card rows) contribute their copy as body text rather than being dropped.

The page's **role** is what the slide declares, or else: first slide cover, last slide closing, everything else content.
A drafted slide declares it from the plan's `contentType` (`title` is a cover, `conclusion` a closing, anything else content), so position decides only for a slide that says nothing; the stock renderer follows the same rule.
A plan of fewer than four slides (`MIN_SLIDES_FOR_A_COVER`) opens on content unless the request asks for a cover: a three-slide deck asked to cover three topics spent a third of itself on a title page (#347).
A pull quote is offered only when the body has enough copy for a highlight to be a highlight.
It is offered, not owed: when placing it would leave anything else without a slot, the page is placed without it (#388).
On a heading-and-column arrangement the quote took the column, the body had nowhere to go, every arrangement was refused, and a page with room in its column was split in two.

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
| aspect | 6.0 | How far the slot's shape is from the exhibit's declared shape, `min(1, abs(ln(a_item / a_slot)) / ln 4)`. 6.0 is the smallest weight at which the arrangement tracks the exhibit; the shipped packs' choices did not change at any of the weights measured (1, 2, 4, 6, 8 and 12). |
| legibility | 12.0 | How far short of readable the slot leaves an exhibit (section 7). Weighted like overflow, doubled because pair costs are averaged over a page. |
| prominence | 0.4 | Mismatch between the item's importance and the slot's prominence. |
| frame hint | -2.0 | A bonus when the material is the kind the frame is for (a heading in a headline frame). A heading set as a body paragraph is penalised. |
| sequence | 2.2 | Preference for the k-th paragraph to land in the k-th text slot in reading order, so a page reads as a page rather than three fragments. Only the slots a paragraph sits in are ordered: not a headline, pull-quote, eyebrow or the apparatus. Counting an eyebrow as the first place to read sent a cover's lone subtitle into it, in small capitals above the title, instead of to the standfirst below. |
| over the slot's `max_chars` | up to 2.0 | `min(2, (chars - max_chars) / max_chars)`: a fast character-count check alongside the typeset one. |
| under the slot's `min_chars` | 0.3 | A slot built for more copy than it is given. |
| body copy in a small rung | 3.0 | A body paragraph in a slot that sets text as a caption or footnote (`W_SMALL_RUNG`). Larger than the preferences it competes with, so the paragraph goes there only when no slot set at body size can take it. |

**Parallel points share a rung (#371).**
A page's paragraphs are parallel points when there are two or more and the longest is at most twice the shortest (`PARALLEL_SPREAD`).
On an archetype with a slot set at body size, they are gathered only into such slots (`_grouped`, which sizes the groups to them), and any other rung, a standfirst, a caption or a footnote, is infeasible for them.
Without this, on 10 of the 23 languages the evidence runs authored, one of three equal points went to a caption, footnote or standfirst slot and was set a rung apart from the other two.
An archetype with no body-size slot keeps the cost above instead, so it stays usable.
Slots set in a caption or footnote rung are not counted as body slots for grouping either, whenever the archetype has a slot set at body size.

A required slot pulls material into itself with a bonus of 5.0, because the assignment has no concept of "this column must be used".
The bonus applies only when the item suits the slot (no more than 1.5 times its `max_chars`); applied flatly it outweighed every other term, and a three-hundred-character paragraph won the headline slot.

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
- a typed **diagram** (flow, value chain, matrix, funnel, pyramid, timeline, Venn, org chart) is measured the same way: laid out at the slot's width at 10pt with its details, the size a table's cells are held to (`diagrams.needed_height_in`, `LEGIBLE_PT`), in both faces, the taller kept, and never under 1.0in.
  When a word of it cannot be set whole at that width at 10pt, its floor is infinite and the slot is refused outright (#363): a four-step flow in a half-width slot broke "ReplicaSet" and "maxUnavailable" across lines;
- an **imported SVG** (a Mermaid graph, or a figure drawn with `draw_svg`) is scaled to fit its slot and its text with it, so its floor is the height at which its smallest label is drawn at 10pt: the label's font size times 72, over the document's viewBox height, gives the inches per unit (`importer.legible_height_in`), never under 1.0in.
  When the document at that height is wider than the slot, its floor is infinite.
  A sequence diagram given the short band under a stacked page's copy came out at about a third of its height with labels too small to read (#350);
- a **chart** needs 1.6in, a **metrics** row 0.6in (`MIN_EXHIBIT_IN`).

**The shortfall** is `max(0, floor - height) / floor`, from 0 (fits) to 1.

**The decision.**
- For a **table**, a typed **diagram** or an imported **SVG**, any shortfall makes the slot infeasible: its floor is an exact measurement of the exhibit itself, so falling short means setting it below 10pt.
  With the tolerance the estimated floors get, tables in half-width slots came out at 8 and 9pt (#357).
- For a chart or a metrics row, whose floors are estimates, a shortfall above 0.25 (`LEGIBLE_TOLERANCE`) makes the slot **infeasible**.
  As a cost, it was outbid by the text overflow of every other arrangement, and a five-row table was set in a slot 0.2in tall with its rows printed over each other (#351).
- A shortfall within tolerance is charged at weight 12.0, so a slot that fits wins over one that nearly does.
- If no content arrangement in the pack has a slot the exhibit could be read in (`readable_somewhere` is false), splitting the prose around it cannot help, because legibility depends on the slot and not on the prose.
  Such an exhibit is handled in three steps:
  1. **The pack's plain page** (section 9), whose figure frame is the width of the grid and most of the page tall, is tried with the page's whole material.
  2. If that fails because of the prose around the exhibit, and the plain page could show the exhibit legibly on its own, the page is **split** until the exhibit stands on a part the plain page can hold.
     One paragraph and one exhibit can be divided for this, the exhibit continuing on its own page.
  3. If even the plain page is too small for the exhibit (a forty-row table), no split can help, so the page is **placed cramped** at once, with the floor relaxed and an `exhibit_placed_below_legible_size` warning.
  A six-column table forced into a short slot came out at 8pt and still ran into the prose below it, since a native table row grows to its text.
  The plain page is never used in a preview, which shows only what the language itself can do, and a revision that avoids the plain page for a slide is obeyed.

**Margins.**
A slot that bleeds off the page keeps its bleed for a photograph and loses it for an exhibit (`bind._fill`).
A table, chart or diagram carries text that has to sit inside the margins the rest of the page keeps; a language whose figure slot bled set every exhibit hard against the page edge.

**Setting a diagram in the slot it gets** (`diagrams._fitted`).
A diagram is drawn, then read back: every word has to fit whole across its box, breaking only where the typesetter may (spaces, and after a hyphen or dash), and every label and detail has to fit inside its node.
When it does not, what gives way, in order, and each only as far as it has to:
1. the diagram's height, from the shape it is drawn at (`ASPECT`) up to the rect it was handed, because type is what makes it readable;
2. both sizes, together, half a point at a time (`SIZE_STEP_PT`), down to 10pt (`LEGIBLE_PT`);
3. the detail lines, at sizes down to 10pt again, since detail goes before a label is set below reading size;
4. below 10pt, down to 8pt labels and 7pt details (`MIN_LABEL_PT`, `MIN_DETAIL_PT`), with details and then without: the last resort for a slot chosen anyway.
Past all four it is drawn at the floor with the renderer's autofit left on, so its text at least stays inside its boxes.
Both searches are bisections, for the largest size that reads at the full height and then the least height that reads at that size (to 1% of the room, `HEIGHT_PRECISION`), so a diagram costs a handful of draws, and a measuring pass that hands it a hundred inches gets back the height it needs rather than the hundred inches.
Every label of one diagram is set at one size and every detail at another, with autofit off for node text: left to it, each box was shrunk on its own and no two steps of a flow matched.
Labels of nodes side by side start at the same height, measured with the details only when the boxes are tall enough to show them (`DETAIL_MIN_BOX_IN`).
Diagram text is measured with the real typesetter, 4% narrower than its box (`TEXT_MEASURE`, the same margin as an opener's title), both for line counts and for whether a word fits whole.

The floor the selector reads (section 7, above) is this layout at 10pt **with** the details, in a rect up to eight times the diagram's own shape (`PROBE_HEIGHT_FACTOR`), the least height that reads; infinite when none does.
A slot that could show only the labels is not offered, since what the diagram was drawn to say would be lost; a wider slot, the plain page at worst, shows all of it.
The title band is measured at the size the title is drawn at, from the pack's body size.

**Inked for its ground (#396).**
A chart is drawn for the ground under its own frame, and a diagram, a row of figures or another typed block is compiled for it: the topmost frame declaring a ground whose rectangle contains the exhibit's, else the page's.
Both used to be drawn for the page's ground on every page, so on a split page an exhibit on the dark half was inked for the light one.
On a dark ground the chart's furniture takes reversed ink, and each series colour that falls below `SERIES_CONTRAST` against the ground is mixed toward white in tenths until it clears it, keeping its hue so the series reads as the same series on every page.
The WTI navy series on WTI's navy panel measured 1.1:1.

## 8. Splitting a page

A page is also split, when the user left the page count free, if its copy still overflows its frames once repaired: more pages before smaller type (#368, section 12).

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
  A page whose exhibit not even the plain page could show legibly is not split for that, since legibility depends on the slot and not on the prose around it (section 7).

Splitting adds pages the plan did not count, so drafting is told the design's exhibit capacity up front (section 14).

## 9. The fallback chain

Each step is tried only when the one before it fails, and each affects the smallest unit it can.

1. **The cheapest feasible archetype** for the page (section 6).
2. **A split** of the page into parts that each fit (section 8).
3. **The pack's plain page, for an exhibit** no arrangement can show legibly (section 7), logged as `exhibit_placed_on_plain_page`.
   **A cramped placement**, with the legibility floor relaxed, when even the plain page cannot hold it or nothing is left to divide.
4. **The whole pack again, without the avoid list**, when the failure came from a `replace` verdict's avoid list (section 13).
   The cheapest arrangement is taken again, which is the one the page was on unless its material or neighbours changed; placing it as before beats downgrading it because a revision was asked for.
5. **The pack's plain page**, for that page only (`vocabulary.plain_content`, #322).
   A headline, one full-width column and a full-width figure frame, composed from the pack's own grid, type and colours.
   It is never offered alongside the pack's archetypes, because it would win on capacity and flatten every deck; it is used only after all of them have refused a page.
   Every other page keeps its arrangement.
6. **The stock renderer for the whole deck**, only if even the plain page refuses a page.
   Before #322 this was step 2's failure mode, and one unplaceable slide put a six-slide technical deck on stock placeholders (#351).

Previews are different: a specimen page no archetype can carry is skipped, since three preview cards showing two pages each are still a comparison a user can make.

Steps 3, 5 and 6 are logged (step 3 as `exhibit_placed_on_plain_page` or `exhibit_placed_below_legible_size`, then `page_placed_on_plain_fallback` and `pack_could_not_place_deck`), and steps 5 and 6 are also recorded on the render's trace span and named by the run checks (section 15).
Steps 2 and 4 are ordinary outcomes and are not logged; a split shows in the page count.

## 10. Binding material into frames

Placed items are copied into their archetype's frames (`app/design/bind.py`).
A frame nothing was bound to is dropped, unless it carries a ground or scrim, and the page closes up around it (`_compact`), so a figure slot on a page with no figure does not leave a third of the page white.

- **Vertically**, rows below a dropped frame move up by the archetype's own smallest row gap, and whatever is left at the bottom goes to the last row.
- **Horizontally**, a true row that lost a member (its original members side by side, no two in the same columns) shares its full width among the survivors, in proportion to the widths they had.
  Spans use the largest remainder, so they always sum to the row's width.
  Packing survivors left at their old widths only moved the hole to the end of the row (#357).
- **A survivor is cut to its copy.**
  A text frame that shared a row with a frame that was dropped was as tall as that row, so the hole moved inside it: one point at the top of a page and the next row a figure's height further down.
  Before the page closes up, each such frame that holds only text is shortened to the height its copy needs at the width it is about to have (`_fitted_to_copy`), measured as in section 3, with `compile`'s equal split among several blocks and the frame's padding counted, plus one line of the block's leading.
  That line is the window in which PowerPoint or LibreOffice can break a line a word earlier than the typesetter; cut to the exact depth, the last line would overprint the row closed up beneath it.
  Only a true row counts, its members side by side, the same rule as the horizontal close-up: a heading stacked over prose beside a tall figure shares the figure's band without being in a row with it.
  A frame is only ever shortened, and an exhibit is never cut, since it fills whatever it is given.
  If the compaction is then discarded, the page gets its frames back uncut.
- Two frames overlap only if they share columns as well as height.
  Reading side-by-side frames as an overlap made compaction skip every page with a row (#357).
- Only pages whose frames are all grid-placed are compacted.
  A measured pack mixes grid and fractional placement, whose vertical fractions are not of the same box, so its pages are left exactly as the archetype states.
- A compaction that does not come out as a clean stack is discarded, and the page renders as its archetype states.

**Where a block sits in its frame.**
Text is anchored to the top of its frame, except in a headline frame set in the display rung, which is anchored to the bottom (`_anchor`).
The rule reads the frame, not the block, so a title repair steps down to a smaller rung still rests on the same baseline; a title too long for its band would run up into the empty top of the page rather than down over its standfirst.
Openers are built upward from a baseline, and a title's band is tall enough for the longest title it is offered; hung from the top, a one-line title left an empty line between itself and its standfirst, so the shorter the title, the further it drifted from what it introduces.

### Settling short material (#378)

An archetype states each frame's height for a page of the material it was measured on.
A page with less kept those heights: copy held a frame running to the bottom margin, a flow drawn at its own shape floated in a frame several times its height, and the bottom third of the page was empty.
Validation flagged it every round, and nothing on the ladder could help: a `replace` moved it to another arrangement with the same heights, and a redraft only changes the words.

So once a page is bound (`settle.settle`):
- Each row below the heading is cut to what it holds, where what it holds has a height of its own: copy as it will be set, plus one line of its own leading; a table planned as the renderer plans it; a diagram or a figure row compiled and measured; an imported SVG at its own aspect.
  A chart, a photograph or anything else that fills its frame has none, and its row keeps the archetype's height.
- The rows are closed up at the archetype's own gap.
- In a row of several frames, an exhibit shorter than the row is centred on it; copy stays at the row's top.
  At the top of a column the copy runs the full length of, a short flow left the rest of that column empty.
- **Short prose alone is the heading's lead (#392).**
  When there is a heading and everything under it is prose filling at most `SHORT_PROSE_SHARE` (half) of the space there, the prose starts exactly `LEAD_GAP` (10% of the content box) under the heading, or the archetype's own gap if that is larger, at the rung selection gave it; the space left is below it.
  Centred instead, three sentences of about 60 characters floated mid-page as an island apart from their heading, 1.25in of air above them and 1.9in below (`probe-footer-not-cover/20261013-061639-claude-code`, all three pages).
  Only the position changes: setting such prose a rung up was tried and dropped, since a rung is selection's decision (it would have set over-long paragraphs and bulleted lists in a display rung, and changed face part-way through a split story).
  Prose beside an exhibit, and prose that is not short, is settled at the optical centre as below.
  Across the 24 languages the gate admits from the evidence runs, composing three short sentences, nine, and three beside a table, no page changed its arrangement, its rungs, its split or its copy budget against the rule before it.
- The heading stays where the archetype puts it, and the rows below it are set as one group in the space under it, at the optical centre: `OPTICAL_SHARE` (0.4) of the free space above the group, the rest below, and never closer to the heading than the archetype's own gap.
- Nothing moves when the space freed is under `MIN_SLACK` (8% of the content box): a page that nearly fills its arrangement is the arrangement working.
- Only content and exhibit pages that are a clean vertical stack of grid-placed frames are settled, the shape an authored language derives; an opener, a divider and a closing page compose their space on purpose, and a measured pack is left as its own pages were.

## 11. Laying out a table

A table is laid out from its content, not split evenly (`app/renderer/table_layout.py`, `plan_table`), and **always drawn as a native, editable PowerPoint table, never as a picture**, on both render paths (`app/renderer/native_table.py`).
A table is data a user edits after the deck is delivered; the composed path used to rasterise tables, and a short table placed in a tall frame was stretched until it read as a condensed face.
The design compiler still carries a table as a boxed image mark, so geometry and measurement treat it as an exhibit, and only the renderer draws it, natively.
On a composed page it sits at the top of its frame; on a template slide it hangs a third of the way into any surplus.

- **Face.**
  The table is planned in the face it is drawn in, bold for the header and total rows.
  Measuring in one face and drawing in another is how planned rows came out too short for the lines drawn in them.
- **Columns.**
  Widths are proportional to each column's longest cell, with a floor of 8% of the table, and **no column narrower than its longest unbroken word** (a point larger and bold in the header).
  Proportional widths alone squeezed a column of one-word labels beside a column of sentences below its own words, and "Strategy" was set as "Stra / tegy".
- **Size.**
  Body sizes 14, 13, 12, 11, 10, 9 and 8pt are tried in turn, largest first; a size at which some word cannot be set whole is skipped.
  The first size whose rows fit the band is used.
- **Rows.**
  Each row is as tall as its tallest wrapped cell, and the native table is given those heights.
- **Numbers.**
  A column of figures (ignoring blanks and placeholders such as "-" and "n/a") is right-aligned; a last row labelled Total, Sum or similar gets the total treatment.
- **Colour.**
  Filled rows take the design's table colours; a hairline table's cells are transparent, so its text takes a colour that reads on the ground behind it.
- **Overflow is reported, not hidden.**
  If the table is still too tall at 8pt, the plan says so (`overflows`) and the renderer logs `table_exceeds_its_frame`.
  Rows are not shortened on paper: a native row grows to its text regardless, so shortening it only hid an overflow the rendered table still had.
  Section 7's floor exists so that a layout never asks for this.

## 12. Repair: fixing a composed page in code

After composition, each page is repaired in code before rendering (`app/design/repair.py`).
A finding names a frame and a patch names an operation on that frame, from a closed vocabulary (`repair.PATCHES`): `set_columns`, `set_type_role`, `set_fit`, `drop_content`, `set_ground`, `set_z`, `move_band`.
An operation outside it raises when applied, and the loop skips that patch, so nothing unaccounted for reaches a page.

The loop runs at most 3 rounds (`MAX_ROUNDS`).
A round is **kept only if it lowers the page's score by at least 0.01** (`MIN_IMPROVEMENT`), and the loop stops at the first round that does not, so it can make a page better or leave it alone and has no path to making it worse.

The most common patch is `set_type_role`: copy that overruns its frame is set one rung smaller on the pack's ramp, **never below the floor of the rung its frame was designed for** (`repair.designed_floor_pt`).
The legibility check reads that same designed floor.
Checked against the rung in use instead, body copy stepped down to a caption was held to the caption floor, passed, and could step again: a paragraph ended on the footnote rung at 11.3pt against its pack's 12.15pt body floor (#348, reproduced in `tests/test_body_copy_floor.py`).
The body floor itself is 75% of the 3% of page height an authored language may not invent body type below (`authored.POLICY_FLOOR_SHARE`), about 12.2pt on a 16:9 slide, which is the product owner's policy of about 12pt (#368); the benchmark's `body_legible` check holds body to the same 12pt.

**The type-size policy (#368).**
Too much copy means more pages, not smaller type, unless the user fixed the page count:
- with the count free, composition splits a part whose copy still overflows its frames after repair onto a continuation page, while the part can be divided (`compose(pages="free")`);
- with it fixed (the brief states a `slideCount`), repair may set copy below the designed floor, down to `RELAXED_FLOOR_PT` (10pt, the size tables and diagrams are held to), using the pack's own smaller rungs (`compose(pages="fixed")`, `repair.relaxed_floor`);
- every page set below its floor records the smallest size (`Provenance.below_floor_pt`); the render reports the pages and slides, the trace keeps the pages (`belowFloorPages`), and the closing message names each slide and size and says it was to keep the count the user asked for.
A pack whose own ramp has no rung below the floor cannot go below it however the count stands, which is every authored language so far: their body is 16pt or more and their smallest rungs sit above 12pt.
A measured pack keeps the sizes its publication set (BXSL's body is 11pt, read on screen); its floor is its own (`policy.floor_for`).
Copy that still does not fit is left too long, measured, and handed back to the writer with a budget (section 13).
Because repair works page by page, a headline stepped down on one page and not another made one deck's headlines differ between pages (#357).
So after repair, headings are grouped by the rung their arrangement designed them on (recorded on the page as `Provenance.heading_rung`, with the frame they were bound to as `heading_frame`), and each group takes the smallest rung repair stepped any member down to, which every member is known to fit.
A measured pack's deliberate differences between arrangements, an assertion in a pull quote on one page and a headline on another, are kept, and a group is never taken to body size or below: a heading that had to be set as body copy to fit is one broken page, not the deck's new voice.
Text within 2% of its frame (`OVERFLOW_TOLERANCE`) is not a finding.
Repair runs in code rather than as a tool because the validator it replaced was a tool the model could decline to call, and it was called zero times across every observed run.

## 13. Validation and the revision ladder

A rendered deck is checked in two ways (`validate_rendered_output`): geometry, read from the rendered PDF, and a vision check of each page for two classes of defect only, damage (overlap, spill, illegible type, low contrast) and arrangement (a large empty area beside a crowded one, an exhibit in a shape that fights it, something oddly placed).
Colours, typefaces and wording are explicitly not defects.
The pages are judged concurrently, at most `compile_concurrency` at once, and a page whose call fails is skipped on its own rather than ending the review of the pages after it.
A recorded verdict is keyed by its page's picture as well as the prompt, which every page shares, so a replay gives each page its own verdict whatever order they were recorded in.

Each finding carries the **smallest fix** that would work, and the fix decides the rung:

| Rung | Meaning | What the loop does |
| --- | --- | --- |
| `nudge` | The page is right; one thing is in the wrong place or size. | Answered as a `replace`. Nothing in composition reads a finding and composition is deterministic, so re-rendering the same arrangement reproduced the identical page and spent a revision on it (#359). Acting on the finding itself (handing it to repair as a patch) is the better long-term rung and is not built. |
| `replace` | The page needs a different arrangement of the same material. | Record the archetype the page was placed on and re-compose the page with that archetype ruled out. The copy is untouched. |
| `redraft` | The copy itself is the problem. | Send the slide back to be written again (by compile where it applies, section 14), with the findings and the exhibits it already carries. |

Rules of the ladder:
- A page draws at most **2 layout revisions** (`LAYOUT_REVISION_CAP`), per page rather than per run, so one stubborn page cannot spend the budget of every other flagged page.
- Both layout verdicts are answered the same way, so one page drawing both is one revision.
- An avoid set that would leave no archetype is ignored rather than obeyed: placing a page the way it was placed before beats refusing to place it.
- **A re-placement must not make the page worse.**
  If, once repaired, a re-placed page still has copy running out of a frame, the page is placed as if nothing were ruled out, which puts it back on an arrangement that holds it.
  The selector charges overflow as a cost, so the cheapest arrangement left after a `replace` could be one whose only prose slot is a caption a third the size of the copy, and repair cannot set copy below its designed floor: on the last round of `technical-http-caching/20261012-162837-claude-code` the body ran off the bottom of the page and shipped (#380).
- Every composed page records its **alternatives**: the pack's other arrangements that could carry the same material legibly, with its copy inside its frames after repair, leaving out the one it is on and the ones it was asked to avoid.
  A slide none of whose pages has an alternative is reported as unmovable by the render, and the loop does not offer it a `replace`: its finding goes on to the redraft rung instead.
  Before this, the loop learnt that a page could not move only by spending a render on it - probes for #376 saw a second `replace` swap two pages between the only two arrangements that held them, and a third hand both back unchanged.
- The placements a `replace` rules out come from the render result, not from shared state.
  The loop runs each tool as its own task, in a copy of the context, so a value the renderer set in a context variable never reached the loop and every `replace` ruled out nothing (#344).
- **Rebuilt exhibits replace their predecessors.**
  During a revision round, a rebuilt exhibit replaces the blocks of its type and region built before that round unless its slot names one of them, and slot names match regardless of case and separators.
  A model redrafting a page names its slots afresh each time, and a Kubernetes deck shipped three stacked copies of one table (#312).
- A deck is validated at most **3 times** (`MAX_VALIDATION_ATTEMPTS`); only a completed check spends an attempt.
  When 60% or more of the deck is flagged (`FULL_REWRITE_ISSUE_RATIO`), the revision is no longer restricted to the flagged slides.
  Nothing is scheduled at the cap, since it would be rendered and shipped without a validation after it.

**Copy over budget.**
After each render, composition measures the copy still running past its frames (`run.copy_over_budget`): per slide **and per field** (the headline, the body copy, a caption), how many characters it carries and about how many its frames hold at the size they are set in.
Frames the system fills itself, a source line, footnotes, a folio, are never reported: they are not the writer's to shorten.
After validation, each such slide gets a `redraft` finding naming the fields over budget, for example "the headline is about 90 characters where the page sets about 70" (`WorkingState.copy_findings`), which the revision directive passes to the writer.
A code edit changes the file without a render, so it drops every budget measured before it.
A budget is something a writer can act on; "it overflows" is not.
This is the fit-before-render order the project plan sets out: fit in code first, and only then ask the model to shorten a specific field to a character budget.

A validator turning a draft down (`validate_slide_json` rejecting a slide) is recorded as a verdict, `rejected`, not as a failed tool call; a call the validator could not even read stays a failure (#345).

### Amending a finished deck (#376)

A message sent after a deck is finished is a request to change that deck, not a request for a new one.
- The run's state is kept in `deck_snapshots` (one row per session) when a run completes, in addition to the Redis state that is cleared.
  A new message that is later than the snapshot restores it and sets `amending`; a snapshot whose file is gone is ignored and the message starts a deck as before.
- The restored state keeps the brief, the design, the slides and the rendered file, so the intake, consultation, design, research, planning and drafting phases do not run again.
  What the last run concluded about the deck (clean, attempts, findings) is dropped, since the deck is about to change.
- The `change` phase runs next, offers `apply_deck_operation`, `revise_slides` and (when the operator enables code) `edit_deck_with_code`, and tells the model to make exactly the requested change and leave copy, layout and design alone.
  Which tool answers which request is in "Deck-wide operations" below.
  `validate` then judges the changed deck.
  A new finding on a slide the change sent back to drafting (`revise_slides`) walks the run back through the normal revision ladder.
- After a script edited the deck, a new finding on any other slide is that script's doing, and goes back to the `change` step, once (`CHANGE_FIX_ROUNDS`), with the finding in its directive, for one more script that corrects the first without undoing it.
  It never goes to the ladder: re-placing a page cannot recolour a link a script made blue, and the ladder re-placed and then redrafted a cover the user never asked to change, while the re-render dropped an earlier repair on another page (`probe-hyperlink-terms`, 2026-10-04).
  If that script misses too, the finding is reported in the closing message and nothing else is tried.
- A finding the deck was handed over with is not the change's to answer.
  When an amendment starts, the last validation's findings are kept as (slide, finding type) pairs; after the change, a finding matching one of them is reported in the closing message, marked as already there, and schedules no redraft, no re-placement and no backstop script.
  A deck left with only such findings counts as settled.
  The cost is that new damage of the same type on a slide that already had a finding is not told apart from the old one; the alternative, re-judging the whole deck, redrafted slide 2's copy in answer to a request for a watermark (`probe-watermark`, 2026-10-04).
- Every script that edited the deck is kept in `code_edit_log`.
  A render rebuilds the file from the slides, which drops all such edits, so each render replays the log through the same three gates over the fresh file (`edit_deck_with_code.replay`).
  An edit that no longer applies is logged and skipped; the rest stand.
- A change declined in words, with no script run and no slide redrafted, leaves the deck as it was handed over, so the handover's findings are restored as its validation and it is not checked again.
  Checking it again spent a vision call per page on a deck nobody touched (`probe-rotate-exhibit`, 2026-10-04).
- A change step that runs out of turns without an edit hands the deck back unchanged, and the closing message is told so, with the last refusal: the step's own directive was otherwise the last thing it read, and the summary could describe the change as made (`probe-progress-bar`, 2026-10-04).
- A script that draws a figure must say where its numbers came from (`inputs`).
  Whether it draws one is read off its syntax: a name from the deck's chart or table API anywhere, or a plotting verb (`plot`, `bar`, `barh`, `pie`, `scatter`, `hist`) called as a method or imported from a library.
  A text match on `bar(` refused a progress bar drawn by the script's own helper `bar`, and then by `draw_bar` (`probe-progress-bar`, 2026-10-04); a script that does not parse is still matched on its text.
- A script edit is looked at before the deck is handed over, whatever phase made it.
  Any edit that changes the file reopens `validate` for one more check, past the revision budget if need be; at the cap that check schedules no revision, but it is what the closing message reports.
  The `amend` backstop runs only once validation has spent its budget, so before this its edit shipped unchecked and the closing message listed the findings it had just fixed as still there (`probe-watermark`, 2026-10-04).

### Deck-wide operations (#376)

A request about the deck as a whole rather than what one slide says - a logo in a corner, a watermark, a footer with page numbers, a QR code, a progress bar, a recolour, a link on every mention of a phrase - is a typed operation, not a script.
The eight probes answered all of them with `edit_deck_with_code` (`benchmark/reports/2026-10-04-probes-376.md`): every edit needed two model turns reading the deck before it could choose coordinates, a recolour reached shapes but not the theme, and an edit outlived a re-render only by being run again over whatever the new composition produced, where a script that guarded itself ("the corner is taken") would skip and the change would be gone.

- **The vocabulary** (`renderer/deck_ops.py`) has three kinds.
  An *overlay* is an element (text, a picture the user supplied, a QR code, a shape, or a progress bar) with an anchor (the four corners, the four edge midpoints, the centre), a page set (`all`, `all-but-cover`, `first`, `last`, or page numbers), an optional size, opacity, rotation and link, and a layer (`front` or `watermark`).
  A *recolour* names a colour role (`accent`, `accent_alt`) and the hex it becomes.
  A *link* names a phrase and a URL.
  Colours are the deck's own roles (`ink`, `muted`, `accent`, `accent_alt`, `ground`) or a hex value; text is set in the deck's body face.
  An operation that cannot be meant (a progress bar on a side edge, a QR code as a watermark, a colour that is neither a role nor a hex value) is refused with the reason when it is offered, not when it is drawn.
- **Recorded, then rendered.**
  `apply_deck_operation` checks an operation and hands it back; the loop records it in the run (`deck_operations`, kept across pauses and in the deck snapshot) and bumps the design serial, so the deck is rendered again and validated.
  The render applies every recorded operation to every file it writes, after composition and before the replay of recorded scripts.
  There is therefore one way an operation reaches a deck, the same on the first render as on the tenth, and a re-render cannot drop one.
  It is applied to the written file rather than inside composition so that it holds on all three render paths (composed, uploaded template, stock) and on pages the renderer appends itself, such as the Sources appendix.
  An operation replaced under its own id, or removed by id, is gone from the next render; a removal naming an id the deck does not carry is refused with the ids it does, rather than reported as done.
- **Placement.**
  An overlay sits at its anchor, inset from the top or bottom edge by the smaller of `EDGE_INSET_IN` and the pack's narrowest margin, which keeps it in the margin band, clear of the grid's content by construction.
  Across, it sits on the grid's side margin, so a footer ends where the copy ends: set `EDGE_INSET_IN` from the edge it lined up with nothing on a page whose copy starts 0.85in in (`probe-footer-not-cover/20261013-061639-claude-code`).
  A progress bar runs flush along its edge, as long as the share of the deck reached on that page.
  A picture is fitted inside the size asked for at its own aspect, never stretched; with no size, a picture is 0.6in high and a QR code 1.0in.
  Text with no size is set at the pack's caption rung.
- **Collision.**
  On each page the overlay is tested against the page's content: every shape with text, every picture, table, chart and group, and every overlay already placed on that page by an earlier operation - including what the slide shows from its layout and master (a template's own logo or footer), but not their empty placeholders, which draw nothing.
  A watermark is not content to a later overlay: it crosses everything by design.
  A shape without text (a panel, a rule) and a shape covering at least `GROUND_SHARE` of the page are grounds, not content: an overlay may sit on them, as type sits on a panel.
  Overlap counts only past `TOLERANCE_IN`.
  The overlay is tried at each of `SHRINK_STEPS` of its size in turn and placed at the first that covers nothing; at none, the page goes without it, and the report names the page and what it would have covered.
  Below half its size a logo stops reading as the user's logo and a footer as text, so half is the floor rather than a smaller size that technically fits.
  The closing message is told which pages went without and why, and to offer another position or a smaller size.
- **Watermarks** are not collision-checked: a watermark crosses the content by definition.
  It is drawn in front of the content, at `WATERMARK_OPACITY`, rotated by `WATERMARK_ROTATION_DEG` unless the operation says otherwise, and sized so its line runs about two thirds of the page's diagonal.
  In front, because a watermark behind the content is hidden by any filled shape in front of it: the probe's watermark read "DRA" on the page whose diagram boxes covered the rest.
  It is named as a background shape, so the geometry check does not count it as overlapping the copy, and the vision prompt treats a faint mark across the copy as requested.
  Its box is sized from its text with wrapping and auto-fit both off, since LibreOffice wrapped a rotated auto-fit box to "DRAF/T" even with wrapping off.
- **Contrast.**
  Every overlay's colour is checked against the ground it actually lands on - the topmost solid shape without text under its centre, else the slide's background - and replaced by the deck's ink (or reversed ink, on a dark ground) when it falls short: `TEXT_CONTRAST` for text, `MARK_CONTRAST` for a bar, a rule or a shape.
  Without it an accent progress bar on a cover painted in the accent was simply not there.
- **A recolour** is applied to the pack and the design system before anything is drawn, so what the render paints from them is in the new colour, charts included (they reach the file as pictures, which no pass over the file can recolour).
  The same operation then repaints, on every page, layout, master and native chart, each colour that was the role's previous value - the pack's or design system's own, and the theme slot's - and sets the theme slot (`accent1`, `accent2`), so whatever a template or a later page draws from the theme is in the new colour too.
  A value the deck also uses for another role (ink, muted, ground, the other accent) is left on the page: where accent and ink share a hex, repainting it would turn the body text the new colour.
- **A link** splits runs at the phrase's edges, so only the phrase is linked, matched without regard to case, as a whole word ("AI" is not the "ai" in "maintain"), and never across a line break or into a field.
  It is underlined and carries Office's `hlinkClr` extension set to the text's own colour, so it is not drawn in the theme's hyperlink blue: on the dark cover of `probe-hyperlink-terms` that blue was the defect.
  A phrase the deck never uses is reported, not silently a no-op.
- **"All but the cover"** skips the pages a reader takes for a cover: on the composed path, pages set as covers, plus the first page of the slide the deck opens on when that slide is a cover by composition's own rule (`page_role`), even when composition set it on a content page for the copy it carries.
  A short deck that opens on content (#347) has no cover, and the operation goes on every page; the report says so, and the closing message is told to tell the user, since they asked to skip a page the deck does not have.
  On the template and stock paths it is the first slide when the slide calls itself a cover or a title page, by its role, content type or layout name read whole ("Title and Content" is not a title page).
- **"Last"** is the last page of the deck as designed, not the Sources appendix the renderer appends after it.
  A listed page past the end of the deck is reported as left off, with the deck's length, rather than dropped without a word.
- **Pictures** come from the user only.
  An image overlay names a picture the user supplied; one that names any other is refused, and the change step is told to make the rest of the change and ask for the picture rather than draw a stand-in.
  The run records the picture's content hash with the operation and finds it by that on every render, since names are positional and a later upload can move them.
  `probe-overlay-picture` drew a coffee cup out of ellipses and shipped it as the user's coffee cup.
- **A failure costs one operation.**
  A missing picture or a phrase not found is reported for that operation and the others are applied; an error inside one operation is reported for it, and the render still ships the deck.
- **When code is still the answer.**
  `edit_deck_with_code` stays for what the vocabulary cannot say: moving, rotating or restyling something that is already on a page.
  The change step names it only when the operator has enabled code; otherwise a request outside the vocabulary is declined in words.

## 14. What drafting is told

Some decisions are made by telling the model a constraint, because the cheapest fix is a draft that never needs fixing.

- **Exhibit capacity.**
  Each drafting and revision turn states how many charts, tables, diagrams or images, counted together, a page of the design can hold (`run.exhibits_per_page`): the most figure slots any content arrangement has.
  Material that needs more is more than one slide.
  A draft with more exhibits than any arrangement holds is otherwise split onto a continuation page the plan never counted.
- **Headline length.**
  Each drafting and revision turn states how long a headline the design sets at full size (`run.headline_capacity`): the tightest slot where the selector puts a probe heading at the pack's `headline` rung.
  One headline past it is stepped down the ramp by repair, and then every headline designed on that rung is set smaller to match (section 12).
  A measured pack whose headline-rung slots are label-sized (under 20 characters) states nothing, rather than a budget no claim could meet.
- **The exhibits a revised slide already carries**, with their slot names, so a rebuild replaces the right one (#312).
- **The plan's content type** for the slide (chart, table, dashboard, image) and the deck's format vocabulary.

### Compile: one answer per slide, all at once (#323)

On a design language, drafting is a compile step, not a conversation.
Each outstanding slide is asked for in **one request**, and its answer carries the whole slide: the `build_*` calls its exhibits need and one `write_slide` call with its copy.
The requests run **concurrently**, at most `compile_concurrency` (6) at once.
- Every request shares one prompt prefix: the sourcing rules, how exhibits are built, the research findings and the deck's whole outline, then the user's own messages.
  Only the slide's own request differs, and it comes last, so a provider can cache the prefix across the deck.
- On a provider that caches prefixes by itself (`prefix_cache`: OpenAI and Azure OpenAI; the claude-code and replay stand-ins, which take a live run's shape), the first slide's request goes **alone**, and the rest are sent once it is answered (#325).
  A provider writes its cache as it serves a request, so requests sent together each pay for the shared prefix in full; on the three-slide footer probe the prefix is 40.9K of each request's 47.5K characters.
  One request's latency buys every other slide that prefix from the cache.
  The others are released by the first answer, by the first back-off the request meets (a rate-limited request has cached nothing, and the others must not wait out its retries), or by its failure.
  On Zhipu, which is not known to cache automatically, and Anthropic, which caches only up to explicit breakpoints the provider does not yet set, every slide goes at once as before.
- The answer is checked by the same code a drafted slide went through: each builder validates its own arguments, the grounding check refuses a figure nothing stands behind, `design_slide_content` finishes the copy it is handed (`written`) without a call of its own, and `validate_slide_json` checks the slide.
- An exhibit is put on the slide the request was for, whatever slide index the answer named.
- A slide that fails any check is asked **once more** (`MAX_ATTEMPTS` 2), in the same conversation, with each call's result and a line per failure.
  This is the plan's escalation rule: a compile output failing a gate twice moves on.
  The retry is a whole answer: what the first answer built is discarded before it is applied, since both answers fall in one validation round, where a rebuild under a new slot name would otherwise sit beside the first.
- A slide compile could not finish goes to the drafting conversation.
  One whose copy never arrived is still outstanding; one whose copy was stored but whose exhibit or slide check failed is queued for drafting, and the drafting directive names what did not go through.
  Without that, the stored slide counted as written and shipped with copy written round an exhibit that was never built.
- A request that fails hands its slide to drafting rather than ending the run: one slide's error must not lose the slides written beside it.
  A provider that is really down fails drafting next, which does end the run.
- An image selected in the same answer is placed with the copy whether or not the copy names it, and the copy's budget counts the exhibits the plan gave the slide, so the budget it is written to is the one it is checked against.
- Compile does not run on an uploaded template, where each slide chooses a layout before it is written.
  It also does not run where the drafting conversation would offer `edit_deck_with_code`, one slide sent back with a deck already rendered, since a position fix may be the right answer there.
  Every first pass and every round that rewrites several slides has no such script on offer.
- `written` is stripped from any call the model makes itself; it is compile's alone, since it skips the call that writes the copy.

Before compile, drafting a slide took four or five sequential calls: choose an exhibit, call `design_slide_content` (which made a call of its own for the copy), validate, end the turn.
On the three-slide HTTP caching task the whole run took 17 chat calls before compile and 11 after, including two retries and a revision round (`technical-http-caching/20261012-162837-claude-code`).
Recorded concurrent answers arrive in completion order, so replay hands each request the answer recorded under its own hash; when the prompt drifted (every prompt does on another day, since each carries the date), the answer recorded from the same place in the run, which names the slide, and only then the next in order.

### Prompt layout for a prefix cache (#325)

Providers cache a request by prefix, reading tools first, then the system prompt, then the conversation (R-107), so the system prompt the loop's model-driven steps share is laid out with everything that holds for the whole run first and everything a step changes last.
- **Head, the same at every step once the design is chosen** (choosing a design language withholds the template geometry tools for the rest of the run): the date line, the rules, the detail level, the research rules for the run's mode, how exhibits are built, the tools the run has, and the design system.
  The design system is written with its keys sorted: restored from saved state between parts of a run it comes back in another key order, which was the same tokens as a different prompt.
- **Tail:** the research findings (which grow while research runs and then hold), the tools offered only at some steps (`revise_slides` while amending, `edit_deck_with_code` once a rendered file exists), the work completed so far, and the next step.
  Before this the completed work and the step-filtered tool list sat ahead of the design system, so a request's cacheable prefix ended about 12K characters in.
- What a cache could have answered is measured on every run, in every mode, replays included (`app.research.prefix`, `prefix.jsonl` in the run directory, `python -m app.research prefix <run>`): each chat request against every request already answered when it was sent, counting a shared prefix from 1,024 tokens up.
  A replay yields once before each answer, so requests a phase sends together are measured as concurrent, as they are against a real provider.
  A request is entered only once it is answered, so one that raised (a spent budget, a drifted tape) is not counted as input, and it is compared only with requests that carried the same tools.
- On the replay of `probe-footer-not-cover/20261013-061639-claude-code`, the share of chat input a cache could have answered went from 4.8% to 40.9%.
  Steps whose tools differ still share nothing: tools come first, so the change step (two tools) and the closing message (none) cannot reuse each other's prefix.
  Sending every step the same tools and restricting them with OpenAI's `allowed_tools` would remove that, at the price of every request carrying every schema, and is not done here.

## 15. How a run is checked afterwards

The headless runner scores every run with deterministic checks (`app/research/checks.py`).
The ones that report on the decisions above:

| Check | Fails when |
| --- | --- |
| `text_within_the_page` (blocking) | The rendered geometry shows overflow past the page or overlapping text. |
| `headlines_within_the_ramp` | A content headline is set above 48pt. |
| `body_legible` | Body copy on a page measures below 12pt, unless the render reported the page as set small to keep a fixed page count (it passes, and is named). |
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
| `LEGIBLE_TOLERANCE` | 0.25 (charts, diagrams, metrics); 0 for tables | `design/select.py` |
| `MIN_EXHIBIT_IN` | chart 1.6, diagram 1.0, metrics 0.6 | `design/select.py` |
| diagram `LEGIBLE_PT` | 10.0 (= `LEGIBLE_CELL_PT`) | `renderer/diagrams.py` |
| `MIN_LABEL_PT`, `MIN_DETAIL_PT` | 8.0, 7.0 | `renderer/diagrams.py` |
| `SIZE_STEP_PT`, `HEIGHT_PRECISION` | 0.5pt, 1% of the room | `renderer/diagrams.py` |
| `PROBE_HEIGHT_FACTOR` | 8 times the diagram's own shape | `renderer/diagrams.py` |
| `TEXT_MEASURE` | 0.96 | `renderer/diagrams.py` |
| `LEGIBLE_CELL_PT` | 10.0 | `renderer/table_layout.py` |
| table body sizes | 14 to 8pt | `renderer/table_layout.py` |
| `_MIN_COL_FRACTION` | 0.08 | `renderer/table_layout.py` |
| `MIN_SPLITS` | 4 | `design/run.py` |
| figure natural height | 0.34 of the box | `design/authored.py` |
| `PROSE_SHRINK_FLOOR` | 0.35 | `design/authored.py` |
| `DENSITY_SCALE` | airy 0.7, balanced 1.0, dense 1.45 | `design/authored.py` |
| `PLAIN_COLUMN_SHARE` | 0.34 | `design/vocabulary.py` |
| `MIN_SLACK` | 0.08 of the content box | `design/settle.py` |
| `OPTICAL_SHARE` | 0.4 of the free space, above the group | `design/settle.py` |
| `SHORT_PROSE_SHARE`, `LEAD_GAP` | 0.5 of the space under the heading, 0.10 of the content box | `design/settle.py` |
| `W_SMALL_RUNG` | 3.0 | `design/select.py` |
| `PARALLEL_SPREAD` | 2.0 (longest / shortest paragraph) | `design/select.py` |
| `CAPACITY_STARTS` | 12 starting words, fewest kept | `design/setting.py` |
| `COVER_BASELINE`, `SECTION_BASELINE` | 0.93, 0.62 | `design/vocabulary.py` |
| repair `MAX_ROUNDS` | 3 | `design/repair.py` |
| `OVERFLOW_TOLERANCE` | 0.02 | `design/repair.py` |
| `LAYOUT_REVISION_CAP` | 2 per page | `agent/loop.py` |
| `MAX_VALIDATION_ATTEMPTS` | 3 | `agent/loop.py` |
| `RELAXED_FLOOR_PT` | 10.0 | `design/repair.py` |
| `MIN_BODY_PT` (benchmark) | 12.0 | `research/checks.py` |
| compile `MAX_ATTEMPTS` | 2 per slide per round | `agent/compile.py` |
| `compile_concurrency` | 6 requests at once | `config.py` |
| `FULL_REWRITE_ISSUE_RATIO` | 0.6 | `agent/loop.py` |
| authoring `MAX_ATTEMPTS` | 2 | `design/author_run.py` |
| `EDGE_INSET_IN` | 0.3in, or the pack's narrowest margin if smaller | `renderer/deck_ops.py` |
| `SHRINK_STEPS` | 1.0, 0.75, 0.5 of the size asked for | `renderer/deck_ops.py` |
| `TOLERANCE_IN` (overlay collision) | 0.02in | `renderer/deck_ops.py` |
| `GROUND_SHARE` | 0.9 of the page | `renderer/deck_ops.py` |
| `WATERMARK_OPACITY`, `WATERMARK_ROTATION_DEG` | 0.15, -30 | `renderer/deck_ops.py` |
| `TEXT_CONTRAST`, `MARK_CONTRAST` | 4.5, 3.0 | `renderer/deck_ops.py` |
| `CHAR_EM` | 0.68 em per character, sizing a text overlay's box | `renderer/deck_ops.py` |
| `STRADDLE_SHARE` | 0.05 of a frame's area on a second ground before it is seated | `design/grounds.py` |
| `SERIES_CONTRAST` | 3.0, a chart series against a dark ground | `renderer/chart_renderer.py` |

## 17. Evidence log

The runs that motivated rules in this document.
Their cassettes, manifests and check results are committed under [`benchmark/evidence/`](../benchmark/evidence/), so each is reproducible at no cost by replaying it with the headless runner:

```
cd services/ai
PYTHONPATH=. .venv/bin/python -m app.research run ../../benchmark/tasks/<category>/<task>.yaml \
    --mode replay --cassette ../../benchmark/evidence/<task>/<run>/cassette.jsonl
```

A replay feeds the recorded model answers to the current code, so it tests the deterministic engine; a change to a prompt cannot show in a replay.

| Issue | Run | What it showed | Rule |
| --- | --- | --- | --- |
| #306 | 2026-09-24 live runs | An authored cover whose frames overlapped at equal depth was refused at render, and the run shipped nothing. | Gate check 3 (section 4). |
| #312 | `technical-http-caching/20260924-191957-live` | A revision rebuilt a diagram under a new slot name and the page carried it twice. | Rebuilt exhibits replace their predecessors (section 13). |
| #344 | `technical-http-caching/20260924-152618-claude-code` | Three `replace` verdicts re-placed slide 1 identically; `avoided={}` on every recomposition. | Placements travel in the render result (section 13). |
| #345 | `technical-k8s-rollouts/20260924-203834-live` | Seven rejected drafts reported as seven failed tool calls. | Rejections are verdicts (section 13). |
| #348, #312 | `technical-k8s-rollouts/20260924-203834-live` | Slide 2 shipped three stacked copies of its comparison table and text ran off the page; body copy measured 9.8 to 12.2pt on four pages. | Rebuilt exhibits replace their predecessors. |
| #351 | `technical-k8s-rollouts/20260924-203834-live`, replayed as `20260925-091641-replay` | Two slides with a diagram and a table could not be placed in a one-exhibit-slot language; the whole deck fell back to stock. Once composed, tables were drawn as slivers against the page edge, with cells overprinting. | Splitting divides visuals (section 8); legibility floor (section 7); prose yields first (section 3); no bleed for exhibits; table face, columns and rows (section 11); the render-path check. |
| #357 | `technical-k8s-rollouts/20260925-091641-replay` | Five of seven content headlines stepped down to 31.6pt regular by repair, two left at 45pt bold. | One headline rung per deck (section 12); headline length told to drafting (section 14). |
| #364 | `technical-k8s-rollouts/20260925-091641-replay` | Tables were pictures that could not be edited, and a short table in a tall frame was stretched to read as a condensed face. | Tables are native on both paths (section 11). |
| #361 | found by review of this document; reproduced on the 17 languages in `benchmark/evidence/` and `technical-k8s-rollouts/20260924-203834-live` | Capacities were measured in Arial at regular weight as characters-per-line times lines, and an opener's heading was sized as a headline but drawn as display type: text of the advertised length needed up to 3.4 times its frame. Once the heading band was right, a one-line title hung from its top; and a prose cell that lost its row's figure kept the figure's height. | Capacities set as drawn and the rung a slot is set in (section 3); a survivor is cut to its copy and display titles rest on the bottom of their band (section 10). |
| #363 | `technical-k8s-rollouts/20260924-203834-live`, replayed | A four-step flow in a half-width slot broke words mid-word, ran a detail out below its box, and was shrunk box by box by autofit. | Diagram floor measured at 10pt with zero tolerance (section 7); diagrams set whole, at one size, inside their nodes (section 7). |
| #376 | `probe-watermark`, `probe-recolour-accent`, `probe-progress-bar` (2026-10-02) | A `replace` handed back byte-identical pages across rounds once a page had no arrangement left, and swapped two pages' arrangements when only two existed. | Composition reports the slides no other arrangement carries, and their findings go to a redraft (section 13). |
| #376 | `probe-hyperlink-terms/20261004-153956-claude-code` | A script made the linked words pure blue on the dark cover; the ladder re-placed the cover, re-rendered (dropping an earlier repair to slide 3) and then redrafted the cover's copy. | A finding the change's script caused goes back to the change step (section 13). |
| #376 | `probe-progress-bar/20261004-154000-claude-code` | A progress bar was refused twice as an ungrounded chart, because the script's helper was called `bar` and then `draw_bar`; the change step ran out of turns and the closing prompt still asked for the change. | Figures read off the script's syntax; an unmade change is said to be unmade (section 13). |
| #376 | `probe-rotate-exhibit/20261004-154006-claude-code` | A request declined in words was followed by a full validation of the untouched deck. | A decline restores the handover's findings (section 13). |
| #376 | `probe-watermark/20261004-152315-claude-code` | Asked for a watermark, the run redrafted a slide's copy, because the amended deck was validated afresh and the empty lower third it had been handed over with read as a new finding. | Findings the deck was handed over with are reported, not reopened (section 13). |
| #376 | `probe-watermark/20261004-151346-claude-code` | The `amend` backstop's script moved a diagram the last validation had flagged, and the deck was handed over without being checked again, its closing message still naming the finding. | A script edit reopens validation for one more check (section 13). |
| #376 | the nine languages authored in the `probe-*` evidence runs | Five were refused for a derived stacked page that ran a frame off the canvas, though each page as authored fitted; the refusal named a frame the model never wrote. | Unsound derived arrangements are dropped (section 3). |
| #376 | `probe-overlay-picture/20261002-182649-claude-code` | A message after a finished deck restarted the pipeline from intake, so no edit tool was ever offered. | A later message amends the finished deck (section 13). |
| #376 | `probe-footer-not-cover/20261013-061639-claude-code` | The footer request answered with one `apply_deck_operation` call and no reading of the deck: the footer landed on all three pages of a deck that opens on content, clear of the copy, with live page numbers, and the deck was re-rendered and validated with it. It sat 0.3in from the right edge while the copy's margin was 0.85in, and the closing message could not say why a footer meant to skip the cover was on page 1. | Deck-wide operations: side-margin alignment, the no-cover note (section 13). |
| #323 | `technical-http-caching/20261012-162837-claude-code` | Three slides asked for at once in one answer each; two failed a check (a diagram label over 40 characters, copy under its density floor) and were fixed by their one retry; the run took 11 chat calls where the same task took 17 before. | Compile (section 14). |
| #380 | `technical-http-caching/20261012-162837-claude-code` | A last-round `replace` set slide 3's five-sentence body in the `exhibit` arrangement's caption slot, 439 characters where it holds about 175, and the last line ran off the page. | A re-placement must not make the page worse; an alternative must hold the copy (section 13). |
| #350 | `technical-http-caching/20261012-162837-claude-code` | A Mermaid sequence diagram imported with no message arrows, a black mark at its top-left and its note unboxed; on the stacked arrangement it shrank to a third of its slot. | An imported SVG's floor is its smallest label at 10pt (section 7); the import fixes themselves are in `renderer/svg/importer.py`. |
| #378 | the eight `probe-*` decks, 2026-10-04, and `technical-http-caching/20261012-162837-claude-code` | Two sentences beside or above a one-row flow kept the archetype's full heights: the flow floated in a tall frame and the bottom third of the page was empty, on every probe deck, so `validation_converged` failed for spacing alone. | Settling short material (section 10). |
| #392 | `probe-footer-not-cover/20261013-061639-claude-code`, and the 24 admitted evidence languages composing three and nine short sentences and three beside a table | Three sentences under a heading floated mid-page apart from it on every page of the deck. | Short prose alone starts under its heading (section 10). |
| #371 | the 23 languages authored in `benchmark/evidence/`, composing a heading and three points of about 100 characters | On 10 of them one point was set in a caption, footnote or standfirst rung and the other two in the body. | Parallel points share a rung (section 6.3). |
| #359 | found while writing this document | A `nudge` verdict re-renders the identical page, since composition never reads the finding. | `nudge` is answered as a `replace` (section 13). |
| #322 | the 17 languages in `benchmark/evidence/`, via `app.research.gate_audit` | A language could derive and still be unable to show a table legibly or compile a cover. | The compile gate (section 4); the plain-page fallback (section 9). |
| #396 | WTI specimen deck (`language_specimen_slides` on `wti-2025`) | The split-canvas chart straddled the navy panel's edge and was drawn throughout for the white half: title, legend, values and years dark on navy, the first series navy on navy. | One frame, one ground (section 3); an exhibit inked for its ground (section 7). |

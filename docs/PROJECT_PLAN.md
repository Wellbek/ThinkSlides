# ThinkSlides Project Plan

Status: planning complete for iteration 1; decisions D-10 to D-14 recorded.
Last updated: 2026-09-24.
Companion documents: `docs/RESEARCH.md` (source registry, cited as R-xxx) and `docs/CLAUDE_PRESENTATION_WORKFLOW.md`.

This is the rolling plan.
It is updated whenever research materially changes our understanding, and every change to a decision is recorded in section 23.

## Contents

1. Project mission
2. Current architecture
3. Current execution flow
4. Current bottlenecks
5. Root-cause analysis
6. Claude workflow investigation
7. WeKnora analysis
8. JEV/System One and distillation analysis
9. RAG, MCP and tooling analysis
10. Presentation skill architecture research
11. Presentation evaluation methodology
12. Benchmark design
13. Proposed target architecture
14. Proposed agent architecture and model routing
15. Proposed observability model
16. Proposed debugging and research runner
17. Proposed local and API execution architecture
18. Codebase cleanup plan
19. UX improvement plan
20. Implementation roadmap
21. Risks
22. Open questions
23. Decisions
24. Rejected alternatives
25. Completed research
26. Remaining research
27. Next iteration

Appendices: A. Iteration review; B. Version control plan; C. Engineering style rules for implementation.

---

## Status and priorities

Living section, updated whenever the order of work changes.
Last updated: 2026-09-25, during the third implementation session.
Every open item is a GitHub issue labelled `roadmap` and its area; nothing planned lives only in this document.

### Where the project stands

The mission is a demonstrated quality/cost frontier: cheap models with strong orchestration matching frontier workflows on a benchmark.
Measured against that, the project is about **23% done**.
Shares are of the total remaining effort as estimated now; they will move as work reveals its size.

| Area | Share of total | Done within it | Contribution | What is done | What is not |
| --- | --- | --- | --- | --- | --- |
| Research and planning | 5% | 90% | 4.5% | audit, source registry, Claude workflow study, plan, decisions D-1 to D-14 | reference pages of the Slides type, chart fidelity, benchmark licences |
| Measurement and evaluation | 15% | 65% | 9.8% | run traces, headless runner with claude-code / replay / live, spend ledger, 10 tasks, first baseline, automated checks (#314), checklist grading (#315), rejections kept apart from failures (#345), a render-path check (#351) | results store and graphs (#316), category references and judges (#331), OTel (#317) |
| Bugs in the existing pipeline | 10% | 70% | 7.0% | #306, #307, #301, #299, #300, #312, #344, #351 fixed, and #348's overflow; readable exhibits (#354, #355) | #302, #347, #348's caption-sized body, #350, #357, and the older open bug backlog |
| Defect prevention (iteration 2) | 12% | 0% | 0% | - | #318 to #322 |
| New architecture (iterations 3-4) | 33% | 3% | 1.0% | provider injection, the runner's seams | #323 to #328 |
| Visual quality and categories (iteration 5) | 12% | 0% | 0% | - | #303, #329 to #331 |
| Frontier comparison and routing (iteration 6) | 5% | 0% | 0% | - | #332, #333 |
| UX and cleanup | 5% | 15% | 0.8% | minimal versions of the dead controls | #334 to #337 |
| Production hardening | 3% | 0% | 0% | - | #338 |
| **Total** | **100%** | | **about 23%** | | |

### Priority order, and why

Done: #314, #315, #301, #299, #300 (PRs #340 to #343, #346); #312, #344, #345, #351 and #348's overflow (PRs #349, #352 to #356).

1. **#322 and #357**, the composed path's remaining weak points.
   #351 showed that one page an authored language cannot place took the whole deck to the stock renderer; #322 gates a language on compiling every page kind and falls back per page, not per deck.
   #357 is the polish the Kubernetes replay still shows: a hole where a row lost a member, headline weight changing between arrangements, 8pt tables.
2. **#350**, a re-placement onto an arrangement much worse for its exhibit than the one it avoids, and **#347** when short decks matter.
3. **The ten-task baseline** in claude-code mode and on gpt-5.6-luna, scored by #314 and #315.
   It is also the first live test of the exhibit-capacity directive from #356, which replay cannot exercise.
4. **#323 storyline plus parallel compile, with #325 prompt shrinking**: the largest runtime and cost lever, and the core bet of the plan.
   The six-slide run took 75 calls and 847k input tokens ($0.159); this is where that is won back.
5. Iteration 2 prevention (#318 to #321), then evidence (#327, #328), then visual quality, then the frontier report.
   #302 is low priority: only the prompt misreports the headline size.

Spend so far: about $0.27 of the $3 budget, all on luna.

---

## 1. Project mission

Use relatively cheap models, combined with strong orchestration, retrieval, tools, reusable skills, structured generation, visual evaluation and iterative refinement, to produce consistently high-quality presentations across many presentation types, at materially lower cost and ideally lower runtime than frontier-model workflows.

The target is not "a deck that renders".
It is a system whose output has a coherent argument, appropriate research, grounded figures, strong visual hierarchy, domain-appropriate layouts and useful exhibits, with traceable decisions and a demonstrated quality/cost frontier.

What success looks like, stated so it can be measured (section 12):

- On a fixed benchmark, the improved system's quality is not significantly worse than a frontier-model workflow on the same tasks, category by category, at a fraction of the cost.
- Median runtime for a 12-slide deck with research is well below today's roughly 7 minutes of machine time (R-001).
- Zero blocking defects (overflow, overlap, empty or incommensurable charts, unsupported figures in charts) in delivered decks, measured, not assumed.
- Every run can answer why it did what it did and why it stopped.

---

## 2. Current architecture

### 2.1 Services

| Service | Stack | Size | Role |
| --- | --- | --- | --- |
| `apps/web` | Next.js 14, TypeScript, Zustand | ~11.4k lines | Workspace UI, SSE client, settings |
| `services/orchestrator` | Go 1.24, Gin, pgx, Redis | ~4.6k lines incl. tests | HTTP entry point, JWT auth, Redis job queue, SSE hub, worker that calls the AI service |
| `services/ai` | Python 3.11, FastAPI, 2 uvicorn workers | ~61k lines app, ~130 test files | Agent loop, tools, design engine, renderer, evaluation harness |
| PostgreSQL 16 + pgvector | | | System of record; `document_chunks` vector table (unused, see 2.4) |
| Redis 7 | | | Job queue, event pub/sub, working-state checkpoints (1 h TTL) |

### 2.2 AI service module map

| Area | Key modules | What it does |
| --- | --- | --- |
| Orchestration | `agent/loop.py` (5,452 lines), `agent/pipeline.py` | A phase table walked by the loop: intake, scout, consult, design, research, plan, review, draft, render, validate, amend |
| Tools | `agent/registry.py`, `agent/tools/*` (31 tools) | Research, planning, per-slide builders (`build_chart`, `build_table`, `build_diagram`, block builders), `design_slide_content`, render, validate, questions, directions, sandboxed code tools |
| LLM layer | `llm/provider.py`, `llm/catalog.py`, `llm/openai_compat.py`, `llm/usage.py` | Anthropic, OpenAI, Zhipu, Azure providers behind an OpenAI-shaped `chat()`; per-run token counting |
| Design engine | `design/*` (authoring, authored, pack, select, bind, repair, compile, deck_format, vocabulary) | Authored design languages, derived packs of page archetypes, cost-based archetype selection, monotone repair; every decision rule is written up in [`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md) |
| Measurement | `measure/*` | Page and deck metrics, density, grammar, clusters, archetypes |
| Rendering | `renderer/*` | XML-clone renderer for templates, python-pptx path for packs, matplotlib charts (PNG), diagrams, Mermaid, SVG import, previews via LibreOffice and PyMuPDF |
| Grounding | `grounding/*`, `narrative.py`, `titles.py` | Figure-to-span grounding report, outline arc check, title register |
| Evaluation | `eval/*`, `eval_scorecards/` | Style-fidelity scorecard against one reference publication, exemplar library, vision judge |
| Connections | `connections/*` | Encrypted per-workspace MCP, image and figure providers |
| Ingestion | `ingestion/*` | Chunker, embedder, pgvector worker (not called by anything) |

### 2.3 Architecture map

```
Browser ──HTTP──► Next.js (3000) ──rewrite/route──► Orchestrator (8080) ──LPUSH──► Redis queue
   ▲                                                   │  worker goroutine per job (unbounded)
   │ SSE (direct, ?token=)                             ▼
   └──────────── SSE hub ◄── Redis pub/sub ◄── AI service (8000): AgentLoop in-process task
                                                   │
                     ┌─────────────────────────────┼──────────────────────────────┐
                     ▼                             ▼                              ▼
              LLM provider (one model)     Search backend              LibreOffice + PyMuPDF
              chat + vision                native / Tavily / Firecrawl  (render, previews, QA)
                     │
          Postgres (sessions, events, files, templates)   Shared volumes (uploads, templates, output, previews)
```

### 2.4 Facts that shape the diagnosis

- **One model for every step.** `_configure` builds a single chat provider from workspace settings and publishes it to every tool (`run_context.set_chat_provider`); the vision provider is built from the same credentials.
  Intake, planning, copywriting, direction authoring, document summarisation, image selection and closing all use the same model.
- **No prompt caching.** No `cache_control` anywhere; the system prompt is about 33,800 characters (about 8.5k tokens) with the default design system and 24 research findings, rebuilt on every turn with changing sections before the final "Next Step".
- **Drafting is a per-slide ReAct episode.** `_model_turns` offers the slide toolset (up to about 25 tools), and the model calls `build_*` tools, then `design_slide_content`, which makes its own LLM call with a separate large prompt.
  Each slide is a new episode (`messages` rebuilt per phase entry), run sequentially.
- **Research is snippets.** Findings are held as title, URL and a snippet cut to 320 characters (`RESEARCH_SNIPPET_CHARS`).
  With the native OpenAI backend the "snippet" is a slice of the search model's own answer around a citation, not source text (`search_backends._openai_citation`).
- **Documents are stuffed or serially summarised.** `analyze_document` reads PDFs with PyPDF2, truncates or summarises chunk by chunk in a sequential loop; no retrieval.
- **RAG is dead code.** `IngestionWorker`, `embedder.py` and `document_chunks` are never called from the app.
- **Charts are images.** `renderer/chart_renderer.py` draws matplotlib PNGs by deliberate choice (cross-renderer fidelity over editability).
- **Deterministic constraints are delegated to the model.** The requested slide count is passed to the planner, which is asked to self-report `deviations`; no code compares the requested and planned counts (observed 6 requested, 12 planned, empty deviations: R-001).
- **Usage is counted but only logged.** `llm/usage.py` totals tokens per model per run and logs them once; there is no per-call, per-phase or persisted record.
- **Strong existing assets.** A typed design engine where "no model ever emits a coordinate", deterministic WCAG and legibility gates on authored languages, a grounding report, an arc check, an evaluation harness with a held-out split, and 3,466 passing Python tests (plus Go tests and a clean TypeScript typecheck, run 2026-09-24).

---

## 3. Current execution flow

Traced from code, then confirmed against the event log of session `597fd8e0` (R-001).

```
POST /api/sessions/:id/messages (orchestrator)
  -> persist user message + empty assistant row; LPUSH job; 200
  -> worker: BRPOP -> POST AI /api/v1/sessions/:id/messages (fire and forget)
AI: AgentLoop.run
  _load_working_state (Redis)         resume if paused
  _configure                          settings -> one chat provider, vision provider, search config, MCP discovery
  _load_context                       design system (template bundle if any), documents list, chat history
  read user answers from chat         direction choice, colour, outline change, autonomy, deck format, research opt-in
  _resolve_pack                       reinstall chosen design language (ContextVar)
  walk PIPELINE until done or paused:
    intake   (loop)   1 LLM call -> brief JSON (needsResearch, ambiguities, searchQueries, slideCount)
    scout    (loop)   2 web_search calls, sequential                      [Deep mode, research needed]
    consult  (loop)   ask_user_question -> PAUSE                           [unless autonomy confirmed]
    design   (loop)   ensure design system; propose_design_directions:
                      LLM authors 3 languages, deterministic gates, render 3 specimen decks -> PAUSE
    research (model)  up to 10 turns offering web_search                  [Deep mode]
    plan     (loop)   plan_presentation_structure: 1 LLM call -> outline JSON; arc check
    review   (loop)   outline shown -> PAUSE
    draft    (model)  for each outstanding slide, sequentially:
                      up to 6 turns; model picks build_* tools, then design_slide_content (its own LLM call)
    render   (loop)   ground figures (report only); finalize_and_render_pptx -> .pptx + LibreOffice previews
    validate (loop)   validate_rendered_output: LibreOffice PDF geometry + vision per slide (<= 12)
                      issues -> slides_needing_revision -> walk back to draft (cap: 2 revisions)
    amend    (model)  edit_deck_with_code, only if enabled and validation still unhappy at cap
  closing message     1 LLM call
  events -> Redis -> orchestrator SSE hub -> browser; final assistant message saved on done
```

### 3.1 Where the system calls models

| Call site | Model | Count per 12-slide deep run (estimate from code and R-001) |
| --- | --- | --- |
| Intake brief | chat | 1 |
| Native web search (OpenAI Responses) | search model | 4 to 12 |
| Research turns (choosing queries) | chat | 2 to 6 |
| Design direction authoring | chat | 1 to 3 (retries on gate failure) |
| Plan | chat | 1 |
| Draft turns (tool selection) | chat | about 2 per slide (24) |
| `design_slide_content` | chat | 1 per slide per draft or revision (12 to 18) |
| Vision QA | vision | up to 12 per validation round, up to 3 rounds |
| Document summarisation, image selection, key data | chat | per document or image, when present |
| Closing message | chat | 1 |

Roughly 60 to 100 model calls per deck, almost all on the same model.

### 3.2 Where the system retrieves, decides, renders, persists

- **Retrieval**: web search only; no document retrieval; research findings held in Redis-persisted working state and re-injected into every system prompt.
- **What slides to create**: the planner (one call), reviewed by the user.
- **Layouts**: pack archetype selection in code (`design/select.py`) on the design-language path; `select_layout` substring scoring on the template path.
- **Styles**: the chosen direction's authored language, or the template's extracted bundle.
- **Visual assets**: `build_*` tools, `select_source_image`, optional `generate_image` provider.
- **Rendering**: python-pptx or XML clone, then LibreOffice to PDF to PNG.
- **Evaluation**: LibreOffice geometry, output QA rules, per-slide vision verdicts.
- **Retries**: provider-level retry and rate-limit backoff; validation revision cap; phase stall detection (two no-progress passes end the run).
- **Termination**: every phase done; or a pause; or provider failure; or two stalls (with the last rendered deck handed over if one exists).
- **Persistence**: `agent_events` (every event with `raw_event`), messages, generated files, Redis working state (1 h TTL), files on shared volumes.

---

## 4. Current bottlenecks

### 4.1 Measured runtime (R-001, session `597fd8e0`, 12 slides, Deep mode, gpt-4o)

| Stage | Machine time | Share |
| --- | --- | --- |
| Drafting (12 slides, sequential) | 132 s | 32% |
| Render + validate + 2 revision rounds | 121 s | 30% |
| Web search (4 calls, sequential, about 20 s each) | 92 s | 22% |
| Design directions (author 3 + render specimens) | 30 s | 7% |
| Plan | 13 s | 3% |
| Post-validation redraft and re-render (see P-13) | 14 s | 3% |
| Intake and closing | 7 s | 2% |
| **Total** | **about 409 s** | |

Across all five runs, tool time was dominated by `web_search` (433 s total), `validate_rendered_output` (316 s) and `design_slide_content` (261 s).

### 4.2 Bottleneck inventory by category

Each item links to a root-cause entry in section 5.

**Quality**

- Weak research: short snippets, the search model's paraphrase instead of source text (P-4).
- Weak content selection and poor source grounding: charts built from mismatched or invented figures, zero-filled categories (P-1).
- Weak narrative: topic-label titles, generic filler copy (P-7).
- Weak slide decomposition and density control: overflow, crowded pages, slide count ignored (P-2, P-6).
- Repetitive layouts and generated-design tells (P-8).
- Weak diagrams and data visualisation: charts not checked for meaning, not editable (P-1, P-10).
- Hallucination risk is detected (grounding report) but not prevented (P-1, P-4).

**Orchestration**

- Per-slide conversational drafting where a compile step would do (P-3).
- Poor model selection: one model for everything (P-5).
- Weak evaluation loop and stopping criteria: non-converging revisions (P-2).
- Excessive sequential execution (P-3, P-4).
- Deterministic constraints delegated to the model (P-6).

**Runtime**

- Sequential native search at about 20 s per call (P-4).
- Sequential drafting (P-3).
- Whole-deck re-render and re-validate per revision round (P-2).

**Cost**

- 8.5k-token uncached system prompt on every turn (P-5, P-9).
- Frontier-priced model on trivial steps (P-5).
- Research re-sent in full on every call (P-9).

**Engineering**

- A 5,452-line loop module holding orchestration, prompts and state (P-11).
- Dead RAG path, dead UI chain, unwired gRPC contract (section 18).
- Observability: no per-call trace, no persisted usage, no cost view (P-12).
- Evaluation calibrated to one publication (P-10).
- Run durability and concurrency limits (P-14).

---

## 5. Root-cause analysis

Each problem lists observed evidence, likely cause, confidence, impact, possible solutions and the evidence required before implementing.
Problems are ordered by expected impact on the mission.

### P-1 Semantically wrong exhibits ship

- **Evidence**: R-002: bar charts mixing a growth rate, a focus share and zero-filled categories; "Netflix viewing hours (billions)" beside "number of shows" on one axis; an empty chart; stat callouts of unrelated percentages on the conclusion slide.
  Validation passed these, because the vision prompt excludes content and wording (`validate_rendered_output._aesthetic_prompt`) and nothing else inspects chart meaning.
- **Likely cause**: (a) the drafting model is asked to build a chart when the plan says `contentType: chart`, even when the evidence holds no coherent series (commit `0494191` strengthened exactly that instruction); (b) evidence is snippets, so the model assembles whatever numbers it can find; (c) no chart-semantic validation exists.
- **Confidence**: High for (c); Medium for (a) and (b).
- **Impact**: Very high: a wrong chart is the most credibility-destroying defect in a business deck.
- **Possible solutions**: a deterministic chart lint on the typed chart spec (one unit per axis, no mixed quantity kinds, no zero-filled placeholders, minimum series length, categories drawn from one dimension, every value bound to an evidence span); demote a planned chart to text when the lint fails; evidence-bound data extraction before charting.
- **Evidence required**: run the lint over existing generated decks and the regression fixtures to measure false-positive rate; confirm that `build_chart` arguments carry enough typing (units, series names) to lint.

### P-2 Geometry defects are found late and fixed by the wrong mechanism

- **Evidence**: R-001: 3 of 5 runs ended at the revision cap with unresolved issues; in session `597fd8e0` the same three overflow findings (slides 3, 9, 12) survived three rounds; each round cost about 32 s of render and validation plus redrafting.
  R-002: clipped titles and title/body collisions.
- **Likely cause**: capacity is advisory.
  Copy is written without a hard fit check against the slot's measured capacity, the renderer lays it out, and LibreOffice geometry detects overflow afterwards.
  The fix sent back is "shorten the copy", but the defects were titles (a structural capacity problem) and exhibit/text collisions (a binding problem), which prose rewriting does not reliably fix.
- **Confidence**: Medium-High.
- **Impact**: High on quality (visible defects) and runtime (about 30% of machine time).
- **Possible solutions**: fit-before-render using font metrics (the Slides type does this with arithmetic: R-102); on a fit failure, re-bind to a roomier archetype or split the slide deterministically, and only then ask a model to shorten a specific field with a character budget; validate and re-render only changed slides; stop when a round does not reduce blocking defects.
- **Evidence required**: measure how often pre-render fit estimates agree with LibreOffice geometry on the regression decks (`tools/measure_typesetting.py` exists for this); verify fonts are metric-compatible in the renderer container (R-101 safe-font finding).
- **Update (R-005)**: the first headless run found a larger cause than capacity: the revision loop never asks for the fixes (#299).
  With most of the deck flagged, the redraft prompt carries none of the findings, only the first slide is redrafted, the `replace` rung re-renders an identical page, and the walker keeps drafting after the cap.
  Separately, the drafting prompt reports the display size (57.6 pt) as the title size; the rendered titles measure 39.6 pt, so this misleads the copy rather than causing the clipping (#302, corrected by measurement).

### P-3 Drafting is sequential and conversational

- **Evidence**: R-001: about 11 s per slide, 132 s for 12 slides, of which `design_slide_content` accounted for about 44 s; the rest is loop turns in which the model chooses tools.
  Code: one ReAct episode per slide with the full system prompt, run in sequence (R-003).
- **Likely cause**: the architecture treats each slide as an open-ended agent task.
  After the plan exists, a slide is a function of its plan entry, its evidence and the design pack, so tool choice is mostly already decided by `contentType`.
- **Confidence**: High.
- **Impact**: High on runtime and cost; Medium on quality (repeated re-derivation produces inconsistency).
- **Possible solutions**: one structured-output call per slide that returns the slide's full typed content (copy plus exhibit specs), with the tool validators run as deterministic checks on that output; all slides in parallel with a shared cached prefix; the agent loop remains only for the genuinely open-ended phases.
- **Evidence required**: A/B on the benchmark (parallel compile versus current drafting) for quality parity per category; check provider rate limits for N concurrent calls.

### P-4 Research is slow and thin

- **Evidence**: R-001: 20.6 s mean per `web_search` on the native OpenAI backend, run sequentially; R-003: 320-character snippets, which on the native backend are the search model's own answer text; the Korean deck's sources include hashed and truncated URLs (R-002).
- **Likely cause**: native search is an LLM call that writes an answer and then cites; queries are issued one per model turn; no page fetch, no extraction, no store.
- **Confidence**: High.
- **Impact**: High on grounding quality; about 22% of runtime.
- **Possible solutions**: a query plan made in one call, executed in parallel against a search API that returns source text (Tavily or Firecrawl are already integrated), page fetch and clean extraction for the top results, chunking into a session evidence store with span IDs, per-slide retrieval, and citations drawn from spans.
- **Evidence required**: latency and result-quality comparison of backends on the benchmark topics; cost of page fetches; check that span-level grounding (`grounding/claim.py`) works on fetched text.

### P-5 One model does everything, without caching

- **Evidence**: R-003: a single provider for all calls; no prompt caching; 8.5k-token system prompt per turn; the Anthropic default model in the catalog is `claude-opus-5` with a 16k `max_tokens` floor.
- **Likely cause**: provider abstraction built around "the model the user chose in Settings"; routing was never a design goal.
- **Confidence**: High.
- **Impact**: High on cost; Medium on runtime.
- **Possible solutions**: per-step model roles (section 14) configured per workspace with sane defaults; cache-stable prompt layout (static skill text and tool schemas first, run-level context next, per-call payload last); Batch API for non-interactive benchmark runs.
- **Evidence required**: the benchmark's model-size sweep per step; cache hit rates from traces.

### P-6 Checkable constraints are left to the model

- **Evidence**: R-001: 6 slides requested, 12 planned, empty deviations; code relies on self-report (`plan_presentation_structure.py` rule text).
- **Likely cause**: planning output is not validated against the brief.
- **Confidence**: High.
- **Impact**: Medium-High: users notice immediately.
- **Possible solutions**: the plan-validate-execute pattern (R-103): the brief's explicit constraints become typed fields; a validator compares plan to brief and either re-plans with the specific violation or records a deviation itself.
- **Evidence required**: none beyond a regression test from the observed case.
- **Update (R-005)**: the planner prompt itself contradicts the count: `Target Slide Count: 3` beside the boardroom format's "Plan roughly 10-18 slides", plus unconditional title and conclusion slides (#300).

### P-7 Weak narrative and topic titles

- **Evidence**: R-002: about half the titles are topic labels; generic filler ("This presentation will explore..."); the arc check (`narrative.py`) flags this per slide but only as advice in the drafting directive.
- **Likely cause**: the storyline is generated by the same model in one call with a very broad prompt, then the drafting turn writes titles again; there is no structured "what does this slide claim, on what evidence" artefact.
- **Confidence**: Medium.
- **Impact**: High for consulting, executive and technical decks (R-410); lower for keynotes and posters.
- **Possible solutions**: a storyline artefact with an action title, the claim, the evidence references and the exhibit intent per slide, written by a stronger model once; title register enforced by format; checklist items in the benchmark.
- **Evidence required**: benchmark comparison of storyline model sizes; human spot-check of title quality.

### P-8 Generated-design tells and repetitive composition

- **Evidence**: R-002: accent rules under every title (R-101 calls them "a hallmark of AI-generated slides"); cream background with high-contrast serif (R-104 trait 1); the same composition on most pages; serif body with sans headings mixed within one deck.
- **Likely cause**: authored languages are gated for contrast, legibility and derivability, not for known generated-design defaults; archetype selection optimises fit, not variety.
- **Confidence**: Medium-High.
- **Impact**: Medium-High on perceived quality.
- **Possible solutions**: a deterministic deck lint with the anti-default list; a variety term across the deck in archetype selection; per-format palettes and type pairings as skill data.
- **Evidence required**: run the lint over existing decks; judge pairwise before/after.

### P-9 Knowledge is one monolithic prompt

- **Evidence**: R-003: 34k-character system prompt with rules for every phase, the full design system JSON, and all research on every turn; contradictory lines (for example "Call extract_template_design_system" when no template is attached, although no phase offers that tool).
- **Likely cause**: accretion of fixes into prose.
- **Confidence**: High.
- **Impact**: Medium on quality (instruction dilution), High on cost.
- **Possible solutions**: versioned prompt templates per step; presentation skills loaded per phase and per format (section 10); research passed as a retrieved slice, not the whole store.
- **Evidence required**: token accounting before and after; benchmark parity.

### P-10 Evaluation is calibrated to one publication

- **Evidence**: R-004: the style reference is a 184-page McKinsey report; `words_per_slide` target 195.
- **Likely cause**: the epic that built it targeted consultancy-grade decks.
- **Confidence**: High.
- **Impact**: Medium now, High once optimisation starts, because optimising all categories toward a report makes pitch and keynote decks worse.
- **Possible solutions**: category-conditional reference sets (section 12); keep the split discipline and the "judge outranks metric" rule.
- **Evidence required**: licence-clean exemplar sources per category.

### P-11 Orchestration code is hard to change safely

- **Evidence**: `loop.py` at 5,452 lines mixes the phase walker, prompts, dispatch, preconditions, working state and event shaping; `WorkingState` is untyped and persisted as JSON; `TOOL_PHASES` duplicates pipeline labels.
- **Likely cause**: rapid iteration.
- **Confidence**: High.
- **Impact**: Medium: slows every later change and hides coupling (issue #290 was a ContextVar not reinstalled on resume).
- **Possible solutions**: extract prompt assembly, dispatch, state and phases into modules along the target architecture's seams, as part of the iterations that change them, not as a big-bang refactor.
- **Evidence required**: none; covered by the existing test suite.

### P-12 The system cannot explain itself

- **Evidence**: usage is a single log line per run; `agent_events` records tool calls and durations but not model calls, prompts, token counts per call, cache hits, decision reasons or termination reasons.
- **Likely cause**: observability grew from the UI's needs (SSE events).
- **Confidence**: High.
- **Impact**: High on the ability to do any of this plan with evidence.
- **Possible solutions**: section 15.
- **Evidence required**: none.

### P-13 Work continues after the deck is declared final

- **Evidence**: R-001: in session `597fd8e0`, `deck_finalized` and `file_generated` fire at 11:38:23, then a `validate_slide_json`, `build_diagram`, `design_slide_content` and a fourth render follow, with no validation, before the closing message.
- **Cause (reproduced, R-005)**: `_slides_outstanding` ignores the validation cap and `draft` precedes `validate` in `PIPELINE`, so once validation is settled at its cap the walker redrafts the flagged slide again, re-renders, and skips validation as settled (#299).
- **Confidence**: High.
- **Impact**: Medium: the delivered file differs from the validated one.
- **Possible solutions**: treat the cap as ending revision; make the render after a cap-settled validation impossible without a validation.

### P-14 Run durability and admission control

- **Evidence**: the orchestrator starts a goroutine per job with no concurrency limit; the AI service runs the agent as an in-process task inside 2 uvicorn workers alongside CPU-heavy LibreOffice conversions; a restart loses in-flight runs except for paused working state.
- **Likely cause**: prototype deployment model.
- **Confidence**: Medium (not load-tested).
- **Impact**: Low now; High at multi-tenant scale.
- **Possible solutions**: a bounded worker pool, rendering in a separate worker process, durable run state keyed by run ID.
- **Evidence required**: a load test in a later iteration.

---

## 6. Claude workflow investigation

Full analysis: `docs/CLAUDE_PRESENTATION_WORKFLOW.md`.
Summary of what matters for the plan:

1. Anthropic ships two workflows.
   The `pptx` skill generates code and requires structural validation plus visual QA of every slide with one targeted fix round (R-101).
   The Slides type writes a declarative, flow-laid-out deck in one pass from a settled outline and forbids verification by default, relying on fit arithmetic and "split, never shrink" (R-102).
2. The quality visible at a glance (clean layout, consistency, no overflow, no generated-design tells) comes largely from deterministic structure and explicit rules; narrative judgement comes from the model.
   This is a strong inference, not a measurement.
3. Knowledge is packaged as short instructions plus scripts, loaded progressively, and grows as failure-mode lists (R-101 history, R-103).
4. Charts are native and dressed; the only images are for chart types PowerPoint cannot express (R-101, R-106).

Consequence for the plan: keep our constrained representation; enforce capacity before render; compile slides in parallel after a strong-model storyline; move design knowledge into data and linters; aim the critic at semantic defects.

---

## 7. WeKnora analysis

WeKnora (R-301) is a knowledge-base Q&A platform.
Our problem is narrower: turn a brief, a handful of documents and web research into grounded slides within minutes.

| Concept | What WeKnora does | Problem it solves | Do we have it? | Decision |
| --- | --- | --- | --- | --- |
| Multi-format parsing with OCR and VLM captions | PaddleOCR-VL, anydoc office parser, image descriptions | Getting clean text and figures out of real documents | PyPDF2 text only; images catalogued | **Adapt**: layout-aware PDF extraction (tables, figures with captions), VLM captions only for figures we might reuse |
| Adaptive and parent-child chunking | Small chunks for recall, parent sections for context | Precise retrieval without losing context | No chunking in the live path | **Adopt** (simplified): section-aware chunks with a parent pointer |
| Hybrid retrieval (BM25 + dense) | Multi-way recall with reranking | Recall for exact figures and names (BM25) and paraphrase (dense) | None | **Adopt** (simplified): Postgres full-text plus pgvector in one query; rerank only if the benchmark shows need |
| GraphRAG | Entity graph over documents | Multi-hop questions across a large corpus | No | **Reject** for now: a deck's corpus is small and the questions are known per slide |
| Many vector backends | Milvus, Qdrant, Elasticsearch, and so on | Enterprise deployment choice | pgvector exists | **Reject**: pgvector is sufficient at our scale |
| Knowledge base product (folders, FAQ, wiki, chunk editing) | Curated long-lived KBs | Organisation knowledge management | No | **Defer**: a project-level source library is in scope later (section 9), a KB product is not |
| ReAct agent with MCP and sandboxed skills | General agent over KB and tools | Open-ended Q&A | Yes, in our own form | **Reference**: confirms MCP and sandboxed skills as the ecosystem pattern |
| Parsing trace timeline and Langfuse tracing | Stage-by-stage visibility | Debugging ingestion and agent loops | No | **Adopt** the idea (section 15); tracing backend choice is open (question Q-6) |
| Retrieval evaluation (hit rate, BLEU, ROUGE) | Pipeline evaluation | Knowing retrieval works | No | **Adapt**: retrieval hit rate on benchmark source packs; BLEU/ROUGE rejected as quality measures for slides |

---

## 8. JEV/System One and distillation analysis

### 8.1 JEV / System One models (R-501)

What it is, per the vendor: a fast, non-autoregressive model that returns schema-typed values with calibrated confidence, for classification, selection, routing and guardrails, not for text generation.

**Possible integration points**, each a typed decision the pipeline already makes or should make:

| Decision | Current mechanism | Output type | Value if JEV works |
| --- | --- | --- | --- |
| Deck format from the brief | regex over chat plus intake LLM | enum | Low: already cheap |
| Does the brief need research | intake LLM field | bool with confidence | Low-Medium |
| Slide content type (chart, table, diagram, text) | planner LLM | enum per slide | Medium: many calls, calibrated confidence could gate escalation |
| Chart type for a data series | drafting LLM | enum | Medium |
| Source relevance for a slide | none (all research in prompt) | score per chunk | Medium-High: high cardinality, latency-sensitive |
| Defect triage (which fix rung) | vision LLM verdict | enum | Medium |
| Escalation (is this output good enough for a cheap model to keep) | none | bool with confidence | Potentially High, if calibration holds |

- **Expected benefits**: lower latency and cost on high-count classification steps; calibrated confidence as an escalation signal.
- **Risks**: early access, single vendor, unreplicated benchmarks built by the vendor; "0% hallucination" is schema conformance, not correctness; another external dependency and key.
- **Implementation complexity**: Low if every such decision already sits behind a typed `Classifier` interface; the interface is worth having regardless.
- **Evidence supporting a decision**: none independent yet.
- **Decision**: evaluate later, after deterministic and cheap-LLM baselines exist for the same decisions, on our benchmark.
  Requires an early-access key, which is a stop-and-ask item.

### 8.2 Distillation and compression (R-414, R-402, R-412, R-413)

- **CTCD (R-414)** co-trains a large and a small encoder model on GLUE; it presumes we train both models.
  We consume hosted APIs and train nothing, so it does not apply directly.
- **AutoPresent (R-402)** is the relevant evidence: an 8B model fine-tuned on 7k instruction-to-code pairs with a high-level slide library matched GPT-4o on single-slide generation.
  The analogue for us is the slide-compile step, whose inputs and outputs are typed and whose quality can be checked deterministically.
- **Cascades and routing (R-412, R-413)** reduce cost without training when a cheap model's failure is detectable.
  Our compile step has detectable failure (schema, fit, grounding, chart lint), which makes a cascade natural.

**Decision**: no distillation now.
Record every compile-step input and output in traces from iteration 1 so that a distillation or fine-tuning experiment is possible later without new data collection.
Start with static per-step routing, then a cascade with deterministic escalation signals, then learned routing only if traces show it would help.

---

## 9. RAG, MCP and tooling analysis

### 9.1 Where retrieval pays off

1. **Per-slide evidence slices.** Each slide gets the few chunks relevant to its claim instead of every snippet the run found.
   This improves grounding and cuts tokens.
2. **User documents.** Long documents are retrieved from, not stuffed or serially summarised.
3. **Design and example retrieval.** Exemplar slides and archetypes retrieved by material shape and format (a small, curated library; not a vector search problem at first).
4. **Project memory.** Previously used sources, brand rules and approved decks for the same project.

### 9.2 Source lifecycle

```
input -> registration -> storage -> extraction -> indexing -> retrieval -> attribution -> downstream access -> persistence
```

| Stage | Design |
| --- | --- |
| Input | Uploaded files; pasted text and URLs in the prompt (automatically detected and registered, per mission); web search results; MCP resources; template files |
| Registration | A `Source` record: ID, kind (upload, pasted text, URL, web result, MCP), origin, content hash, fetched-at, licence or access note, scope |
| Storage | Raw bytes on the uploads volume keyed by hash; extracted text and figures in Postgres |
| Extraction | Layout-aware text with page and section anchors; tables as structured rows; figures with captions; URLs fetched and cleaned |
| Indexing | Section-aware chunks with parent pointers; Postgres full-text and pgvector embeddings; every chunk has a stable span ID |
| Retrieval | Hybrid query per slide from its claim and evidence intent; top-k with a token budget |
| Attribution | Every figure and quote in a slide references span IDs; `grounding/check.py` verifies values against spans; citations rendered from span metadata, never from model-typed URLs |
| Downstream access | Storyline gets source summaries and key facts; slide compile gets its slice; critic gets the slice for grounding checks |
| Persistence | Sources and chunks persist with the session; promoted to project scope on user action |

### 9.3 Scope levels

| Level | What lives there | Decision |
| --- | --- | --- |
| Request | Web results and pasted sources for one message | Yes, as the default scope for research |
| Presentation (session) | Everything registered while building one deck | Yes: the unit that revisions and edits reuse |
| Project | Sources, brand rules, approved decks shared across decks for one client or topic | Later: requires a project concept in the product |
| Global | Curated design knowledge, exemplars, archetypes | Yes, but as skill data (section 10), not as retrieved user content |

Pasted sources are registered at request level and attached to the session automatically.

### 9.4 MCP

MCP is already supported for workspace connections (R-108).
Its role in the target architecture:

- **Inbound (adopt)**: MCP servers are source adapters.
  Their resources and tool results are registered as `Source`s and flow through the same extraction and attribution path, so connector content is cited like any other source.
- **Internal (reject)**: MCP is not the internal tool bus; in-process typed functions are simpler and faster.
- **Outbound (defer)**: exposing ThinkSlides as an MCP server (create deck, revise slide) is a product decision for later.

### 9.5 External tools worth adding

| Tool | Purpose | Decision |
| --- | --- | --- |
| Search API returning source text (Tavily, Firecrawl; both integrated) | Replace native LLM search on the hot path | Adopt as default for Deep mode, subject to benchmark |
| Page fetch and clean extraction | Full text for grounding | Adopt |
| Layout-aware PDF extraction (for example pdfplumber or PyMuPDF text blocks, already a dependency) | Tables and sections | Adopt; choose by benchmark |
| OOXML structural validator (like R-101's) | Catch files PowerPoint rejects | Adopt |
| Native chart writer (python-pptx charts) | Editable charts | Evaluate (fidelity benchmark) |

---

## 10. Presentation skill architecture research

### 10.1 Is a skill system justified?

Yes, for a specific reason: most of what distinguishes a good pitch deck from a good conference talk is knowledge (structure, register, density, exhibit conventions, anti-patterns), and that knowledge currently lives as prose scattered through `loop.py`, tool prompts and `deck_format.py`.
Packaging it lets us load only what a step needs (R-103), version it, test it, and grow it from observed failures as R-101 does.

It is not justified as a runtime where a model browses a filesystem and runs scripts.
Our pipeline knows which step it is in and which format the deck is, so it can load the right skill parts itself.
This is the key adaptation: **progressive disclosure driven by the orchestrator, not by the model**.

### 10.2 Proposed structure

```
services/ai/skills/
  _shared/
    design-rules.yaml          anti-default lint rules, type-scale rules, contrast rules
    chart-conventions.yaml     chart type by question, dressing rules, chart lint rules
    diagram-grammar.yaml       node/edge/group vocabulary and layout rules (section 11.5)
  boardroom/
    SKILL.md                   short: audience, register, storyline patterns, density, title grammar
    storyline.md               narrative patterns (situation-complication-resolution, pyramid)
    archetypes.yaml            preferred page archetypes and variety constraints
    checklist.yaml             QA and benchmark checklist items for this format
    exemplars/                 reference page IDs for the judge and for archetype retrieval
  pitch/ ...
  academic-talk/ ...
  technical-architecture/ ...
  research-poster/ ...
  product/ ...
  keynote/ ...
```

- **Instructions** (`SKILL.md`, under about 1k tokens): what only a model can use.
- **Data** (`*.yaml`): what code enforces (lint rules, archetype preferences, capacities, checklist items).
- **Examples**: few, curated, used for retrieval and judging, not pasted wholesale into prompts.
- **Scripts**: none inside skills; enforcement code lives in the pipeline and reads skill data.
- **Evaluators**: each skill's `checklist.yaml` feeds both the critic and the benchmark, so the same definition of "good" drives generation and measurement.

### 10.3 What the skill system does not do

- It does not replace the design engine: packs, archetypes and selection stay in `app/design`.
- It does not choose steps: the pipeline does.
- It does not let arbitrary third-party skills run code in the service (security).

### 10.4 Evidence still needed

- A small experiment in iteration 3: the same storyline step with the monolithic prompt versus a skill-loaded prompt, compared on the benchmark (quality, tokens).

---

## 11. Presentation evaluation methodology

### 11.1 Principles

1. **No single score.** Report dimensions separately; plot frontiers per dimension (R-004's style/substance separation is kept).
2. **Category-conditional.** Each category has its own reference set and its own checklist; a consulting deck is not judged against conference norms.
3. **Deterministic first.** Anything a program can check is checked by a program.
4. **Judges compare, programs measure.** Vision and LLM judges are used for pairwise preference and defect detection, with position swapping and a different model family from the generator where possible (R-411).
5. **Checklists beat holistic scores** for content (R-404, R-403).
6. **Humans calibrate.** A small human-rated set per category validates each automated instrument; human agreement is itself only moderate (Fleiss' kappa 0.59 in R-401), so we report agreement, not ground truth.

### 11.2 Dimensions and instruments

| Dimension | Deterministic instrument | Model instrument | Human |
| --- | --- | --- | --- |
| Layout integrity (overflow, overlap, clipping, margins) | LibreOffice geometry, fit estimator, margin checks | vision defect detector | spot-check |
| Visual hierarchy | type-scale adherence, size contrast ratio, title position consistency | pairwise vision preference | calibration set |
| Typography | fonts count, sizes count, minimum size, metric-safe fonts | | |
| Composition and consistency | archetype variety, repeated-element position drift, whitespace distribution, alignment grid adherence | pairwise | calibration set |
| Information density | words, blocks and ink per page against the category's reference distribution | | |
| Visual complexity | element count, colours used, exhibit count per page | | |
| Anti-default lint | rule hits from `design-rules.yaml` | | |
| Narrative and storytelling | arc check (`narrative.py`), assertion-title share, section structure | instance checklist items | calibration set |
| Research quality | distinct sources, source type mix (primary versus aggregator), recency | | |
| Factual grounding | share of figures bound to spans; value match; unsupported figures in charts (must be 0) | claim verification against spans | spot-check |
| Chart quality | chart lint (units, dimensions, zero-fill, series length, type fit), dressing rules | | |
| Diagram quality | grammar lint (section 11.5) | pairwise | |
| Audience and task fit | | instance checklist items (R-405 audience coverage) | calibration set |
| Editability | native charts share, text as text, placeholders used | | |
| Instruction compliance | slide count, requested sections, exclusions | checklist items | |

### 11.3 Category reference distributions

The existing McKinsey profile becomes the consulting reference only.
Each category needs 3 to 10 licence-clean exemplar decks, split into design and held-out validation pages as R-004 already does.
Candidate sources: public investor presentations (pitch, corporate), public conference talk slides with permissive licences (academic, technical), government and statistics-office briefings (executive), SlidesBench decks if the licence allows (R-402).

### 11.4 Why these methods

- Assertion titles and visual evidence improve comprehension in technical settings (R-410), so "assertion-title share" is a real quality signal for those categories, not taste.
- LLM judges are biased but useful when used pairwise with swapping (R-411) and decoupled from extraction (R-403).
- Instance checklists capture what the requester actually asked for, which generic rubrics miss (R-404).

### 11.5 Diagram grammar for publication-quality technical figures (R-415)

Observed in DeepSeek-V3's figures and adopted as lint rules and generator constraints:

| Principle | Rule for our diagram generator |
| --- | --- |
| Grouping | Subsystems are containers with a title; containers nest at most two levels |
| Overview plus detail | A node may expand into a detail panel connected by leader lines; never both in one cramped box |
| Colour discipline | At most three category fills plus neutral; every fill has a legend entry or an obvious label |
| Flow | One primary direction per diagram; orthogonal connectors; crossings minimised by layout |
| Labelling | Nodes are nouns, edges carry short verbs or data names in small italics; no sentences in boxes |
| Repetition | Repeated structures drawn as identical parallel units with the varying label only |
| Density | Node count bounded per diagram (for example 12 to 15), otherwise split or zoom |
| Notation | Symbols and units consistent with the text |
| Whitespace | Uniform gaps from the pack's spacing scale |

The generator should take a typed graph (nodes, edges, groups, emphasis) from the model and do layout deterministically (Graphviz, ELK or the existing primitives), rather than ask the model to draw.

---

## 12. Benchmark design

### 12.1 Goals

Compare models, model sizes, orchestration strategies and systems on quality, efficiency and reliability, and produce the graphs listed in 12.7.

### 12.2 Task suite

- **Categories (8)**: consulting/strategy, executive briefing, startup pitch, investor update, technical architecture, academic conference talk, research poster, product presentation.
- **Per category**: 6 to 10 tasks, split into a development set and a held-out set.
- **Two task modes**:
  - **Source-pack tasks**: fixed documents and fixed web snapshots supplied with the task, which removes research variance and makes grounding checkable.
  - **Open-research tasks**: live research allowed; used to measure research quality and end-to-end runtime.
- **Each task file** (`benchmark/tasks/<category>/<id>.yaml`): brief, audience, constraints (slide count, sections), source pack references, instance checklist (10 to 50 binary items), category reference set ID.
- **Consultation**: benchmark runs answer the pipeline's questions from the task file (scripted answers), so pauses do not block and runs stay comparable.

### 12.3 Conditions

| Axis | Levels |
| --- | --- |
| System | current `main` (baseline), each roadmap iteration, the Anthropic `pptx` skill run in Claude Code on the same task (D-11), manual Claude Slides or PowerPoint add-in comparison (small subset, if terms allow) |
| Model per role | cheap, medium, frontier, per step (section 14), and "all frontier" and "all cheap" corners; the frontier corner is run through `claude-code` mode (D-12), cheap and medium through live APIs within the budget (D-10) |
| Orchestration | current agent loop; compile-in-parallel; with and without visual critique; 0, 1 or 2 revision rounds |
| Research backend | native, Tavily, Firecrawl (open-research tasks) |
| Repeats | 3 runs per condition per task at first; more where variance is high |

### 12.4 Metrics

Quality: the dimensions in 11.2, each reported separately.
Efficiency: wall time, per-phase time, model calls, input, cached-input and output tokens per model, tool calls, retrieval time, render time, estimated cost (tokens times a price table snapshot stored with the report).
Reliability: failure rate, retry rate, malformed-output rate (schema failures per call), convergence rate (runs ending with zero blocking defects), revision rounds used, judge variance (repeat judgements), run-to-run variance.

### 12.5 Raw data storage

```
benchmark/
  tasks/<category>/<task>.yaml
  sources/<pack-id>/...               fixed documents and web snapshots
  references/<category>/...           reference profiles and exemplar IDs (not the licensed files)
  runs/<run-id>/                      one directory per run, gitignored, archived
    manifest.json                     task, condition, git SHA, model IDs, prompt and skill versions, price table ID
    trace.jsonl                       spans (section 15)
    deck.pptx, deck.pdf, pages/*.png
    checks.json                       deterministic results
    judgements.jsonl                  every judge call with inputs hashes and outputs
    scores.json                       per-dimension scores
  results.duckdb                      one row per run and per (run, dimension); rebuilt from runs/
  reports/<date>-<name>.md            generated tables and figures
```

Raw runs are never overwritten; reports are regenerated from raw data.

### 12.6 Statistics

- Paired comparisons per task (same task, two conditions) with bootstrap confidence intervals over tasks.
- Pairwise judge results as win rates with position-swapped double judging; ties allowed.
- No composite score unless a later iteration finds a weighting validated against human preference.

### 12.7 Graphs the benchmark must produce

Quality versus cost, quality versus runtime, cost versus quality per category, model size versus quality per role, agent or call count versus quality, call count versus runtime, cost reduction versus baseline, defect rate versus revision rounds.
Each quality graph is drawn per dimension, never on a merged score.

### 12.8 Attribution design (model versus system)

A 2x2 per task: {our system, Claude workflow} by {cheap model, frontier model}.
With no Anthropic API key (D-11), the frontier cells come from Claude Code: our pipeline in `claude-code` mode, and the `pptx` skill run directly in Claude Code.
The Claude workflow has no cheap-model cell, so that corner stays empty.
Holding the system fixed isolates model capability; holding the model fixed isolates system capability.

---

## 13. Proposed target architecture

### 13.1 Pipeline

```
Brief
  │ Intake: parse constraints (deterministic) + classify format/research need (cheap model)
  ▼
Consult (at most one pause, questions only where answers change the deck)
  ▼
Evidence ──────────────────────────────────────────────────────────────┐
  │ query plan (cheap model, 1 call) → parallel search → parallel fetch  │
  │ → extraction → chunking → hybrid index (session evidence store)     │
  ▼                                                                     │
Storyline (stronger model, 1 structured call, validated against brief)  │
  │ per slide: action title, claim, evidence refs, exhibit intent        │
  ▼                                                                     │
Outline review (pause)                                                  │
  ▼                                                                     │
Design language (existing authoring + gates; or template bundle)        │
  ▼                                                                     │
Slide compile × N  (parallel; cheap model; schema-constrained) ◄────────┘ retrieval per slide
  │ deterministic gates: schema, grounding, chart lint, capacity fit (split or re-bind), deck lint
  ▼
Layout binding (design/select.py)  →  Render (python-pptx / XML clone)  →  OOXML validation
  ▼
Rasterise changed pages  →  Critique (deterministic checks, then vision on changed pages)
  ▼
Targeted repair × k (parallel, cheapest effective rung)  →  stop rule  →  Export + trace + scores
```

### 13.2 Separation of concerns

**Deterministic infrastructure**: brief constraint parsing, source registry, extraction, chunking, indexing, plan validation, schema validation, grounding checks, chart lint, deck lint, capacity fit, archetype binding, rendering, OOXML validation, rasterisation, geometry checks, stop rules, tracing, cost accounting.

**LLM reasoning**: brief classification and questions, research query planning, storyline, slide compile (copy and exhibit specs), targeted rewrites under explicit budgets, design-language authoring, closing summary.

**Retrieval**: evidence store queries per slide and per storyline section; exemplar and archetype lookup by material shape.

**Visual evaluation**: geometry checks on rendered PDF; vision critique of changed pages for arrangement and legibility defects; benchmark-time pairwise judging.

**Orchestration**: the phase table (keep `pipeline.py`'s idea), a parallel executor with concurrency limits, budgets, and escalation rules; no model decides the next phase.

**Presentation rendering**: the existing renderer, extended with native charts if the fidelity benchmark allows, and a diagram layout engine for typed graphs.

### 13.3 Answers to the design questions

| Question | Answer |
| --- | --- |
| Major components | Intake, source registry and evidence store, storyline planner, design-language author, slide compiler, gates, layout binder, renderer, critic, repair, orchestrator, trace store, benchmark runner |
| Which are agents | Only research query planning may iterate (a bounded loop: plan, search, assess coverage, one follow-up round); everything else is a single call or deterministic |
| Which are deterministic | Everything in 13.2's deterministic list |
| Which use models | Intake classification, consultation wording, query planning, storyline, compile, rewrites, authoring, vision critique |
| Which models for which tasks | Section 14 |
| Where retrieval is used | Storyline (source summaries and key facts), compile (per-slide slices), critic (grounding), exemplar lookup |
| Where MCP is used | Inbound source adapters only |
| Where skills are used | Per-format knowledge loaded into intake, storyline, compile and critic prompts; skill data read by gates and lints |
| Where style systems live | Design language (authored) or template bundle, derived into a pack; skill data supplies per-format defaults and anti-defaults |
| Internal representation | Brief, Source/Chunk/Span, Storyline (slides with claims and evidence refs), SlideSpec (typed copy and exhibit specs), Pack/Archetype binding, rendered page geometry, Trace |
| Where rendering happens | After compile and gates; again only for changed slides |
| Where visual evaluation happens | After render, on changed pages only |
| Where refinement happens | Targeted repair of flagged slides; at most two rounds |
| What runs in parallel | Searches, fetches, extraction, embedding, the three design directions, slide compile, per-slide gates, per-slide render and rasterise where the renderer allows, per-page critique, repairs |
| What is cached | Prompt prefixes (skills, schemas, deck-level context); search and fetch results by query and URL; extraction by content hash; embeddings by chunk hash; rendered pages by slide content hash |
| What is persisted | Sources, chunks, storyline, slide specs, pack binding, renders, traces, scores |
| Where cheap models are used | Classification, query planning, compile, rewrites, critique triage |
| Where stronger models are used | Storyline; design-language authoring (to be benchmarked); escalations |
| What triggers escalation | A compile output failing the same gate twice; low-confidence classification; a storyline failing validation twice; category flagged as high-stakes by the user |
| What causes convergence | Zero blocking defects after gates and critique |
| What causes termination | Convergence; or two repair rounds; or a round that does not reduce blocking defects; or budget (tokens, time, calls) exhausted; each recorded with evidence |
| How unnecessary iterations are avoided | Prevention before render (fit, lint, grounding), repairs only for blocking classes, only changed slides re-checked, no-improvement stop |

### 13.4 Blocking versus advisory defects

Blocking (must be fixed or the slide is simplified): overflow, overlap, clipped text, chart lint failure, unsupported figure in a chart or stat callout, empty exhibit, contrast below floor, slide count violation.
Advisory (reported, fixed only if cheap): topic title in a format that prefers assertions, low variety, density outside the category range, anti-default lint hits.

---

## 14. Proposed agent architecture and model routing

### 14.1 Roles

| Role | Kind | Calls per deck | Parallel |
| --- | --- | --- | --- |
| Intake | single call + deterministic parse | 1 | no |
| Researcher | bounded loop (max 2 rounds) | 2 to 4 model calls + parallel tools | tools yes |
| Storyliner | single structured call (+1 retry on validation failure) | 1 to 2 | no |
| Designer | existing authoring (3 languages) | 1 to 3 | yes |
| Compiler | single structured call per slide | N | yes |
| Repairer | single structured call per flagged field or slide | 0 to k | yes |
| Critic | vision call per changed page, after deterministic checks | 0 to N | yes |
| Summariser | single call | 1 | no |

Only the researcher is an agent in the sense of R-202; the rest are workflow steps.

### 14.2 Model routing hypothesis

To be confirmed or refuted by the benchmark's per-role sweep.

| Task | Deterministic | Small/cheap | Medium | Frontier | Vision | Basis |
| --- | --- | --- | --- | --- | --- | --- |
| Constraint extraction (count, sections, exclusions) | primary | fallback | | | | P-6 |
| Format and research-need classification | regex first | primary | | | | cheap, typed |
| Source filtering and relevance | BM25 and dense scores | rerank if needed | | | | R-301 |
| Summarisation of long sources | | primary | | | | extraction-style |
| Figure extraction to typed values | parsers for tables | primary | escalate | | | checkable |
| Research query planning | | primary | | | | R-203 |
| Narrative and storyline | | | primary | escalate | | section 8 of the workflow doc |
| Slide decomposition | inside storyline | | primary | | | |
| Layout selection | primary (`select.py`) | | | | | existing engine |
| Design-system generation | gates | | primary | | | to benchmark |
| Visual asset selection | | primary | | | assist | |
| Diagram spec (graph) | layout engine | primary | escalate | | | typed graph |
| Slide compile | validation | primary | escalate | | | R-402, R-102 |
| Validation | primary | | | | | |
| Visual critique | geometry first | | | | primary (cheap vision) | R-101 |
| Final review | checklist | | | | optional | |

### 14.3 Escalation and convergence rules

- A compile output that fails the same gate twice is retried once on the next tier with the gate's message.
- A storyline that fails plan validation twice is escalated.
- Vision critique runs only on pages that changed and passed deterministic checks.
- Each repair targets one field with an explicit budget (for example "title, at most 48 characters").
- The run stops when blocking defects reach zero, or after two repair rounds, or when a round fails to reduce blocking defects, or at budget; the reason is recorded.

---

## 15. Proposed observability model

### 15.1 Span model

Every unit of work is a span in one tree per run: `run` > `phase` > `step` > (`llm_call` | `tool_call` | `retrieval` | `render` | `check`).

| Field | Content |
| --- | --- |
| `span_id`, `parent_id`, `run_id` | tree identity |
| `kind`, `name`, `role` | what it is (for example `llm_call`, `compile_slide`, `compiler`) |
| `reason` | why it was invoked (phase rule, gate failure ID, escalation rule, user answer) |
| `inputs_ref` | hashes and IDs of inputs (brief, storyline entry, chunk IDs, pack ID), not raw bodies |
| `retrieved` | chunk and span IDs with scores |
| `tools_available`, `tools_used` | for model calls that may call tools |
| `model` | provider, model ID, parameters |
| `prompt_ref` | template ID, template version, skill versions, prompt hash; full prompt stored separately in debug mode |
| `output_ref` | output hash; typed output stored for compile and storyline |
| `evaluation` | gate results, lint hits, critic verdicts |
| `errors`, `retries` | error classes and counts |
| `timing` | start, end, queue wait |
| `usage` | input, cached input, output tokens |
| `cost_estimate` | computed at report time from a price table snapshot |
| `termination` | for phases and runs: condition met, iteration cap, budget, no-improvement, user pause, failure |
| `convergence` | blocking-defect counts per round |

### 15.2 Questions the trace must answer

| Question | How |
| --- | --- |
| Why was this agent invoked? | `reason` on the span |
| Why was another not invoked? | Skipped phases emit a zero-duration span with the rule that skipped them |
| Why did the system stop? | Run-level `termination` |
| Did it converge? | `convergence` series ends at zero |
| Did it hit a budget or iteration limit? | `termination` value |
| Was the quality threshold reached? | Blocking-defect count and advisory counts at stop |
| What evidence caused the next iteration? | The repair span's `reason` points at the gate or critic finding IDs |

### 15.3 Storage

- Postgres table `run_spans` (JSONB payload) for the product, linked to `agent_events` for the UI.
- JSONL export per run for the research runner and benchmark.
- Full prompts and responses stored only in debug and benchmark modes, with a retention policy, because they contain user content.
- Decided (D-13): the own store above is the source of truth and works with nothing else running.
  An optional OpenTelemetry exporter (GenAI semantic conventions) sends the same spans to Langfuse when configured.
  Langfuse is self-hosted through an opt-in Docker Compose profile, off by default; Langfuse Cloud would send prompts off the machine and needs a key, so it is a stop-and-ask item.

---

## 16. Proposed debugging and research runner

### 16.1 Why it exists

The UI path requires the orchestrator, Redis, Postgres, a browser and human answers to questions.
That makes runs slow to start, hard to reproduce and impossible to batch.
Debugging, benchmarking, regression testing, orchestration analysis and cost analysis all need the same thing: run the pipeline headless, deterministically where possible, and get a complete, structured trace.

### 16.2 Design

- **Entry point**: `python -m app.research <command>` in `services/ai`, building on `tools/e2e.py` and `tools/render_deck.py`.
- **Commands**:
  - `run <task.yaml> [--mode claude-code|replay|record|live] [--models <profile>] [--out runs/]`
  - `phase <run-id> <phase>`: re-run one phase from a saved run's state (for example re-compile slide 4 only)
  - `show <run-id>`: human-readable timeline with timings, tokens, decisions and termination reason
  - `diff <run-a> <run-b>`: side-by-side slides, checks, costs and timings
  - `bench <suite> --conditions <file>`: fan out runs and write `results.duckdb`
- **Isolation**: in-memory working state and event sink instead of Redis and SSE; SQLite or a disposable Postgres schema for sources; scripted answers for consultation pauses.
- **Outputs**: exactly the `runs/<run-id>/` layout in 12.5, plus `trace.md` (human-readable) and `prompts/` in debug mode.

### 16.3 What it enables

- Reproducing P-13 and every future bug outside the UI.
- Regression tests that replay recorded model responses at zero cost.
- Per-phase optimisation without re-running earlier phases.
- The benchmark itself.

---

## 17. Proposed local and API execution architecture

### 17.1 Execution modes

| Mode | Model calls | Cost | Use | Comparable to live? |
| --- | --- | --- | --- | --- |
| `live` | real providers | real | benchmarks, final validation | yes |
| `record` | real providers, responses saved as cassettes keyed by request hash | real, once | building regression fixtures | yes |
| `replay` | cassettes only; a cache miss fails loudly | zero | regression tests, refactors | quality yes, latency no |
| `claude-code` | each request written to a file; a Claude Code session reads it and writes the response (D-12) | Claude subscription, no API billing | development, validation before any paid run, the frontier-model reference | quality as a frontier reference only; latency and cost no |
| `stub` | deterministic fakes (existing test fakes) | zero | unit tests | no |

Every trace records its mode, and reports never mix modes in one comparison.

### 17.2 The `claude-code` mode (D-12)

Local runs feed model requests directly to a Claude Code session, so they are paid by the Claude subscription rather than API billing.

- **Provider**: a `ClaudeCodeProvider` implementing the same `chat()` interface as the API providers.
  Each call writes `runs/<run-id>/llm/<seq>.request.json` (role, step, messages, tool schemas, response schema, image paths for vision calls) and blocks until `<seq>.response.json` appears, in the OpenAI-shaped `{message, tool_calls}` the loop already consumes.
- **Driving it**: the research runner starts in the background; the Claude Code session watches the request folder, answers each request as the model, and writes the response file.
  Vision requests carry rendered page paths, which the session reads as images.
- **Accounting**: request tokens are counted locally with the tokenizer of the API model the step would use in `live` mode, so every `claude-code` run also yields a projected API cost for that step.
- **Recording**: responses are saved as cassettes, so a `claude-code` run can be replayed at zero cost by the `replay` mode.
- **Limits, stated so nobody misreads results**:
  - The answers come from the session's model (a frontier model), so quality measured this way is the frontier reference, not the cheap model's quality.
  - Latency and throughput are not comparable to API runs; each request waits on a session turn.
  - Call volume matters: the current per-slide loop makes 60 to 100 calls per deck, which is slow to serve by hand, so `claude-code` validation starts with small decks (3 to 6 slides) and becomes cheaper once slide compile (iteration 3) cuts the call count.
  - It is for development and validation volumes, not bulk benchmarking through the subscription.

### 17.3 Budget policy (D-10)

API budget: $3 available now, up to $20 if topped up.
Credits are spent only on real validations of something that has already passed in `claude-code` mode.

- **Ledger**: the runner keeps one cumulative spend ledger per machine (`~/.local/share/thinkslides/ledger.jsonl`, outside any checkout so branches cannot each see the whole budget; tokens times the dated price table in `benchmark/prices.json`) and refuses to start a `live` run whose projected cost would exceed the remaining budget.
- **Projection first**: a step's cost is projected from its `claude-code` token counts before any live call.
- **Order of spend**: the smallest task that validates the change (one 3- to 6-slide deck, Simple mode), on the cheapest model that is supposed to serve that step, one run at a time.
- **Rough scale (unverified prices, to be checked before spending)**: token counts from R-003 suggest one current-architecture 12-slide run is on the order of 0.5M input tokens, which at a gpt-4o-class price is roughly one to two dollars.
  So the current $3 covers about one full baseline run of today's pipeline, not a benchmark.
  The baseline in iteration 1 is therefore measured mainly in `claude-code` mode, with one small live run to anchor tokens and latency.
- **Native search**: the native OpenAI search backend is itself a model call per query and is excluded from paid validation runs; Tavily (key present) is used instead.

### 17.4 Credentials and external services

Observed configuration (names only, values not read): `.env` has `ZHIPU_API_KEY` and `TAVILY_API_KEY`; the workspace settings table holds an encrypted OpenAI key used with `gpt-4o` (chat) and `gpt-5.6-terra` (native search).
No Anthropic API key is configured.
No API was called during this planning phase.

| Needed for | Service | Status | Action |
| --- | --- | --- | --- |
| Live validation and cheap/medium conditions | OpenAI | key present | spend only under the budget policy (17.3) |
| Cheap Chinese-language condition | Zhipu | key present | optional |
| Research backend comparison | Tavily | key present | confirm plan limits |
| Research backend comparison | Firecrawl | not configured | stop and ask |
| Claude-model conditions and the `pptx` skill comparator | Anthropic API with code execution | declined (D-11) | served by `claude-code` mode and the `pptx` skill in Claude Code instead |
| Classifier comparison | TypeSafe Jev early access | not configured | stop and ask |
| Commercial comparators | Claude Slides, Claude for PowerPoint, NotebookLM | subscriptions and terms | stop and ask |

Implementation stops for an explicit decision before any step that needs a missing key, new credits beyond the ledger, or a paid service.

---

## 18. Codebase cleanup plan

No code is changed in this phase.
Categories: **remove**, **refactor**, **retain**, **investigate**.

| Item | Evidence | Category | Why |
| --- | --- | --- | --- |
| `apps/web/src/components/layout/AppShell.tsx` and its chain `chat/ChatArea.tsx`, `chat/ChatMessageList.tsx`, `chat/ChatInput.tsx`, `chat/TemplateSelector.tsx`, `preview/SlidePreviewSidebar.tsx`, plus `GeneratedFileCard.tsx` and `Sources.tsx` if only the chain imports them | Import graph: `AppShell` is imported by nothing | remove | Dead UI; misleads readers about which UI is live |
| `apps/web/src/hooks/useChat.ts`, `components/workspace/TimerDisplay.tsx` | Imported by nothing | remove | Dead code |
| `services/ai/app/ingestion/*` and `document_chunks` | Never called from the app | investigate | Revive inside the evidence store (iteration 4) or remove; do not keep both |
| `services/orchestrator/proto/agent.proto` | Not wired (README says so) | remove | Unused contract |
| `TOOL_PHASES` in `loop.py` | Duplicates pipeline labels | refactor | One source of truth |
| System prompt's "Call extract_template_design_system" line | That tool is in no phase toolset | refactor | Prompt and toolset disagree (P-9) |
| `loop.py` (5,452 lines) | Mixed concerns | refactor | Along target seams, incrementally (P-11) |
| `WorkingState` | Untyped, persisted as JSON | refactor | Typed state with versioned serialisation |
| Hardcoded `redis://redis:6379` in `api/generate.py` and `agent/loop.py` | Ignores `settings.redis_url` | refactor | Configuration consistency |
| `PyPDF2==3.0.1` listed twice in `requirements.txt`; PyPDF2 is deprecated | requirements file | refactor | Move to a maintained extractor as part of iteration 4 |
| `datetime.utcnow()` usages | 129 deprecation warnings in the test run | refactor | Fix with the next change to those files |
| `SimplePresentationRequest` endpoint | Legacy direct-render path | investigate | Keep only if a caller exists |
| `docs/architecture-diagram-prompts.md` | States 12 tools and "no image generation" | refactor | Stale: 31 tools, `generate_image` exists |
| README "Design Decisions" on images | Same staleness | refactor | Documentation accuracy |
| Composer and preview controls with no behaviour (section 19) | No handlers | done: minimal versions marked beta or in development (D-14) | UI honesty |
| `design/*`, `measure/*`, `grounding/*`, `narrative.py`, `titles.py` | Core of the target architecture | retain | The strongest assets |
| `eval/*` harness | Sound methodology | retain, extend | Becomes the consulting category of the benchmark |
| `llm/usage.py`, `agent/redact.py`, `llm/openai_compat.py` | Useful, focused | retain | |
| Sandboxed code tools (`edit_deck_with_code`, `compute_visual_with_code`) | Off by default; security-sensitive | investigate | Keep behind the flag until the benchmark shows they earn their risk |
| matplotlib chart images | Deliberate choice | investigate | Native charts benchmark (Q-2) |
| Orchestrator worker concurrency (unbounded goroutines) | `processor.go` | investigate | Admission control before multi-tenant load |
| `build.log`, `recent_log.log`, `.projectatlas/` at repo root | Untracked local files | retain (local) | Not in git; consider `.gitignore` entries |

---

## 19. UX improvement plan

Based on a code-level audit of the live workspace UI (`WorkspaceShell`, `ChatFlow`, `ActivityFeed`, `Composer`, `SlideStage`, `QuestionCard`) and the observed session transcripts.
An end-to-end visual walkthrough is scheduled for the next iteration (section 27); findings here are to be confirmed there.

| Priority | Finding | Evidence | Proposal |
| --- | --- | --- | --- |
| 1 | Controls that did nothing: "Mention slide", "Code mode", "Share", the preview's mark, pin and draw tools, "Send mark to chat", hard-coded "WK" avatars | No handlers; mark rendered a static pin | Done in a minimal form (D-14); the full versions (server-side slide targeting, shared links, drawing) remain on the roadmap |
| 1 | Up to four round trips before any slide exists (autonomy or consult, design, outline review) | Session transcripts (R-001) | One consultation card that carries format, research, angle and design choice together; outline review stays |
| 1 | Defects delivered silently in the deck | 3 of 5 runs unresolved (R-001) | Per-slide status in the preview (clean, advisory, unresolved) with the reason |
| 2 | Progress is a feed of tool events | `ActivityFeed.tsx` | Phase-level progress with slide-level completion, time elapsed and remaining estimate from traces |
| 2 | Source visibility | Sources shown as citations; raw hashes in the appendix (R-002) | A sources panel with title, publisher, date and which slides use each source; clean appendix |
| 2 | Slide count and other explicit constraints can be ignored | P-6 | Show the brief's parsed constraints in the outline review, with any deviation stated by the system |
| 3 | Hard-coded avatar initials | `WorkspaceShell.tsx` | Use the signed-in user |
| 3 | Missing presentation configuration: audience, length, format, brand as explicit inputs rather than inferred from chat | Composer | Optional structured fields on the home view |
| 3 | Error states for provider failures and degraded connections are text in the feed | Loop events | Actionable error cards (retry, change model, open settings) |

### 19.1 Minimal in-development controls (D-14, implemented 2026-09-24)

| Control | What it does now | Marked as | What the full version needs |
| --- | --- | --- | --- |
| Mark and pin tools (preview) | Click the slide to drop numbered points; points are per slide | "Beta" tag, tool tooltips | Server-side slide targeting so the agent receives structured regions |
| Send mark(s) to chat | Writes "On slide N (mark 1 at x% across, y% down; ...)" into the composer, then clears the marks | Disabled until a mark exists | Same |
| Draw tool | Disabled | "Draw - in development" tooltip | Freehand annotation |
| Mention slide (@) | Inserts `@slide N` for the slide open in the preview | Disabled until the deck has slides | Server-side parsing of mentions |
| Code mode | Appends a visible request to prefer code-level fixes; still gated by the server's `code_amendment_enabled` | "Code mode (beta)" chip | A real mode flag carried to the loop |
| Share | Popover explaining shared links are in development, with the deck download | "In development" tag | Per-session URLs, access control, links without the user's token |
| Avatars | Signed-in user's initials instead of a hard-coded "WK" | | |

The download link is deliberately not offered for copying: it carries the user's JWT as `?token=`.
Also fixed while checking these in the running UI: the "Run complete ... finished in 4m 42s" divider overflowed a narrow chat panel; it now wraps.

---

## 20. Implementation roadmap

Iterations are ordered by expected impact and by dependency: measurement first, because every later claim needs it.
Each iteration is a branch with small commits (Appendix B).

### Iteration 1: Measurement foundation

- **Objective**: make every run explainable and comparable; re-baseline current `main`.
- **Motivation**: P-12; R-001's caveat that observed defects may predate same-day fixes; R-203 ("full production tracing").
- **Research basis**: R-203, R-301 (tracing), R-004 (harness discipline).
- **Expected impact**: no user-visible change; unlocks evidence for every later decision.
- **Scope**: span model and trace store with the optional OpenTelemetry exporter (section 15, D-13); per-call usage with cached tokens; research runner with `claude-code`, `replay`, `record` and `live` modes and the spend ledger (sections 16 and 17); benchmark task schema; 2 categories x 5 tasks (consulting, technical) with source packs and checklists; deterministic checks already available (geometry, grounding, arc, output QA) wired into `checks.json`; baseline report measured in `claude-code` mode plus one small live anchor run.
- **Dependencies**: none beyond the existing OpenAI key; live spend stays within the ledger (D-10).
- **Risk**: Low.
- **Validation**: a 3-slide task completes end to end in `claude-code` mode; replaying its cassettes reproduces the same deck except timestamps; the trace answers the section 15.2 questions; only then one small `live` run on the cheapest configured model, with its cost recorded in the ledger.
- **Benchmark impact**: creates the baseline.
- **Complexity**: Medium.
- **Rollback**: tracing is additive and behind a setting.

### Iteration 2: Prevent the worst defects

- **Objective**: zero blocking defects in delivered decks.
- **Motivation**: P-1, P-2, P-6, P-8.
- **Research basis**: R-101 (validator, anti-defaults, one fix round), R-102 (fit before render, split not shrink), R-103 (plan-validate-execute), R-104.
- **Scope**: chart lint and evidence binding for chart values; capacity fit at compile time with deterministic split or re-bind; plan-versus-brief validator; deck lint from `design-rules.yaml`; OOXML structural validator; repairs targeted per field with budgets; re-render and re-validate only changed slides; no-improvement stop.
- **Dependencies**: iteration 1.
- **Risk**: Medium (false positives in lint could over-simplify decks).
- **Validation**: blocking-defect rate on the benchmark; false-positive audit on 50 slides.
- **Benchmark impact**: large drop in blocking defects; validation time down.
- **Complexity**: Medium.
- **Rollback**: each gate behind a flag.

### Iteration 3: Compile instead of converse

- **Objective**: cut drafting time and cost without quality loss.
- **Motivation**: P-3, P-5, P-9.
- **Research basis**: R-102 (one pass after a settled outline), R-202 (workflows over agents), R-107 (cache-stable prompts), R-402.
- **Scope**: storyline artefact with action titles, claims, evidence references and exhibit intents; one structured compile call per slide in parallel with bounded concurrency; per-role model configuration with defaults; cache-stable prompt layout; first two format skills (boardroom, technical) as data plus short instructions.
- **Dependencies**: iterations 1 and 2 (gates are what make cheap compile safe).
- **Risk**: Medium (quality regression on narrative consistency across parallel slides).
- **Validation**: paired benchmark against iteration 2 per category; per-role model sweep.
- **Benchmark impact**: drafting time from about 130 s towards the latency of one call; token cost down.
- **Complexity**: High.
- **Rollback**: the current draft phase stays available behind a setting until parity is shown.

### Iteration 4: Evidence store and parallel research

- **Objective**: grounded content and faster research.
- **Motivation**: P-4, P-1.
- **Research basis**: R-203 (parallel search), R-301 (hybrid retrieval, parent-child chunks), R-405 (correctness against sources).
- **Scope**: source registry and lifecycle (section 9.2) including pasted sources; query plan plus parallel search and fetch; extraction and chunking; Postgres full-text plus pgvector; per-slide retrieval; span-based citations and a clean source appendix; revive or delete `ingestion/`.
- **Dependencies**: iteration 1; benefits from 3.
- **Risk**: Medium (extraction quality, fetch failures, licence and robots considerations).
- **Validation**: grounding rate, unsupported figures, retrieval hit rate on source-pack tasks, research time.
- **Benchmark impact**: grounding up; research time down.
- **Complexity**: High.
- **Rollback**: per-backend flags.

### Iteration 5: Visual quality and category coverage

- **Objective**: better-looking, better-fitting decks across categories.
- **Motivation**: P-7, P-8, P-10.
- **Research basis**: R-101, R-104, R-410, R-415, R-401.
- **Scope**: remaining format skills; archetype variety across a deck; diagram grammar with typed graphs and deterministic layout; native chart evaluation and adoption if fidelity allows; category reference sets and pairwise vision judging in the benchmark; human calibration set.
- **Dependencies**: iterations 1 to 3.
- **Risk**: Medium.
- **Validation**: per-category pairwise win rates against iteration 4; human calibration agreement.
- **Complexity**: High.
- **Rollback**: per-skill and per-renderer flags.

### Iteration 6: Frontier comparison and routing optimisation

- **Objective**: demonstrate the quality/cost frontier.
- **Scope**: frontier conditions through `claude-code` mode and the `pptx` skill in Claude Code (D-11, D-12); cascade escalation tuned from traces; optional Jev evaluation; report with the section 12.7 graphs.
- **Dependencies**: iterations 1 to 5; budget top-up decision (D-10); credentials (section 17.4).
- **Complexity**: Medium.

### Later

UX iteration (section 19) runs alongside iterations 2 to 4 where it touches the same surfaces (per-slide status with iteration 2, sources panel with iteration 4).
Slide-scoped editing, project-level sources, run durability and admission control (P-14), and distillation of the compile step (section 8.2) follow once the above is measured.

---

## 21. Risks

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Cheap models cannot write acceptable storylines | Medium | High | Storyline on a medium or strong model by design; benchmark per role |
| Parallel compile loses cross-slide consistency | Medium | Medium | Storyline carries the shared context; deck lint for repetition; one consistency pass if needed |
| Lint false positives degrade decks | Medium | Medium | Advisory versus blocking split; false-positive audits |
| LibreOffice geometry differs from PowerPoint | Medium | Medium | Metric-safe fonts (R-101); occasional PowerPoint spot checks |
| Benchmark measures the wrong thing | Medium | High | Human calibration set; category references; checklists; judge outranks metric rule |
| Judge bias toward its own family | Medium | Medium | Cross-family judging; position swap (R-411) |
| Provider rate limits under parallelism | Medium | Medium | Bounded concurrency; backoff already exists |
| Research fetch legal and robots issues | Low-Medium | Medium | Respect robots; store extracted text for the session only; cite |
| Scope creep into a knowledge-base product | Medium | Medium | Section 7 rejections recorded |
| Evidence from R-001 is stale | High | Low | Iteration 1 re-baselines first |
| API budget ($3, up to $20) too small for statistically useful live comparisons | High | Medium | `claude-code` mode for development and the frontier reference; `replay`; source-pack tasks; small decks; cheapest models; ledger with a hard stop (D-10) |
| `claude-code` results misread as cheap-model quality | Medium | High | Mode recorded on every trace; reports never mix modes (17.2) |

## 22. Open questions

| ID | Question | How to resolve |
| --- | --- | --- |
| Q-1 | Does fit-before-render agree with LibreOffice geometry well enough to skip most visual checks? | Iteration 2 measurement on regression decks |
| Q-2 | Native charts or images? | Fidelity benchmark in LibreOffice and PowerPoint; product decision on editability |
| Q-3 | Which model tier does the storyline need per category? | Iteration 3 sweep |
| Q-4 | Is design-language authoring worth its 30 s and three calls per deck versus a curated theme library? | Pairwise benchmark in iteration 5 |
| Q-5 | ~~How are local runs executed?~~ Resolved: `claude-code` mode (D-12) | |
| Q-6 | ~~Tracing backend?~~ Resolved: own store plus optional OpenTelemetry to self-hosted Langfuse (D-13) | |
| Q-7 | Should the consultation be one pause? | UX study with transcripts; product decision |
| Q-8 | What caused the post-final redraft in P-13? | Reproduce in the research runner |
| Q-9 | Which licence-clean exemplar decks per category? | Research task |
| Q-10 | Is the vision critic worth its cost once deterministic gates exist? | Iteration 2 ablation |

## 23. Decisions

| ID | Date | Decision | Basis |
| --- | --- | --- | --- |
| D-1 | 2026-09-24 | Keep the constrained representation where no model emits geometry | R-102, R-406, existing engine |
| D-2 | 2026-09-24 | Measurement and re-baselining come before any optimisation | P-12, R-001 caveat |
| D-3 | 2026-09-24 | Slides are compiled by single structured calls after a storyline, not drafted by per-slide agents (to be validated in iteration 3) | P-3, R-102, R-202 |
| D-4 | 2026-09-24 | Deterministic gates run before any visual critique; vision only on changed pages | R-101, P-2 |
| D-5 | 2026-09-24 | No single composite quality score | R-004, R-411, R-404 |
| D-6 | 2026-09-24 | Skills are orchestrator-loaded data plus short instructions, not a model-browsed runtime | R-103 adapted |
| D-7 | 2026-09-24 | Evidence store in Postgres (full-text plus pgvector); no GraphRAG, no new vector database | R-301 analysis |
| D-8 | 2026-09-24 | No distillation now; record compile traces for later | R-414, R-402 |
| D-9 | 2026-09-24 | JEV evaluated only behind a classifier interface after baselines exist | R-501 |
| D-10 | 2026-09-24 | API budget is $3 now, up to $20; credits are spent only on real validations of work already validated in `claude-code` mode, enforced by a ledger | Product decision |
| D-11 | 2026-09-24 | No Anthropic API key; Claude comparisons run through Claude Code (our pipeline in `claude-code` mode, and the `pptx` skill) | Product decision |
| D-12 | 2026-09-24 | "Local runs through Claude Code" means a `claude-code` provider mode that hands each model request to a Claude Code session, billed to the subscription | Product decision; design in 17.2 |
| D-13 | 2026-09-24 | Tracing: own simple store as source of truth, optional OpenTelemetry export to self-hosted Langfuse | Product decision |
| D-14 | 2026-09-24 | Non-functional UI controls get minimal working versions, clearly marked beta or in development, rather than being removed | Product decision |

## 24. Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Free-form code generation per deck (pptxgenjs or python-pptx scripts written by the model) as the main path | Returns geometry to the model, which is the defect class we are trying to remove; requires a render-inspect loop on every deck (R-101); keeps the sandbox risk on the hot path |
| HTML-to-PPTX conversion as the representation | Anthropic moved away from it (R-101 history); our typed spec plus pack already gives flow-like guarantees without a browser in the loop |
| Multi-agent hierarchy for drafting | About 15x tokens for multi-agent research (R-203); drafting is not open-ended once the storyline exists |
| GraphRAG and a dedicated vector database | Small per-deck corpora; pgvector suffices (section 7) |
| A single "quality score" to optimise | Encourages metric gaming; categories disagree (R-004) |
| Distillation or co-training now | No training infrastructure; no data yet; CTCD's setting does not transfer (R-414) |
| Big-bang rewrite of `loop.py` | Risky; refactor along seams as each iteration touches them |

## 25. Completed research (this iteration)

- First headless run in `claude-code` mode (R-005), with its replay reproducing identical pages; five defects filed (#299 to #303).

- Repository audit of all three services, the generation path, the design engine, the renderer, the evaluation harness and the frontend (R-003).
- Historical run telemetry and rendered-output inspection from the local volumes (R-001, R-002).
- Test health: 3,466 Python tests pass (6 skipped), Go tests pass, TypeScript typecheck clean (2026-09-24).
- Anthropic `pptx` skill: full source and history (R-101); Slides type instructions (R-102); Skills documentation (R-103); design skills (R-104); cookbook (R-105); Claude for PowerPoint docs (R-106); prompt caching (R-107).
- Anthropic engineering writeups on skills, effective agents and multi-agent research (R-201 to R-203).
- WeKnora (R-301); JEV (R-501); CTCD (R-414); DeepSeek-V3 figures (R-415).
- Slide-generation literature: PPTAgent, AutoPresent, SlidesGen-Bench, PresentBench, X+Slides, SeaSlides, ACE, DECKBench, DeepSlides (R-401 to R-409).
- Presentation comprehension research (R-410); LLM-as-judge biases (R-411); routing and cascades (R-412, R-413).

## 26. Remaining research

- Slides type reference pages (`craft.md`, `layout.md`, `diagrams.md`, `diagram-recipes.md`), which require creating a private deck (R-102).
- Native chart fidelity in LibreOffice versus PowerPoint.
- Licence review of external benchmarks and exemplar decks per category.
- Search backend comparison (latency, text quality, cost) on benchmark topics.
- Layout-aware PDF extraction options compared on real documents.
- Current model prices at benchmark time.
- End-to-end UI walkthrough with screenshots to confirm section 19.
- Reproduction of P-13 on current `main`.

## 27. Next iteration

Planning iteration 2 (still no production changes unless authorised):

1. Walk the UI end to end with the existing harness (dev server on 3001, synthetic events, screenshots) and confirm or revise section 19.
2. Draft the benchmark task schema and write the first 10 tasks (consulting and technical) with source packs and checklists, as documentation.
3. Specify the span schema and trace table as a design document for review.
4. Specify the `claude-code` provider's request and response file format (17.2) and the ledger format (17.3).

Then, once implementation is authorised, start roadmap iteration 1.

---

## Appendix A. Iteration review

### What we know

- A 12-slide deep run took about 7 minutes of machine time; drafting (32%), the render-validate loop (30%) and search (22%) dominate (R-001).
- Validation did not converge in 3 of 5 runs, and identical geometry findings survived all rounds in the traced run (R-001).
- The planner ignored an explicit slide count and reported no deviation (R-001, code).
- Delivered decks contained semantically wrong charts, clipped and colliding titles, topic titles and known generated-design tells (R-002).
- One model serves every step, with an uncached 8.5k-token system prompt per turn (R-003).
- Research evidence is 320-character snippets, and on the native backend those are the search model's own words (R-003).
- The RAG path is unused (R-003).
- Anthropic's two public workflows either inspect every rendered slide or avoid inspection by constraining representation and checking fit arithmetically (R-101, R-102).
- The test suites are healthy.

### What we strongly suspect

- Most visible quality gaps against Claude are system gaps (constraints, lint, chart semantics, fit), not model gaps.
- Parallel compile after a strong storyline can cut drafting time by an order of magnitude at equal quality.
- Deterministic gates will remove most of the validation loop's runtime.
- Evidence-bound charting will remove the worst chart defects.

### What remains uncertain

- How much narrative quality a cheap model can deliver when handed a strong storyline.
- Whether pre-render fit estimation is accurate enough across fonts.
- Whether native charts render faithfully enough in LibreOffice to adopt.
- Whether design-language authoring beats a curated theme library.
- Whether JEV-class classifiers help.

### Highest-impact changes

1. **Measurement foundation**: without it no other claim in this plan can be checked, and R-001 may be stale.
2. **Blocking-defect prevention** (chart lint with evidence binding, fit before render, plan-versus-brief validation): addresses the most visible and most credibility-damaging failures, and removes about 30% of runtime spent in a loop that did not converge.
3. **Compile instead of converse**: the largest runtime and cost lever after research; supported by R-102 and R-202.
4. **Evidence store and parallel research**: the grounding quality ceiling and 22% of runtime.

### Major risks

Cheap-model narrative quality; parallel-compile consistency; lint false positives; benchmark validity.

### First implementation iteration

Roadmap iteration 1 (section 20): the trace store and span model, per-call usage accounting, the research runner with `live`, `record` and `replay` modes, the benchmark task schema with 10 tasks in two categories, and a re-baselined report of current `main`.
It changes no generation behaviour, so it is safe to build first and measures everything that follows.
It is validated in `claude-code` mode first; the only paid step is one small live anchor run within the $3 budget (D-10).

---

## Appendix B. Version control plan

- One branch per roadmap iteration (`iter-1-measurement`, `iter-2-gates`, ...), cut from `main`.
- Commits are small and single-concern (architecture, orchestration, retrieval, skills, evaluation, rendering, performance, testing, UI, documentation, cleanup); no mixed commits.
- Each iteration ends with a benchmark checkpoint: the report is committed under `benchmark/reports/`, and the raw runs are archived by run ID.
- Review points: design review before an iteration starts (a short doc update to this plan), code review per pull request, results review against the benchmark before merging to `main`.
- Explicit merges, one iteration at a time; stacked pull requests merged bottom-up with care (see the stacked-PR merge note in project memory).
- Feature flags for every behaviour change so an iteration can be rolled back by configuration.

## Appendix C. Engineering style rules for implementation

- Pythonic, concise functions with explicit data flow and useful typing.
- Prefer typed data (dataclasses or Pydantic) over dicts across module boundaries.
- Prompts live in versioned template files, not in Python string literals.
- Comments only for the non-obvious, the technically important, paper-derived choices and deliberate constraints, and kept short.
- No new dependency without a recorded reason in this plan.
- Every gate, lint rule and model role is testable without a network.

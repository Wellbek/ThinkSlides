# Research Source Registry

Status: planning phase, iteration 1.
Last updated: 2026-09-24.

This is the structured registry of every source that materially informs `docs/PROJECT_PLAN.md` and `docs/CLAUDE_PRESENTATION_WORKFLOW.md`.
A source appears here only if it changed or supported a decision.
Each entry records what the source actually says, how much weight it carries, and where it lands in the plan.

## Conventions

Confidence describes how much the plan should lean on the source for the specific claim cited, not the source's general reputation.

- **High**: primary source, directly observed, or reproduced in this repository.
- **Medium**: credible primary source whose claim we have not reproduced, or a peer-reviewed result from an adjacent setting.
- **Low**: vendor claim, single unreplicated result, secondary reporting, or an inference several steps from the evidence.

Adoption values: **adopt**, **adapt** (take the idea, not the implementation), **evaluate** (benchmark before deciding), **reject**, **reference** (informs thinking only).

Plan section numbers refer to `docs/PROJECT_PLAN.md`.

---

## 1. Primary evidence from this repository

These are not external sources, but they carry the most weight in the diagnosis because they were observed directly.

### R-001 Historical agent run telemetry

- **Source**: `agent_events` table in the local `thinkslides_pgdata` Docker volume (5 generation sessions, 607 events, 2026-08-29 08:26 to 11:38 UTC).
- **Category**: observed system behaviour.
- **Key facts**:
  - Configured models: chat `openai/gpt-4o`, search `openai/gpt-5.6-terra` via the provider's native web search.
  - Per-tool latency (mean over all runs): `web_search` 20.6 s (21 calls), `validate_rendered_output` 26.3 s (12 calls), `propose_design_directions` 27.8 s (4 calls), `plan_presentation_structure` 11.4 s (5 calls), `finalize_and_render_pptx` 5.7 s (15 calls), `design_slide_content` 3.4 s (78 calls).
  - One fully traced 12-slide run (session `597fd8e0`) took about 7 minutes of machine time, of which drafting took 132 s (about 11 s per slide, sequential) and render plus three validation rounds took 121 s.
  - 3 of 5 runs ended with `validation_unresolved` after the revision cap; 1 run errored; 1 passed on its second validation.
  - The same deterministic overflow findings persisted unchanged across all three validation rounds of session `597fd8e0`.
  - Session `83970e7e` asked for 6 slides; `plan_presentation_structure` was called with `target_slide_count=6`, returned 12 slides, and reported an empty `deviations` list.
- **Caveat**: several fixes landed on the same day (for example #290 "keep the chosen design language for the run that actually renders" at 10:43 UTC), and the running containers may not have been rebuilt, so some defects may already be fixed.
  The first implementation iteration must re-baseline before trusting any of these numbers as current.
- **Confidence**: High for what was observed; Medium as a description of current `main`.
- **Adoption**: reference.
- **Affects**: sections 4, 5, 12, 20.

### R-002 Rendered output of those runs

- **Source**: `thinkslides_previews` volume, sessions `597fd8e0` (12 slides, insurance risk) and `83970e7e` (16 pages, Korean television).
- **Category**: observed output quality.
- **Key facts**:
  - Bar charts placing incommensurable quantities on one axis (a growth rate, a focus share and zero-filled categories; "billions of hours" beside "number of shows"), with `0%` bars standing in for missing data.
  - One chart rendered with no marks at all (Korean deck, slide 9).
  - Titles clipped at the top edge and titles overprinting body text.
  - A repeated "title, accent rule, three bullets, one exhibit strip" composition on most pages.
  - Topic-label titles ("Overview of Risk Management in 2026", "Conclusion") on roughly half the pages.
  - Accent rules under every title and a cream background with a high-contrast serif, both of which Anthropic's public guidance names as tells of generated design (R-104, R-106).
  - A source appendix of four pages containing raw hashes and truncated URLs.
- **Confidence**: High (direct inspection).
- **Adoption**: reference.
- **Affects**: sections 4, 5.

### R-003 Code audit of the generation path

- **Source**: `services/ai/app/agent/loop.py`, `pipeline.py`, `registry.py`, `tools/*`, `design/*`, `renderer/*`, `ingestion/*`, `llm/*`, `eval/*`; `services/orchestrator`; `apps/web`.
- **Category**: observed system structure.
- **Key facts**: summarised in `PROJECT_PLAN.md` sections 2 and 3.
  The most consequential facts are a single chat model for every step, an 8.5k-token system prompt rebuilt each turn with no prompt caching, sequential per-slide ReAct drafting, research held as 320-character snippets, an unused RAG ingestion path, and deterministic constraints (slide count) delegated to model self-report.
- **Confidence**: High.
- **Adoption**: reference.

### R-004 Existing style-fidelity harness

- **Source**: `services/ai/app/eval/*`, `services/ai/eval_scorecards/`.
- **Category**: existing evaluation tooling.
- **Key facts**: scores rendered decks against a measured profile of one reference publication (McKinsey Technology Trends Outlook 2022) with a design/validation page split, plus an exemplar library and a vision judge that is allowed to overrule the metrics.
  Baseline style scores were 0.32 to 0.38 on fixture decks.
- **Assessment**: the discipline (held-out pages, judge outranks metric, style and substance never merged) is sound and should be kept.
  The single reference distribution is not: it is a long-form report, and a target of 195 words per page is wrong for a pitch, a keynote or a conference talk.
- **Confidence**: High.
- **Adoption**: adapt.
- **Affects**: sections 11, 12.

---

### R-005 First headless run of the pipeline

- **Source**: `benchmark/tasks/technical/http-caching.yaml` run with `python -m app.research run` in `claude-code` mode on 2026-09-24 (commit `0a3e8f7` plus the runner), every request and answer kept under `benchmark/runs/` (not committed); its replay rebuilt pixel-identical pages.
- **Category**: observed system behaviour.
- **Key facts**:
  - 26 model calls over 4 parts (intake, two design calls, plan, three drafting turns and three copy calls per round, three vision calls per validation, closing); about 146k estimated input tokens in the drafting part alone, none cached.
  - Each drafting request was about 50 KB, most of it the design system repeated; the draft phase is one conversation across all slides, so context grows with deck length.
  - `propose_design_directions` makes two model calls; its specimen always demands three figures and a chart.
  - The revision loop redrafted only slide 1, three times, without the findings, re-rendered identical pages, and drafted again after the validation cap without validating (#299).
  - The planner prompt pairs the stated count with the format's own slide range (#300).
  - Without research, `build_table` and `build_mermaid` are refused whatever they contain (#301).
  - The drafting prompt reports the title size as 57.6 pt; the rendered PDF measures 39.6 pt, so the prompt is wrong and the rendering is not (#302, corrected by the run checks).
  - There is no technical format, and no planned `diagram` content type (#303).
- **Confidence**: High for the mechanisms (code paths confirmed); the answers were a frontier model's, so model-quality conclusions are out of scope.
- **Adoption**: reference.
- **Affects**: sections 5, 20.

## 2. Official documentation

### R-101 Anthropic public PPTX skill

- **Authors/company**: Anthropic.
- **URL**: https://github.com/anthropics/skills/tree/main/skills/pptx (inspected at commit `34040c9`, 2026-09-10; last change to the skill `fa0fa64`, 2026-07-16).
- **Category**: public source code and instructions.
- **Key idea**: a skill is a short `SKILL.md` plus deterministic scripts.
  New decks are written as `pptxgenjs` programs; template work is unzip, edit slide XML, rezip; every deck is validated structurally (`scripts/office/validate.py`) and visually (LibreOffice to PDF to JPEG, then inspect every slide).
- **Evidence (quoted)**:
  - "Your first render usually has a few real issues - overlaps, overflow, misalignment. Find and fix those, re-render only the slides you changed, and stop."
  - "After staring at the generating code you tend to see what you expect rather than what rendered, so look at the images fresh (a subagent works well for this if you have one)."
  - "Keep charts native. Use `addChart()` for everything PowerPoint can chart."
  - "NEVER use accent lines under titles - these are a hallmark of AI-generated slides."
  - A safe-font list exists because "LibreOffice ... substitutes fonts it doesn't have - and for some fonts the substitute has different widths, so your QA preview can show text overflow (or fit) that the real deck won't have."
- **History**: the first public version (2025-12) generated HTML per slide and converted it with `html2pptx.js`; 2026-02 replaced that with direct `pptxgenjs`; 2026-07 trimmed the docs "to the guidance that earns its place", added the safe-font rule, chart-corruption gotchas and validator improvements.
- **Relevance**: the most concrete public statement of how Anthropic structures presentation generation around deterministic tooling.
- **Confidence**: High.
- **Adoption**: adapt (workflow, QA procedure, anti-pattern list, native charts, font-metric discipline); reject (free-form code generation per deck, see section 13).
- **Affects**: sections 6, 10, 13, 14, 20.

### R-102 Claude Slides artifact type instructions

- **Authors/company**: Anthropic.
- **URL**: type `https://claude.ai/artifact/8jTsAFQMFDb2oA8MsPJ2eL`, release `1790097191-2423`, type description read on 2026-09-24.
- **Category**: official product instructions.
- **Key idea**: a deck is data, not a program.
  Each slide is one `<section>` in a closed HTML/CSS subset on a fixed 1920x1080 canvas, laid out with flex or grid, at most 200 elements; `deck.json` holds order, one-sentence sections (the outline), faces and design-system tokens; the runtime renders, presents and exports to PowerPoint or PDF.
- **Evidence (quoted)**:
  - "decide once: the outline (one sentence per section), length, audience, layouts to repeat ... then write every slide in one pass."
  - "NEVER VERIFY UNLESS THE USER ASKED ... Do NOT read it or your files back to check, re-check layout or sizes, render, screenshot or open it."
  - Pre-emptive fit arithmetic: "A heading ≈ size × lines × 1.1; a table row ≈ 2.1 × font-size per text line ... about 0.6 × font-size per character ... Too much? Split the slide; nothing shrinks."
  - "One idea per slide: a statement beats bullets; turn lists into tables, card rows, big numbers or quotes."
  - "Never invent a statistic or a quote" with bracketed placeholders for missing figures.
- **Relevance**: shows a second, faster Anthropic workflow in which layout quality comes from a constrained representation plus a flow layout engine plus explicit heuristics, not from a render-inspect loop.
- **Confidence**: High for the instructions; the reference pages it cites (`craft.md`, `layout.md`, `diagrams.md` and others) could not be read without creating a deck, which is out of scope for this phase.
- **Adoption**: adapt (single-pass compile from a settled outline, fit-before-render, split-not-shrink, closed representation, design tokens).
- **Affects**: sections 6, 13, 14.

### R-103 Agent Skills overview and authoring best practices

- **Authors/company**: Anthropic.
- **URLs**: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview ; https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (both read 2026-09-24).
- **Category**: official documentation.
- **Key idea**: progressive disclosure in three levels: metadata (about 100 tokens per skill, always loaded), `SKILL.md` body (under 5k tokens, loaded when triggered), bundled resources and scripts (zero tokens until read; script source never enters context, only output).
- **Evidence (quoted)**: "Prefer scripts for deterministic operations"; "Create verifiable intermediate outputs ... analyze → create plan file → validate plan → execute → verify"; "Build evaluations first"; "What works perfectly for Opus might need more detail for Haiku"; API skills run with "No network access" and "No runtime package installation".
- **Relevance**: the skill format is a knowledge-packaging discipline, and the plan-validate-execute pattern is exactly the fix for the unvalidated slide-count constraint (R-001).
- **Confidence**: High.
- **Adoption**: adapt (the packaging and loading discipline inside our own pipeline, not the Claude runtime).
- **Affects**: sections 10, 13.

### R-104 Anthropic `frontend-design` and `theme-factory` skills

- **URL**: https://github.com/anthropics/skills/tree/main/skills/frontend-design ; .../theme-factory (commit `34040c9`).
- **Category**: public source code and instructions.
- **Key idea**: design quality is steered by naming the defaults generated design falls into and forbidding them unless the brief asks for them.
- **Evidence (quoted)**: generated design "clusters around ... a warm cream background (near #F4F1EA) with a high-contrast serif display"; "Visual structure is information"; a two-pass process of "plan, review against the brief, build, critique" with a compact token system of 4 to 6 named colours and one or two typefaces.
  `theme-factory` packages ten themes as small markdown files with a visual showcase PDF and a "create your own theme" fallback.
- **Confidence**: High as a statement of Anthropic's guidance; Medium as a claim about what makes design good.
- **Adoption**: adapt (anti-default lint list; token-first design; theme library as data).
- **Affects**: sections 10, 11.

### R-105 Skills cookbook

- **URL**: https://github.com/anthropics/claude-cookbooks/tree/main/skills.
- **Category**: official examples.
- **Key idea**: PowerPoint is a built-in skill (`pptx`); custom skills are `SKILL.md` plus optional `scripts/` and `resources/`; generated files return as `file_id`s retrieved through the Files API.
- **Confidence**: High.
- **Adoption**: reference (relevant if we ever benchmark the hosted skill through the API).
- **Affects**: sections 12, 17.

### R-106 Claude for PowerPoint documentation

- **URL**: https://claude.com/docs/office-agents/powerpoint (read 2026-09-24).
- **Category**: official product documentation.
- **Key idea**: the add-in reads the slide master, layouts, fonts and colour scheme, edits individual slides without regenerating the deck, and produces native editable charts and diagrams "not static images".
- **Evidence (quoted)**: "not recommended for: Final client deliverables without human review ... Replacing your judgment on design and narrative flow."
- **Confidence**: High for stated capabilities; nothing about internals.
- **Adoption**: reference (native charts and slide-scoped editing as product expectations).
- **Affects**: sections 6, 19.

### R-107 Prompt caching documentation

- **URL**: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (read 2026-09-24).
- **Category**: official documentation.
- **Key facts**: cache prefix order is `tools`, `system`, `messages`; a change at one level invalidates it and everything after; cache reads cost 0.1x base input on standard models; 5-minute writes cost 1.25x; minimum cacheable length is 512 to 4,096 tokens depending on model.
  OpenAI applies automatic prefix caching with similar ordering semantics; that claim is not re-verified here and should be checked before relying on it.
- **Relevance**: our system prompt puts the per-turn "Next Step" at the end but also interleaves changing state (completed work, research) ahead of it, and tools are re-filtered per phase, so little of each request is a stable prefix.
- **Confidence**: High.
- **Adoption**: adopt (prompt layout that is cache-stable by construction).
- **Affects**: sections 5, 13.

### R-108 Model Context Protocol

- **URL**: https://modelcontextprotocol.io (specification; not re-read this iteration).
- **Category**: official specification.
- **Key idea**: a standard for exposing tools and resources to model hosts.
  ThinkSlides already discovers per-workspace MCP servers and scopes their tools to pipeline phases (`app/connections/mcp_tools.py`).
- **Confidence**: Medium (spec not re-read in this iteration).
- **Adoption**: adapt (MCP as a source adapter and an optional outward interface, not as the internal tool bus).
- **Affects**: section 9.

---

## 3. Engineering writeups

### R-201 Equipping agents for the real world with Agent Skills

- **Authors**: Barry Zhang, Keith Lazuka, Mahesh Murag (Anthropic), 2025-10-16.
- **URL**: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills.
- **Key idea**: a skill is "a directory containing a SKILL.md file that contains organized folders of instructions, scripts, and resources", loaded progressively; code is included for deterministic operations.
- **Confidence**: High.
- **Adoption**: adapt.
- **Affects**: section 10.

### R-202 Building effective agents

- **Authors**: Erik Schluntz, Barry Zhang (Anthropic), 2024-12-19.
- **URL**: https://www.anthropic.com/engineering/building-effective-agents.
- **Key idea**: distinguishes workflows ("predefined code paths") from agents ("LLMs dynamically direct their own processes"); names prompt chaining, routing, parallelization, orchestrator-workers and evaluator-optimizer; advises adding agentic complexity "only when simpler solutions fall short".
- **Relevance**: presentation generation after the brief is settled is mostly a fixed-shape workflow; our loop already moved toward this (`pipeline.py`), but drafting remains a per-slide ReAct agent.
- **Confidence**: High (as guidance).
- **Adoption**: adopt.
- **Affects**: sections 13, 14.

### R-203 How we built our multi-agent research system

- **Authors**: Jeremy Hadfield, Barry Zhang, Kenneth Lien, Florian Scholz, Jeremy Fox, Daniel Ford (Anthropic), 2025-06-13.
- **URL**: https://www.anthropic.com/engineering/multi-agent-research-system.
- **Key facts (quoted)**: "agents typically use about 4× more tokens than chat interactions, and multi-agent systems use about 15× more tokens than chats"; on BrowseComp "token usage by itself explains 80% of the variance"; parallel tool calling "cut research time by up to 90% for complex queries"; evaluation started with "about 20 queries"; "Adding full production tracing let us diagnose why agents failed".
- **Relevance**: parallel research is where the runtime win is; multi-agent designs buy quality with tokens, which runs against our cost goal except where work is genuinely parallel.
- **Confidence**: Medium (their domain is open research, not slides).
- **Adoption**: adapt (parallel search and fetch; small evaluation sets early; full tracing); reject (a multi-agent hierarchy for drafting).
- **Affects**: sections 5, 14, 15.

### R-204 Claude Slides launch coverage

- **Source**: The Next Web, 2026-09-17, https://thenextweb.com/news/anthropic-claude-cowork-merge-docs-slides.
- **Category**: secondary reporting.
- **Key facts**: Claude Slides launched in Claude on 2026-09-16; decks are editable, presentable in Claude and exportable to PowerPoint or PDF.
- **Confidence**: Low (secondary), superseded for mechanism by R-102.
- **Adoption**: reference.

---

## 4. Public source code (non-Anthropic)

### R-301 WeKnora

- **Authors/company**: Tencent.
- **URL**: https://github.com/Tencent/WeKnora (v0.8.0, MIT licence, read 2026-09-24).
- **Category**: public source code.
- **Key idea**: a modular Go RAG platform: multi-format parsing (including PaddleOCR-VL and VLM image captions), adaptive and parent-child chunking, hybrid retrieval (BM25, dense, GraphRAG) over pgvector and other stores, reranking, a ReAct agent with MCP tools and sandboxed skills, Langfuse tracing and a stage-by-stage parsing trace, and pipeline evaluation (recall hit rate, BLEU, ROUGE).
- **Relevance**: shows what a retrieval stack for heterogeneous user documents looks like; most of it solves a knowledge-base Q&A product, which is not our product.
- **Confidence**: Medium (README-level reading; implementation not audited).
- **Adoption**: adapt selectively (see `PROJECT_PLAN.md` section 7).
- **Affects**: sections 7, 9.

### R-302 PPTAgent (code)

- **URL**: https://github.com/icip-cas/PPTAgent.
- **Adoption**: reference; see R-401.

### R-303 AutoPresent / SlidesLib (code)

- **URL**: https://github.com/para-lost/AutoPresent.
- **Adoption**: reference; see R-402.

---

## 5. Peer-reviewed research and preprints

Preprints are labelled; they are weighted below peer-reviewed work.

### R-401 PPTAgent: Generating and Evaluating Presentations Beyond Text-to-Slides

- **Authors**: Zheng et al., arXiv 2501.03936 (2025); publication venue not verified in this iteration.
- **URL**: https://arxiv.org/abs/2501.03936.
- **Key idea**: two stages.
  Analyse reference decks (cluster slides into functional types, extract a content schema per slide), then plan an outline that picks a reference slide per new slide and emit edit actions (`replace_span`, `clone_paragraph`, `replace_image`) executed in a REPL with error feedback.
  PPTEval scores Content, Design and Coherence with an LLM judge.
- **Evidence**: 95% success over 500 tasks; removing the outline dropped coherence from 4.48 to 3.36; removing the schema dropped success to 78.8%; human-LLM Pearson correlation 0.71; human inter-rater Fleiss' kappa 0.59.
- **Relevance**: evidence that editing well-designed reference slides beats generating layouts, that an explicit outline carries coherence, and that humans themselves only moderately agree on design quality.
- **Confidence**: Medium.
- **Adoption**: adapt (exemplar-derived archetypes; outline-first; three-dimension rubric as one input).
- **Affects**: sections 10, 11, 14.

### R-402 AutoPresent: Designing Structured Visuals from Scratch

- **Authors**: Ge et al., CVPR 2025, arXiv 2501.00912.
- **URL**: https://arxiv.org/abs/2501.00912.
- **Key idea**: SlidesBench (7k training, 585 test examples from 310 decks in 10 domains); programmatic generation beats end-to-end image generation; an 8B Llama fine-tuned on 7k instruction-code pairs with a high-level library (SlidesLib) reaches results comparable to GPT-4o.
- **Relevance**: the strongest evidence that a small specialised model plus a good high-level API can match a frontier model on the slide-compilation step.
- **Confidence**: Medium (peer-reviewed; single-slide tasks, not decks).
- **Adoption**: evaluate (later, once traces exist).
- **Affects**: sections 8, 14.

### R-403 SlidesGen-Bench

- **Authors**: Yang et al., arXiv 2601.09487 (preprint, 2026-01, revised 2026-08).
- **URL**: https://arxiv.org/abs/2601.09487.
- **Key idea**: Content, Aesthetics and Editability measured computationally; decouples VLM extraction from reasoning to reduce judge hallucination; Slides-Align1.5k human-preference set across nine systems and seven scenarios.
- **Confidence**: Medium-Low (preprint).
- **Adoption**: adapt (decoupled extract-then-score judging; editability as a measured dimension).
- **Affects**: section 11.

### R-404 PresentBench

- **Authors**: arXiv 2603.07244 (preprint, 2026).
- **URL**: https://presentbench.github.io/.
- **Key idea**: 238 instances, each with source material and on average 54.1 hand-written binary checklist items; reports stronger human alignment than holistic scoring and finds NotebookLM strongest among tested systems.
- **Confidence**: Medium-Low (preprint).
- **Adoption**: adopt (instance-specific binary checklists as the primary content-quality instrument).
- **Affects**: sections 11, 12.

### R-405 X+Slides: audience-conditioned slide generation

- **Authors**: Chen et al., arXiv 2606.19256 (preprint, 2026-06).
- **Key idea**: audience coverage, domain coverage, efficiency (utility per attention) and correctness against sources over 113 topics and seven contexts; "visual polish ... shouldn't substitute for source-grounded verification".
- **Confidence**: Low-Medium.
- **Adoption**: adapt (audience as an explicit benchmark variable; correctness checked against sources).
- **Affects**: section 11.

### R-406 SeaSlides: semantic abstraction layer for agentic slide generation

- **Authors**: Fang, Wu, Zhang, arXiv 2608.03298 (preprint, 2026-08).
- **Key idea (quoted)**: "the model writes structured slide content through reusable components and capability modules, while templates own layout, style, and rendering"; three feedback stages localise build errors, constraint violations and visual defects before export.
- **Relevance**: independent support for the design decision ThinkSlides already made ("no model emits a coordinate").
- **Confidence**: Low-Medium.
- **Adoption**: adopt (as corroboration).
- **Affects**: section 13.

### R-407 ACE: self-correcting agentic canvas editor

- **Authors**: Jang et al., arXiv 2608.24103 (preprint, 2026-08).
- **Key idea**: hierarchical scene graph instead of coordinates; a content router feeds the agent "only the relevant slice of each deck (avg. ~89% input-token reduction)"; a critique-driven self-correction loop where "66% of cases halt after one pass"; 1.75x faster and about 44% cheaper than the baseline.
- **Relevance**: slice-scoped context and early stopping are the two runtime levers our revision loop lacks.
- **Confidence**: Low-Medium.
- **Adoption**: adapt.
- **Affects**: sections 5, 14.

### R-408 DECKBench

- **Authors**: Jang et al., arXiv 2602.13318 (preprint, 2026-02).
- **Key idea**: academic slide generation and multi-turn editing benchmark measuring fidelity, coherence, layout and instruction following, with a modular multi-agent baseline.
- **Confidence**: Low-Medium.
- **Adoption**: reference (candidate external suite for the academic category; multi-turn editing as a future benchmark axis).
- **Affects**: section 12.

### R-409 Design First, Code Later (DeepSlides)

- **Authors**: Cui et al., arXiv 2605.26451 (preprint, 2026-04).
- **Key idea**: separate slide design from code implementation; train design and implementation models with multi-agent RL.
- **Confidence**: Low (abstract only).
- **Adoption**: reference (supports the design/compile split; training approach deferred).

### R-410 Garner and Alley, the assertion-evidence approach

- **Citation**: Garner, J. K. and Alley, M. P. (2013). How the design of presentation slides affects audience comprehension: a case for the assertion-evidence approach. International Journal of Engineering Education 29(6), 1564-1579.
- **Evidence**: 110 engineering students; sentence headlines stating the slide's assertion, supported by visual evidence, produced better comprehension, fewer misconceptions, lower perceived cognitive load and better delayed recall than default topic-and-bullets slides.
- **Relevance**: peer-reviewed support for action titles and exhibit-first slides for technical and academic audiences; the codebase already has an arc check (`app/narrative.py`) motivated by the same idea.
- **Confidence**: Medium-High (peer-reviewed; one population).
- **Adoption**: adopt (as a category-conditional default, not a universal rule).
- **Affects**: sections 10, 11.

### R-411 Judging LLM-as-a-Judge (MT-Bench, Chatbot Arena)

- **Citation**: Zheng et al., NeurIPS 2023 Datasets and Benchmarks, arXiv 2306.05685.
- **Key idea**: LLM judges exhibit position, verbosity and self-enhancement bias; some are mitigable (position swapping, reference answers).
- **Confidence**: High.
- **Adoption**: adopt (position-swapped pairwise judging; judge model distinct from generator family where possible; human calibration set).
- **Affects**: section 11.

### R-412 FrugalGPT

- **Citation**: Chen, Zaharia, Zou, arXiv 2305.05176 (2023).
- **Key idea**: prompt adaptation, LLM approximation (caching, fine-tuned smaller models) and LLM cascades; reports matching GPT-4 with up to 98% cost reduction on their tasks.
- **Confidence**: Medium (task-dependent; classification-style tasks).
- **Adoption**: adapt (cascade with an explicit escalation signal for the few steps where a cheap model can fail detectably).
- **Affects**: sections 8, 14.

### R-413 RouteLLM

- **Citation**: Ong et al., arXiv 2406.18665 (2024).
- **Key idea**: learned routers between a strong and weak model trained on preference data; over 2x cost reduction on benchmarks without quality loss.
- **Confidence**: Medium.
- **Adoption**: evaluate (learned routing needs trace data we do not yet have; start with static routing per step).
- **Affects**: sections 8, 14.

### R-414 Co-training and Co-distillation (CTCD)

- **Citation**: Lee et al., Findings of EMNLP 2023, arXiv 2311.02849.
- **Key idea**: train a large and a small model together with two-way distillation; the distilled small model beat the original large model by 1.66 GLUE points.
- **Relevance**: the result is for encoder-style language understanding on GLUE and requires training both models; we train neither and consume hosted APIs.
  Its transferable lesson is modest: small task-specific models can be competitive on narrow tasks, which R-402 shows more directly for slides.
- **Confidence**: Medium for its own setting; Low for transfer to our setting.
- **Adoption**: reject for now; revisit only for a narrow step with abundant traces.
- **Affects**: section 8.

### R-415 DeepSeek-V3 Technical Report

- **Citation**: DeepSeek-AI, arXiv 2412.19437 (2024-12, revised 2025-02).
- **Category**: technical report, used here only for its figures.
- **Observations from Figures 2, 3 and 6 (rendered and inspected)**:
  - Dashed containers group a subsystem and carry a bold, left-aligned title ("DeepSeekMoE", "Multi-Head Latent Attention (MLA)").
  - Overview-plus-detail: a block in the overview is connected by dashed leader lines to its expanded internals.
  - Two or three fill colours encode categories (operators, shared components, routed experts), each with an inline legend; neutral grey carries data.
  - Flow is one-directional (bottom to top), with orthogonal connectors and very little crossing.
  - Annotations are small italics on the edge they describe ("concatenate", "apply RoPE", "Shared").
  - Repetition is shown by parallel, identically structured columns (MTP modules) that differ only in the shifted token labels.
  - Mathematical notation is used for tensors instead of prose labels.
- **Confidence**: High (direct inspection).
- **Adoption**: adapt (diagram grammar; see `PROJECT_PLAN.md` section 11).
- **Affects**: sections 10, 11.

---

## 6. Vendor and industry claims

### R-501 System One models and Jev

- **Authors/company**: Diogo Almeida, TypeSafe AI, 2026-09-15.
- **URL**: https://typesafe.ai/blog/introducing-system-one-models-and-jev.
- **Key idea**: a non-autoregressive "System One" model that returns schema-typed values in parallel (70 to 500 ms), trained with "Reinforcement Learning for Calibrated Decisions", with calibrated confidence scores; targeted at classification, selection, routing and guardrail decisions, explicitly not text generation.
- **Evidence**: vendor benchmarks built by the vendor's own team; "0% hallucination" refers to schema conformance, not factual correctness; early access only; pricing stated as $0.042 per million input tokens with free output.
- **Confidence**: Low (unreplicated vendor claims; no independent evaluation found).
- **Adoption**: evaluate, behind the same interface as any other classifier, only after deterministic and cheap-LLM baselines exist for the same decisions.
- **Affects**: sections 8, 14.

---

## 7. Benchmarks and evaluation resources to consider

| ID | Resource | Use in our benchmark | Status |
| --- | --- | --- | --- |
| R-402 | SlidesBench (310 decks, 10 domains) | reference-free design scoring ideas; possible exemplar source | evaluate licence and fit |
| R-404 | PresentBench (238 instances with sources and checklists) | external content-quality suite with source packs | evaluate licence and fit |
| R-408 | DECKBench | academic category, multi-turn edits | evaluate |
| R-403 | Slides-Align1.5k | judge calibration against human preference | evaluate |
| R-004 | Internal McKinsey reference profile | consulting category only | keep, narrow its scope |

---

## 8. Community examples and guidance

No community sources were relied on in this iteration.
Commercial "how to use Claude for PowerPoint" posts were found (for example prezent.ai, slideuplift.com, deckary.com) but were not used as evidence because they are secondary and promotional.

---

## 9. Contradictions and open disagreements

- **Render-and-inspect versus never-verify.** R-101 makes visual QA mandatory; R-102 forbids verification unless asked.
  Our reading is that they sit at different points on the speed/quality curve and differ in representation: free-form OOXML code needs inspection, while a flow-layout declarative format with fit arithmetic rarely does.
  This is an inference, labelled as such in `CLAUDE_PRESENTATION_WORKFLOW.md`.
- **Native charts versus images.** R-101 and R-106 insist on native editable charts; `app/renderer/chart_renderer.py` deliberately renders matplotlib PNGs for cross-renderer fidelity.
  This is an unresolved product trade-off (editability versus identical rendering) and needs a benchmark on LibreOffice and PowerPoint fidelity before deciding.
- **LLM judges.** R-401 reports 0.71 human correlation; R-411 documents systematic biases; R-404 and R-403 report that checklists and decoupled extraction align better than holistic scores.
  The plan uses judges for pairwise comparison and defect detection, not as a single quality score.
- **Multi-agent benefit.** R-203 reports large quality gains from multi-agent research at about 15x tokens; our cost goal argues against multi-agent drafting.
  The plan uses parallel workers only where work is independent (search, per-slide compilation, per-slide critique).

## 10. Research not yet done

- Read the Slides type reference pages (`craft.md`, `layout.md`, `diagrams.md`, `diagram-recipes.md`), which requires creating a private deck artifact.
- Measure LibreOffice versus PowerPoint rendering fidelity for native charts produced by python-pptx.
- Licence review of SlidesBench, PresentBench and DECKBench for use as internal benchmark inputs.
- Current per-token prices for the candidate models at benchmark time (deliberately not recorded here; they change too often).
- Independent evaluation of Jev, if early access is granted.
- NotebookLM and other commercial generators as benchmark comparators: terms of service review.

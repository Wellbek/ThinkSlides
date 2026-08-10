# ThinkSlides Architecture Diagram Prompts

Ready-to-use prompts for generating the architecture diagrams referenced in the
root `README.md`.
Paste each prompt into a diagram-capable AI tool (for example Claude, ChatGPT,
Eraser.io, napkin.ai, Excalidraw AI, or a Mermaid/PlantUML generator).
Each prompt is self-contained and encodes the real service names, ports,
technologies, and data flows of the ThinkSlides system so the output is accurate.

When you have a final image, drop it into `docs/diagrams/` and replace the
matching placeholder in the README.

Recommended target format: landscape, high-resolution PNG or SVG, clean
enterprise style, consistent color per layer (frontend / orchestration / AI /
data), and legible labels at README display width.

---

## Design Decisions

Notes to fold into future diagram revisions and the README.

**Images are reused from source material, never AI-generated.** When a slide
could use an image, the pipeline looks for one that already exists - embedded
in the user's own uploaded PDF/DOCX documents, or found via `web_search` - and
only attaches it when the match is confidently, specifically relevant to that
slide (`select_source_image`). There is no image-generation step and no
image-generation API integration anywhere in the pipeline. This is a
deliberate product decision, not a missing feature.

**Output validation targets LibreOffice only, treated as ground truth.**
`validate_rendered_output` converts the rendered `.pptx` to PDF via
LibreOffice (the same conversion `PreviewGenerator` already performs for
preview thumbnails) and inspects the actual rendered text geometry for
overflow/overlap. There is no PowerPoint-native rendering engine check - true
PowerPoint rendering would require Windows or a paid cloud rendering API,
neither of which this stack has infrastructure for. LibreOffice's rendered
output is treated as authoritative, consistent with how the preview pipeline
already worked before this decision was made explicit.

---

## Prompt 1 - System Architecture (hero diagram)

You are a senior software architect producing a polished, enterprise-grade
system architecture diagram for a GitHub README.
Generate a single landscape diagram titled "System Architecture".

The system is a three-tier, multi-tenant AI presentation generator.
Render it as clearly grouped layers with the following exact nodes, technologies,
ports, and connections. Use one color per layer (frontend, orchestration, AI,
data/infra) and use real tech/language icons such as from glm, claude, python, go,.... 
Show ports on every service. Show shared Docker volumes explicitly.

CLIENT LAYER:
- Browser (Next.js 14 App Router client). Connects to the web server over HTTP.

FRONTEND SERVICE:
- "Web - Next.js 14 App Router" (TypeScript, Tailwind CSS, Zustand stores,
  Framer Motion). Port 3000.
  Internal detail (small box inside): Next.js middleware rewrites
  /api/orchestrator/* to the orchestrator and /api/ai/* to the AI service;
  a dedicated /api/ai/templates/upload-stream route handler streams SSE without
  buffering. SSEClient opens an EventSource DIRECTLY to the orchestrator
  (bypassing Next.js) at /api/sessions/:id/events?token=<JWT> because
  EventSource cannot set headers.

ORCHESTRATION LAYER:
- "Orchestrator - Go 1.24 / Gin". Port 8080.
  Internal components (sub-boxes): Gin Router, CORS middleware,
  AuthRequired JWT middleware (HS256, 72h, accepts Authorization Bearer OR
  ?token= query param), AuthHandler (bcrypt, UserRepository),
  SessionHandler (direct PostgreSQL via pgx), Redis Job Queue
  (LPUSH/BRPOP on "thinkslides:jobs", job records "thinkslides:job:<id>" 24h TTL),
  background Worker goroutine (BRPOP dequeue -> HTTP call to AI service),
  SSE Hub (subscribes to Redis pub/sub "agent:events:<session_id>" and fans out
  to in-memory subscriber channels -> StreamEvents SSE response).
  Calls AI service over plain HTTP (fire-and-forget), NOT gRPC.

AI LAYER:
- "AI Service - Python 3.11 / FastAPI / Uvicorn (2 workers)". Port 8000.
  Internal components: ReAct Agent Loop (max 25 iterations, WorkingState
  checkpointed to Redis), Tool Registry (12 tools), LLM Provider Abstraction
  (Zhipu GLM default: glm-4.7 text, glm-4.5-flash planning, glm-4.6v vision;
  Azure OpenAI swappable: gpt-4, gpt-4o), PPTX XML-clone Renderer (also
  renders native tables and matplotlib-generated chart images, styled from
  a per-run dataViz config derived from the template's palette),
  Preview Generator (LibreOffice headless + PyMuPDF), Document Ingestion
  (RAG with pgvector), structlog JSON logging.
  Publishes agent events to Redis pub/sub "agent:events:<session_id>".
  Installs LibreOffice in its image for PPTX->PDF->PNG preview rendering AND
  for validate_rendered_output's ground-truth output validation.

DATA / INFRA LAYER:
- "PostgreSQL 16 + pgvector". Port 5432. Vector column dimension 1536 with
  ivfflat cosine index. Tables: organisations, users, templates,
  design_system_bundles, chat_sessions, chat_messages, agent_events (with
  raw_event JSONB), generated_files, ingested_documents, document_chunks.
- "Redis 7". Port 6379. Three uses: (1) job queue list, (2) pub/sub event bus
  "agent:events:<session_id>", (3) key-value working state
  "working_state:<session_id>" (1h TTL) for pause/resume.

EXTERNAL DEPENDENCIES (callouts to the side):
- Zhipu GLM LLM API (default provider) / Azure OpenAI (swappable).
- Web Search backend: Zhipu search_std or Tavily.

CONNECTIONS (label each with the protocol/path):
1. Browser -> Web (:3000) HTTP.
2. Web -> Orchestrator (:8080) via /api/orchestrator/* rewrites (HTTP, JWT Bearer).
3. Web -> AI Service (:8000) via /api/ai/* rewrites (HTTP) and the streaming
   upload-stream route handler (SSE).
4. Browser -> Orchestrator (:8080) DIRECT EventSource SSE
   /api/sessions/:id/events?token=<JWT>.
5. Orchestrator -> Redis: enqueue/dequeue jobs (BRPOP) AND subscribe to
   pub/sub "agent:events:<session_id>".
6. Orchestrator Worker -> AI Service (:8000) HTTP POST
   /api/v1/generate/sessions/<id>/messages (and /answer-question for resume).
7. AI Service -> Redis: PUBLISH events to "agent:events:<session_id>" and
   read/write "working_state:<session_id>".
8. Orchestrator -> PostgreSQL (pgx): sessions, messages, agent_events,
   generated_files, templates, design_system_bundles, auth.
9. AI Service -> PostgreSQL (asyncpg/SQLAlchemy): templates, design_system_bundles,
   agent_events, generated_files, ingested_documents, document_chunks (RAG).
10. AI Service -> Zhipu/Azure LLM APIs and Web Search backend.
11. Shared Docker volumes "uploads", "templates", "output", "previews" connecting
    Orchestrator and AI Service (file handoff for templates, generated PPTX,
    preview PNGs).
12. All services on a single Docker bridge network "thinkslides-network".

Add a small legend: solid arrows = synchronous HTTP, dashed arrows = async
Redis pub/sub, dotted = shared filesystem volume.

---

## Prompt 2 - End-to-End Request and SSE Event Flow (sequence)

Generate a landscape sequence/flow diagram titled
"Request and Real-Time SSE Event Flow".
Show the multi-hop, asynchronous nature of a single chat message and how events
stream back to the browser. Participants left-to-right: Browser, Web (Next.js),
Orchestrator (Go), Redis, AI Service (Python), PostgreSQL, LLM API.

Steps to depict:
1. Browser POST /api/orchestrator/sessions/:id/messages (JWT Bearer).
2. Next.js middleware rewrites to Orchestrator :8080/api/sessions/:id/messages.
3. Orchestrator persists user message + empty assistant placeholder to
   PostgreSQL, then LPUSH a "generate" job onto Redis "thinkslides:jobs".
4. Orchestrator returns 200 immediately to the browser (non-blocking).
5. Browser opens EventSource DIRECT to Orchestrator
   /api/sessions/:id/events?token=<JWT> (SSE). Orchestrator replays historical
   agent_events from PostgreSQL, then streams live.
6. Orchestrator background worker BRPOPs the job, HTTP POSTs (fire-and-forget)
   to AI Service /api/v1/generate/sessions/:id/messages.
7. AI Service runs the ReAct loop, calling the LLM API and executing tools.
   For EACH agent step it: writes an agent_events row to PostgreSQL AND
   PUBLISHes to Redis pub/sub "agent:events:<session_id>".
8. Orchestrator SSE Hub (subscribed to that Redis channel) forwards each event
   to the browser over the open SSE connection.
9. Stream the event types in order as a labeled sequence:
   thought -> tool_call -> tool_result (carries cited sources) ->
   (repeat per tool) -> slide_finalized (per slide, with previewUrl) ->
   file_generated -> done.
10. On the "done" event (paused=false), the worker saves the final assistant
    reply to PostgreSQL (filling the placeholder) and the SSE stream closes.
11. Note the pause/resume branch: if the agent calls ask_user_question, it emits
    a "question" event then a "done" with paused=true; the SSE stays open and
    a later user answer triggers a "resume" job.

Highlight that the generation HTTP call is decoupled from the response:
results travel back via Redis pub/sub, not the HTTP response.

---

## Prompt 3 - ReAct Agent Loop and Tool Pipeline

Generate a landscape diagram titled
"ReAct Agent Loop and Tool Pipeline".
Show the iterative Reason-Act-Observe cycle and the 12-tool registry mapped to
logical phases.

Depict a cycle: Load Context -> Build System Prompt -> [LLM Call] ->
Thought (emit event) -> Tool Call (emit event) -> Execute Tool ->
Tool Result (emit event, with cited sources) -> feed back as role:tool message ->
repeat. Max 25 iterations. Guard rails: cannot exit until
finalize_and_render_pptx AND validate_rendered_output have both run at least
once (validation bounded to 3 attempts, so an unfixable overflow can't loop
forever); web_search capped at 3 calls per run; iteration-cap-reached error
if exhausted. WorkingState (accumulated slides, structure, completed_tools,
answered_questions, web_search_calls, has_called_finalize,
has_called_validate, validation_attempts, pending_question) is checkpointed
to Redis "working_state:<session_id>" (1h TTL) for pause/resume.

Map tools to phases as swimlanes:
- content_analysis: analyze_document (also catalogs embedded PDF/DOCX images
  for reuse), extract_key_data, extract_template_design_system, web_search
- structure_planning: plan_presentation_structure
- layout_selection: select_layout
- content_generation: design_slide_content, select_source_image (reuses an
  already-existing image from the document/web catalog when confidently
  relevant - never generates a new one)
- validation: validate_slide_json (schema, pre-render)
- rendering: finalize_and_render_pptx
- validation: validate_rendered_output (ground truth - inspects the real
  LibreOffice-rendered PDF geometry for overflow/overlap; mandatory final
  step, runs after finalize_and_render_pptx)
- user_input: ask_user_question (pauses workflow)

For each tool show a one-line purpose and which LLM tier it uses:
main LLM (glm-4.7) for design_slide_content and finalize; tools/flash LLM
(glm-4.5-flash) for extract_key_data and plan_presentation_structure;
vision LLM (glm-4.6v) for extract_template_design_system on PDF/image templates;
web_search uses Zhipu search_std or Tavily.

Show the SSE event types emitted by the loop:
thought, tool_call, tool_result (with sources), slide_finalized, question,
error, done (with paused flag). Note timer_update, file_generated, and the
final done are injected by the caller (_run_agent in sessions.py).

---

## Prompt 4 - Data Model (ERD)

Generate a landscape Entity-Relationship Diagram titled
"Data Model (PostgreSQL 16 + pgvector)".
Show all tables, primary/foreign keys, and key columns. PostgreSQL with JSONB,
UUID, ARRAY, and pgvector types.

Entities and relationships:
- organisations (id, name, slug, settings JSONB) 1--* users
- users (id, organisation_id, email, password_hash, display_name, role)
  1--* chat_sessions
- templates (id, organisation_id, name, tags ARRAY, version, file_path,
  is_default) 1--* design_system_bundles; 1--* chat_sessions (template_id)
- design_system_bundles (id, template_id, version, palette/typography/spacing/
  layouts/slide_dimensions/master_elements/embedded_media JSONB,
  raw_xml_snapshot TEXT)
- chat_sessions (id, organisation_id, user_id, title, template_id,
  inline_template_path, slide_count, status) 1--* chat_messages;
  1--* agent_events; 1--* generated_files; 1--* ingested_documents
- chat_messages (id, session_id, role [user/assistant/system], content,
  attachments JSONB, metadata JSONB) 1--* agent_events
- agent_events (id, session_id, message_id, event_type, phase, content JSONB,
  tool_name, tool_args JSONB, tool_result JSONB, duration_ms, token_count,
  raw_event JSONB)
- generated_files (id, organisation_id, session_id, file_type, file_path,
  original_filename, file_size_bytes, metadata JSONB)
- ingested_documents (id, organisation_id, session_id, original_filename,
  file_path, mime_type, chunk_count, status) 1--* document_chunks
- document_chunks (id, document_id, organisation_id, content, chunk_index,
  embedding vector(1536), metadata JSONB) with ivfflat cosine index
  idx_doc_chunks_embedding for RAG similarity search (cosine <=> operator).

Annotate which service writes each table: Orchestrator (Go/pgx) writes
users, organisations, chat_sessions, chat_messages, agent_events (read),
generated_files, templates, design_system_bundles; AI Service
(Python/asyncpg) writes templates, design_system_bundles, agent_events,
generated_files, ingested_documents, document_chunks.

---

## Prompt 5 - Template Ingestion and PPTX Rendering Pipeline

Generate a landscape pipeline diagram titled
"Template Design-System Extraction and PPTX Rendering Pipeline".
Show two pipelines side by side, both ending in shared Docker volumes.

LEFT PIPELINE - Template design-system extraction:
1. User uploads .pptx/.potx/.pdf via /api/ai/templates/upload-stream (streaming
   SSE with 8s heartbeats to avoid proxy timeouts).
2. AI Service: for PPTX/POTX, parse slide XML into a DesignSystemBundle
   (palette, typography, spacing tokens, layout defs, placeholder positions,
   master elements, embedded media, slide dimensions in EMU + px); for PDF
   templates, a vision LLM (glm-4.6v) analyzes the design.
3. Persist Template + DesignSystemBundle rows (raw_xml_snapshot stored).
4. Stream progress events back to the browser.

RIGHT PIPELINE - PPTX rendering (finalize_and_render_pptx tool):
1. Input: validated Slide JSON (shared @thinkslides/schemas) + template path.
2. XML-clone renderer: unzip template PPTX, build layout_map from
   slideLayouts, clone the matching layout XML as each slideN.xml, fill
   placeholder <a:t> text nodes WITHOUT modifying <a:rPr> run properties
   (preserves template design fidelity), update presentation.xml sldIdLst,
   [Content_Types].xml, docProps/core.xml metadata, repack ZIP.
3. Fallback path (no template): python-pptx with a built-in default design
   system + LayoutEngine + TypographyEngine.
4. Write final .pptx to the shared "output" volume.
5. Preview Generator: LibreOffice headless PPTX->PDF (isolated user profile,
   persisted alongside the PNGs as deck.pdf so validate_rendered_output can
   reuse it without converting a second time) -> PyMuPDF (fitz) PDF->PNG at
   2x zoom -> /previews/<session>/slide_N.png on the shared "previews" volume.
6. Orchestrator serves the file via GET /api/files/:id (download) and
   /previews/* (static images) - proxied through the Next.js frontend's
   middleware the same way /api/orchestrator/* and /api/ai/* already are, so
   the browser can actually reach them.

Note the shared schemas (@thinkslides/schemas: slide.ts/slide.py,
agent-events, api) are the contract between the agent loop, the renderer, and
the frontend.

"""
The README's figures, one function each. Run from the repository root:

    services/ai/.venv/bin/python docs/diagrams/src/figures.py [name ...]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from figlib import BLUE, MUTED, Figure, measure  # noqa: E402


# ── Figure 1 ───────────────────────────────────────────────────────────────


def system_architecture() -> Figure:
    f = Figure(1240, 830, "ThinkSlides system architecture",
               "The browser talks to the Go orchestrator over HTTPS and a resumable SSE "
               "stream. The orchestrator admits a run and appends it to a Redis stream; "
               "AI run workers consume it, call language models per role, hand rendering "
               "to a process pool, and publish events back through Redis. PostgreSQL with "
               "pgvector holds every record, the run state and the evidence store.")

    def titled(x, y, w, h, title, logos):
        f.group(x, y, w, h, title)
        lx = x + measure(title, 14, bold=True) + 10
        for i, name in enumerate(logos):
            f.logo(name, lx + i * 25, y - 26, 21)

    # Client
    titled(30, 40, 270, 160, "Client", ["nextdotjs", "react", "typescript", "tailwindcss"])
    f.box(60, 62, 220, 120, [("Workspace UI", {"bold": True}),
                             ("chat, live phase timeline,", {"size": 11.5}),
                             ("slide previews, sources", {"size": 11.5})],
          kind="user")

    # Orchestrator
    titled(30, 262, 270, 400, "Orchestrator", ["go", "gin"])
    f.box(60, 284, 220, 60, ["Auth", ("JWT in header, or ?token= for SSE", {"size": 11})],
          logos=["jsonwebtokens"], logo_size=14)
    f.box(60, 356, 220, 66, ["Admission control", ("per-organisation run and token limits",
                                                   {"size": 11})])
    f.box(60, 434, 220, 66, ["Session, file and template API", ("REST over pgx", {"size": 11})])
    f.box(60, 512, 220, 132, ["SSE hub", ("one subscription per session,", {"size": 11}),
                              ("fanned out to every open tab;", {"size": 11}),
                              ("every event carries its sequence", {"size": 11}),
                              ("and a reconnect replays from", {"size": 11}),
                              ("Last-Event-ID", {"size": 11, "italic": True})])

    # Redis
    f.cylinder(345, 92, 190, 320, [("Redis 7", {"bold": True}), "",
                                   ("run stream", {"size": 12}),
                                   ("consumer group, XACK,", {"size": 11, "italic": True}),
                                   ("XAUTOCLAIM on a dead worker", {"size": 11, "italic": True}), "",
                                   ("event pub/sub", {"size": 12}),
                                   ("agent:events:<session>", {"size": 11, "italic": True}), "",
                                   ("caches", {"size": 12}),
                                   ("search, fetch, renders", {"size": 11, "italic": True})],
               logos=["redis"], logo_size=26)

    # AI service
    titled(620, 40, 350, 622, "AI service", ["python", "fastapi", "pydantic"])
    f.box(640, 62, 310, 150, [("Run workers", {"bold": True}),
                              ("one run per task: the pipeline of Figure 3;", {"size": 11.5}),
                              ("phases are code, models answer", {"size": 11.5}),
                              ("typed requests, slides compile", {"size": 11.5}),
                              ("concurrently; state checkpointed", {"size": 11.5})],
          kind="code")
    f.box(640, 228, 310, 118, [("Design engine", {"bold": True}),
                               ("languages, packs, archetype selection,", {"size": 11.5}),
                               ("binding and repair; no model emits", {"size": 11.5}),
                               ("a coordinate (Figure 4)", {"size": 11.5})], kind="code")
    f.box(640, 362, 310, 126, [("Render pool", {"bold": True}),
                               ("separate processes, off the event loop:", {"size": 11.5}),
                               ("python-pptx, charts, LibreOffice, PyMuPDF", {"size": 11.5})],
          kind="code", logos=["powerpoint", "matplotlib", "libreoffice"], logo_size=22)
    f.box(640, 504, 310, 140, [("Evidence service", {"bold": True}),
                               ("parallel search and fetch, extraction,", {"size": 11.5}),
                               ("chunking, a hybrid index, per-slide", {"size": 11.5}),
                               ("retrieval, span-level citations", {"size": 11.5}),
                               ("(Figure 6)", {"size": 11.5})],
          kind="code")

    # External
    f.group(1046, 40, 174, 622, "External services")
    f.box(1060, 62, 146, 150, [("Model providers", {"bold": True}),
                               ("routed per role:", {"size": 11}),
                               ("small, medium,", {"size": 11}),
                               ("frontier, vision", {"size": 11})],
          kind="model", logos=["openai", "anthropic", "zdotai", "azure"], logo_size=19)
    f.box(1060, 520, 146, 56, [("Web search", {"bold": True}), ("Tavily or native", {"size": 11})],
          kind="plain")
    f.box(1060, 588, 146, 56, [("MCP sources", {"bold": True}), ("inbound only", {"size": 11})],
          kind="plain")
    f.logo("modelcontextprotocol", 1066, 596, 16)
    f.box(1060, 362, 146, 118, [("Telemetry", {"bold": True}),
                                ("optional export of", {"size": 11}),
                                ("run spans to a", {"size": 11}),
                                ("self-hosted Langfuse", {"size": 11})],
          kind="plain", logos=["opentelemetry"], logo_size=18)

    # Stores
    f.cylinder(300, 700, 430, 82, [("PostgreSQL 16 + pgvector", {"bold": True}),
                                   ("records, events, run state, spans, evidence (Figure 7)",
                                    {"size": 11.5})])
    f.logo("postgresql", 318, 724, 30)
    f.cylinder(760, 700, 230, 82, [("Shared file storage", {"bold": True}),
                                   ("uploads, templates, decks, previews", {"size": 11})])

    # client <-> orchestrator
    f.arrow([(230, 182), (230, 284)], label="HTTPS (REST)", label_at=(222, 222), label_anchor="end")
    f.arrow([(60, 578), (42, 578), (42, 122), (60, 122)], color=BLUE)
    f.text(46, 222, "SSE", size=11.5, italic=True, color=BLUE, anchor="start")
    # orchestrator <-> redis
    f.arrow([(280, 389), (345, 389)], label="XADD", label_at=(312, 382))
    f.arrow([(440, 412), (440, 578), (280, 578)], color=BLUE, label="SUBSCRIBE",
            label_at=(446, 512), label_anchor="start")
    # redis <-> run workers
    f.arrow([(535, 112), (640, 112)], label="XREADGROUP", label_at=(587, 105), size=10.5)
    f.arrow([(640, 180), (535, 180)], color=BLUE, label="PUBLISH", label_at=(587, 173))
    # inside the AI service
    f.arrow([(795, 212), (795, 228)])
    f.arrow([(795, 346), (795, 362)])
    # models, telemetry, evidence sources
    f.arrow([(950, 112), (1060, 112)], both=True, label="typed I/O", label_at=(1005, 105))
    f.arrow([(950, 195), (1020, 195), (1020, 421), (1060, 421)], dash="4 3",
            label="spans", label_at=(1026, 300), label_anchor="start")
    f.arrow([(950, 548), (1060, 548)])
    f.arrow([(950, 616), (1060, 616)])
    # stores
    f.arrow([(170, 662), (170, 741), (300, 741)], both=True, label="pgx", label_at=(236, 735))
    f.arrow([(700, 662), (700, 700)], both=True, label="asyncpg", label_at=(706, 686), label_anchor="start")
    f.arrow([(875, 662), (875, 700)], both=True)

    f.legend(30, 812, [("code", "deterministic code"), ("model", "language models"),
                       ("user", "user"), ("group", "process boundary"),
                       ("arrow", "request or data"), ("dashed", "optional")], size=11.5)
    f.logo("docker", 1028, 795, 26)
    f.text(1060, 813, "every service runs in a container", size=11.5, anchor="start", color=MUTED)
    return f


# ── Figure 2 ───────────────────────────────────────────────────────────────


def request_sse_flow() -> Figure:
    f = Figure(1240, 800, "Request and event flow",
               "A message is accepted in milliseconds and runs asynchronously. Every agent "
               "event is written to PostgreSQL with its sequence number before it is "
               "published, so a browser that reconnects replays exactly what it missed.")
    lanes = [("Browser", ["nextdotjs"], "user"), ("Orchestrator", ["go"], "code"),
             ("Redis", ["redis"], "store"), ("Run worker", ["python"], "code"),
             ("Render pool", ["libreoffice"], "code"), ("PostgreSQL", ["postgresql"], "store")]
    xs = [110 + i * 204 for i in range(len(lanes))]
    top = 20
    for x, (name, logos, kind) in zip(xs, lanes):
        f.box(x - 80, top, 160, 54, [(name, {"bold": True})], kind=kind, logos=logos, logo_size=18)

    y = [108]
    lifeline_from = top + 54

    def msg(a, b, text, n=None, color="#000000", note=None, step=32):
        ya = y[0]
        right = b > a
        f.arrow([(xs[a] + (4 if right else -4), ya), (xs[b] + (-4 if right else 4), ya)], color=color)
        if n is not None:
            f.badge(xs[a] + (14 if right else -14), ya - 11, n)
        if right:
            f.text(xs[a] + (28 if n is not None else 10), ya - 6, text, size=11.5, color=color,
                   anchor="start")
        else:
            f.text(xs[a] - (28 if n is not None else 10), ya - 6, text, size=11.5, color=color,
                   anchor="end")
        if note:
            f.text(xs[min(a, b)] + 8, ya + 15, note, size=10.5, italic=True, color=MUTED,
                   anchor="start")
        y[0] += step + (12 if note else 0)

    def act(lane, text, n=None, h=26, w=176):
        ya = y[0] - 10
        f.box(xs[lane] - w / 2, ya, w, h, [(text, {"size": 11})], kind="code")
        if n is not None:
            f.badge(xs[lane] - w / 2 - 12, ya + h / 2, n)
        y[0] += h + 10

    def phase(label):
        ya = y[0] - 4
        f.line([(24, ya), (1216, ya)], color=BLUE, dash="6 4")
        f.text(1214, ya + 15, label, size=12, bold=True, anchor="end", color=BLUE)
        y[0] += 26

    phase("Accept")
    msg(0, 1, "POST /sessions/:id/messages", 1)
    act(1, "auth; admission: run and token limits", 2, w=230)
    msg(1, 5, "INSERT message, run row", 3)
    msg(1, 2, "XADD runs", 4)
    msg(1, 0, "202 Accepted", 5)

    phase("Stream")
    msg(0, 1, "GET /sessions/:id/events", 6, color=BLUE,
        note="header Last-Event-ID: n on a reconnect")
    msg(1, 5, "SELECT events after n: the replay", 7, color=BLUE)
    msg(1, 2, "SUBSCRIBE agent:events:<session>", 8, color=BLUE)

    phase("Run")
    msg(2, 3, "XREADGROUP: one worker", 9)
    msg(3, 5, "load run state", 10)
    act(3, "pipeline step (Figure 3)", 11, w=190)
    msg(3, 4, "render, rasterise, measure", 12)
    msg(4, 3, "deck, page images, geometry", None)
    msg(3, 5, "INSERT event, seq = n + 1", 13)
    msg(3, 2, "PUBLISH event", 14, color=BLUE)
    msg(2, 1, "event", None, color=BLUE)
    msg(1, 0, "id: n+1, event: thought, tool_call ...", 15, color=BLUE,
        note="persisted before published, so whatever was published can be replayed")

    phase("Pause or finish")
    msg(3, 5, "checkpoint state; INSERT question or done", 16)
    msg(3, 2, "PUBLISH; XACK the run", 17, color=BLUE)
    msg(1, 0, "question: the answer returns as step 1", 18, color=BLUE)
    msg(1, 0, "done: the stream closes", None, color=BLUE)

    bottom = y[0] + 4
    for x in xs:
        f.parts.insert(0, f'<path d="M{x},{lifeline_from} L{x},{bottom}" stroke="#9A9A9A" '
                          f'stroke-width="1" stroke-dasharray="3 4" fill="none"/>')
    f.legend(24, bottom + 30, [("arrow", "request or reply"), ("code", "code"), ("store", "store"),
                               ("user", "user")], size=11.5)
    f.line([(560, bottom + 27), (582, bottom + 27)], color=BLUE, sw=1.4)
    f.text(588, bottom + 31, "the event path", size=11.5, anchor="start")
    f.height = bottom + 48
    return f


# ── Figure 3 ───────────────────────────────────────────────────────────────


def stack(f: Figure, x, y, w, h, rows, kind, n=2, **kw):
    """A box with copies behind it: work that runs once per item, in parallel."""
    from figlib import KINDS
    fill, stroke = KINDS[kind]
    for i in range(n, 0, -1):
        f.rect(x + 5 * i, y - 5 * i, w, h, fill=fill, stroke=stroke)
    f.box(x, y, w, h, rows, kind=kind, **kw)


def pipeline() -> Figure:
    f = Figure(1240, 726, "The generation pipeline",
               "Intake, one consultation, parallel evidence gathering, a storyline written "
               "once, the outline review, the design language, then every slide compiled at "
               "once by a small model behind deterministic gates, rendered, checked, and "
               "repaired at the cheapest effective rung until no blocking defect is left.")
    W, H = 168, 118
    tier = lambda t: (t, {"size": 10.5, "italic": True, "color": MUTED})  # noqa: E731

    # Row 1: understand and plan
    f.group(24, 40, 1192, 220, "Understand and plan")
    x1 = [44 + i * 194 for i in range(6)]
    yb = 76
    f.box(x1[0], yb, W, H, [("Intake", {"bold": True}), ("constraints parsed in code:", {"size": 11}),
                            ("count, sections, exclusions;", {"size": 11}),
                            ("format, research need", {"size": 11}), tier("small model")],
          kind="model")
    f.box(x1[1], yb, W, H, [("Consult", {"bold": True}), ("one pause, only for", {"size": 11}),
                            ("answers that change", {"size": 11}), ("the deck", {"size": 11})],
          kind="user")
    stack(f, x1[2], yb, W, H, [("Evidence", {"bold": True}), ("query plan, then search,", {"size": 11}),
                               ("fetch and extract in", {"size": 11}),
                               ("parallel; indexed (Fig. 6)", {"size": 11}), tier("small model, code")],
          kind="code")
    f.box(x1[3], yb, W, H, [("Storyline", {"bold": True}), ("per slide: action title,", {"size": 11}),
                            ("claim, evidence refs,", {"size": 11}), ("exhibit intent", {"size": 11}),
                            tier("medium model, 1 call")], kind="model")
    f.box(x1[4], yb, W, H, [("Outline review", {"bold": True}), ("the user checks the", {"size": 11}),
                            ("argument before any", {"size": 11}), ("slide exists", {"size": 11})],
          kind="user")
    stack(f, x1[5], yb, W, H, [("Design language", {"bold": True}), ("the template's own, or", {"size": 11}),
                               ("three authored, gated,", {"size": 11}), ("shown as specimens", {"size": 11}),
                               tier("medium model (Fig. 4)")], kind="model")
    for a, b in zip(x1, x1[1:]):
        f.arrow([(a + W + (10 if a in (x1[2], x1[5]) else 0), yb + H / 2), (b, yb + H / 2)])
    f.box(x1[3] + 14, yb + H + 12, W - 28, 24, [("validated against the brief", {"size": 10.5})],
          kind="code", r=4)
    f.arrow([(x1[3] + W / 2, yb + H + 12), (x1[3] + W / 2, yb + H)])

    # wrap to row 2
    f.arrow([(x1[5] + W / 2, yb + H), (x1[5] + W / 2, 284), (215, 284), (215, 342)])

    # Row 2: make, check, repair
    f.group(24, 312, 1192, 300, "Make, check and repair", inside=False)
    yr = 352
    stack(f, 44, yr, 200, 150, [("Compile × N slides", {"bold": True}),
                                ("one structured call per slide,", {"size": 11}),
                                ("all at once, with that slide's", {"size": 11}),
                                ("evidence and capacity budgets", {"size": 11}),
                                tier("small model, shared cached prefix")], kind="model")
    f.box(282, yr, 190, 150, [("Gates", {"bold": True}), ("schema, grounding,", {"size": 11}),
                              ("chart lint, capacity fit:", {"size": 11}),
                              ("split or re-bind, never", {"size": 11}),
                              ("shrink below the floor", {"size": 11})], kind="code")
    f.box(510, yr, 190, 150, [("Bind and render", {"bold": True}), ("archetype per page,", {"size": 11}),
                              ("repair, deck operations,", {"size": 11}),
                              ("native tables; in the", {"size": 11}), ("render pool", {"size": 11})],
          kind="code", logos=["powerpoint"], logo_size=18)
    stack(f, 738, yr, 190, 150, [("Check", {"bold": True}), ("geometry from the real", {"size": 11}),
                                 ("render, then a vision", {"size": 11}),
                                 ("verdict per changed page", {"size": 11}),
                                 tier("code, then vision model")], kind="vision")
    f.box(1004, yr, 190, 150, [("Deliver", {"bold": True}), (".pptx, sources appendix,", {"size": 11}),
                               ("the run's trace and scores,", {"size": 11}),
                               ("a summary of open", {"size": 11}),
                               ("issues, if any", {"size": 11})], kind="code")
    f.arrow([(254, yr + 75), (282, yr + 75)])
    f.arrow([(472, yr + 75), (510, yr + 75)])
    f.arrow([(700, yr + 75), (738, yr + 75)])
    f.arrow([(938, yr + 75), (1004, yr + 75)], label="zero blocking", label_at=(971, yr + 66), size=10.5)
    f.text(971, yr + 92, "defects", size=10.5, italic=True, color=MUTED)

    # repair loop
    f.box(510, 528, 418, 44, [("Repair: nudge a frame, re-place the page, or redraft one slide",
                               {"size": 11})], kind="warn")
    f.arrow([(833, yr + 150), (833, 528)], label="blocking", label_at=(839, 518), label_anchor="start",
            size=10.5)
    f.arrow([(510, 550), (377, 550), (377, yr + 150)])
    f.text(445, 568, "changed slides only", size=10.5, italic=True, color=MUTED)
    f.text(719, 596, "at most two rounds; stops early if a round fixes nothing", size=10.5,
           italic=True, color=MUTED)
    f.arrow([(310, yr + 150), (310, yr + 176), (160, yr + 176), (160, yr + 150)], dash="4 3")
    f.text(44, yr + 196, "a slide failing one gate twice is compiled again one tier up", size=10.5,
           italic=True, color=MUTED, anchor="start")

    # trace band
    f.rect(24, 630, 1192, 40, fill="#F7F7F7", stroke="#BFBFBF", r=4)
    f.text(620, 655, "Every step is a span: why it ran, its inputs, model and tier, tokens, cached "
           "tokens, cost, and why it stopped (run_spans, Figure 7)", size=12)

    f.legend(24, 706, [("code", "deterministic code"), ("model", "language model"),
                       ("vision", "vision model"), ("user", "user"), ("warn", "repair")],
             size=11.5)
    f.text(1216, 706, "stacked boxes run in parallel", size=11.5, anchor="end", italic=True,
           color=MUTED)
    return f


# ── Figure 4 ───────────────────────────────────────────────────────────────


def design_selection() -> Figure:
    f = Figure(1240, 820, "How a deck's design language and each page's layout are chosen",
               "Stage I settles one design language for the deck: the uploaded template's "
               "own, or three authored by a model, gated in code and shown as specimens for "
               "the user to choose. Stage II places each page: its material is bound to "
               "every archetype by an exact assignment, infeasible bindings are refused, the "
               "cheapest feasible one wins, and a page nothing can carry is split.")
    sz = 11.5

    # Stage I
    f.group(24, 40, 470, 700, "Stage I · one design language per deck")
    f.box(54, 66, 410, 58, [("Brief and deck format", {"bold": True}),
                            ("a typed value: canvas, type floor, page vocabulary", {"size": sz})],
          kind="code")
    f.box(154, 146, 210, 44, [("A template uploaded?", {"bold": True})], kind="plain", r=20)
    f.arrow([(259, 124), (259, 146)])
    # yes: template
    f.box(54, 222, 180, 92, [("Its measured design", {"bold": True}), ("system, read from the", {"size": sz}),
                             ("slide XML; no authoring", {"size": sz})], kind="code",
          logos=["powerpoint"], logo_size=18)
    f.arrow([(200, 190), (144, 222)], label="yes", label_at=(160, 206), label_anchor="end")
    # no: author
    stack(f, 270, 222, 190, 92, [("Author three languages", {"bold": True}),
                                 ("measure, ramp, colour roles,", {"size": sz}),
                                 ("page kinds; never geometry", {"size": sz})], kind="model", n=2)
    f.arrow([(318, 190), (365, 222)], label="no", label_at=(350, 206), label_anchor="start")
    f.box(270, 338, 190, 118, [("Gates, in code", {"bold": True}), ("WCAG AA contrast", {"size": sz}),
                               ("legibility floor", {"size": sz}), ("derives into a pack", {"size": sz}),
                               ("every page kind compiles", {"size": sz})], kind="code")
    f.arrow([(365, 314), (365, 338)])
    f.arrow([(460, 397), (482, 397), (482, 268), (470, 268)], dash="4 3")
    f.text(476, 330, "fail: re-author", size=10.5, italic=True, color=MUTED, anchor="end")
    stack(f, 270, 486, 190, 78, [("Specimen decks", {"bold": True}), ("three slides, identical copy,", {"size": sz}),
                                 ("so only design differs", {"size": sz})], kind="code", n=2)
    f.arrow([(365, 456), (365, 486)])
    f.box(270, 590, 190, 40, [("User picks one", {"bold": True})], kind="user")
    f.arrow([(365, 564), (365, 590)])
    f.box(54, 650, 410, 70, [("Pack: grid, type ramp, colour roles and page archetypes,", {"size": sz}),
                             ("slot capacities measured by setting copy in the real face;", {"size": sz}),
                             ("every frame seated on one ground", {"size": sz})], kind="code")
    f.arrow([(144, 314), (144, 650)])
    f.arrow([(365, 630), (365, 650)])

    # Stage II
    f.group(530, 40, 686, 700, "Stage II · one layout per page")
    f.arrow([(464, 685), (512, 685), (512, 95), (560, 95)])
    f.box(560, 66, 626, 58, [("Material shape of the page", {"bold": True}),
                             ("kind, items, characters per item, each exhibit's natural aspect", {"size": sz})],
          kind="code")
    stack(f, 560, 152, 300, 150, [("Bind to every archetype", {"bold": True}),
                                  ("an exact assignment of items", {"size": sz}),
                                  ("to slots, per archetype", {"size": sz}),
                                  ("refused, not costed: a required", {"size": sz}),
                                  ("slot left empty, an item left out,", {"size": sz}),
                                  ("an exhibit below its legible floor", {"size": sz})], kind="code", n=2)
    f.arrow([(710, 124), (710, 152)])
    f.box(886, 152, 300, 150, [("Cost of a feasible binding", {"bold": True}),
                               ("slot mismatch, capacity strain,", {"size": sz}),
                               ("exhibit aspect, density deviation,", {"size": sz}),
                               ("reading order, overflow;", {"size": sz}),
                               ("the cheapest wins, ties to the", {"size": sz}),
                               ("page the publication uses most", {"size": sz})], kind="code")
    f.arrow([(870, 227), (886, 227)])
    f.box(560, 330, 300, 62, [("Nothing feasible?", {"bold": True}),
                              ("split the page and bind again", {"size": sz})], kind="warn")
    f.arrow([(700, 330), (700, 302)], both=True)
    f.box(886, 330, 300, 62, [("Still nothing?", {"bold": True}),
                              ("the fallback chain, ending in a plain page", {"size": sz})], kind="warn")
    f.arrow([(860, 361), (886, 361)])
    f.box(560, 420, 626, 62, [("Build, settle and repair", {"bold": True}),
                              ("short material cut to its size and set at the optical centre; "
                               "a monotone patch loop", {"size": sz})], kind="code")
    f.arrow([(1186, 227), (1202, 227), (1202, 451), (1186, 451)])
    f.arrow([(1036, 392), (1036, 420)])
    f.box(560, 510, 626, 62, [("Render, then check", {"bold": True}),
                              ("geometry from the real render; a vision verdict on each changed page", {"size": sz})],
          kind="vision")
    f.arrow([(873, 482), (873, 510)])
    f.box(560, 600, 300, 112, [("Revision ladder", {"bold": True}), ("nudge a frame", {"size": sz}),
                               ("re-place on another archetype", {"size": sz}),
                               ("redraft the slide", {"size": sz}),
                               ("at most twice per page", {"size": sz, "italic": True})], kind="warn")
    f.box(886, 600, 300, 112, [("Keep the better version", {"bold": True}),
                               ("a change is kept only if the", {"size": sz}),
                               ("page scores better than before,", {"size": sz}),
                               ("so a retry never makes a page", {"size": sz}),
                               ("worse", {"size": sz})], kind="plain")
    f.arrow([(710, 572), (710, 600)], label="a defect", label_at=(716, 590), label_anchor="start", size=10.5)
    f.arrow([(560, 656), (544, 656), (544, 186), (560, 186)], dash="4 3")

    f.text(620, 772, "Models choose the content; code decides every position.", size=12.5, italic=True)
    f.legend(24, 806, [("code", "deterministic code"), ("model", "language model"),
                       ("vision", "vision model"), ("user", "user"), ("warn", "fallback or repair")],
             size=11.5)
    return f


# ── Figure 5 ───────────────────────────────────────────────────────────────


def amending() -> Figure:
    f = Figure(1240, 640, "Amending a finished deck",
               "A message after a deck is finished restores the run's snapshot and makes "
               "exactly the change asked for, by the narrowest means that can: a slide's "
               "content goes back to compile, a deck-wide change is a typed operation applied "
               "by every render, and only what neither can say is written as a sandboxed script.")
    sz = 11.5
    f.box(24, 40, 210, 92, [("A later message", {"bold": True}), ("\"add our logo to every", {"size": sz}),
                            ("page but the cover\"", {"size": sz})], kind="user")
    f.box(270, 40, 230, 92, [("Restore the snapshot", {"bold": True}), ("brief, design, slides and the", {"size": sz}),
                             ("rendered file; no phase reruns", {"size": sz})], kind="code",
          logos=["postgresql"], logo_size=16)
    f.arrow([(234, 86), (270, 86)])
    f.box(536, 40, 230, 92, [("Change step", {"bold": True}), ("picks the narrowest means;", {"size": sz}),
                             ("only the tools it may use", {"size": sz}), ("are offered", {"size": sz})],
          kind="model")
    f.arrow([(500, 86), (536, 86)])

    # three lanes
    lanes = [(24, "What a slide says or shows", "model"), (434, "Something across the deck", "code"),
             (844, "Neither: move, rotate, restyle", "warn")]
    for x, title, kind in lanes:
        f.group(x, 196, 372, 300, title)
    f.arrow([(600, 132), (600, 160), (340, 160), (340, 222)])
    f.arrow([(651, 132), (651, 160), (740, 160), (740, 222)])
    f.arrow([(702, 132), (702, 150), (1150, 150), (1150, 222)])

    f.box(44, 222, 332, 70, [("Revise the named slides", {"bold": True}),
                             ("copy, a chart, a figure; the user's specifics", {"size": sz})], kind="model")
    f.box(44, 306, 332, 70, [("Compile them again", {"bold": True}),
                             ("same gates as a first draft (Figure 3)", {"size": sz})], kind="model")
    f.box(44, 390, 332, 86, [("Only those slides re-rendered", {"bold": True}),
                             ("every other page is the page", {"size": sz}),
                             ("the user already approved", {"size": sz})], kind="code")
    f.arrow([(210, 292), (210, 306)])
    f.arrow([(210, 376), (210, 390)])

    f.box(454, 222, 332, 86, [("A typed operation", {"bold": True}),
                              ("overlay (text with live page numbers, a picture", {"size": sz}),
                              ("they supplied, a QR code, a shape, a progress", {"size": sz}),
                              ("bar, a watermark), a recolour, or a link", {"size": sz})], kind="code")
    f.box(454, 322, 332, 70, [("Recorded in the run", {"bold": True}),
                              ("applied by every render, so a re-render keeps it", {"size": sz})], kind="code")
    f.box(454, 406, 332, 70, [("Placed by anchor, clear of content", {"bold": True}),
                              ("shrunk, or left off a page, and said so", {"size": sz})], kind="code")
    f.arrow([(620, 308), (620, 322)])
    f.arrow([(620, 392), (620, 406)])

    f.box(864, 222, 332, 70, [("A script, given layout.json", {"bold": True}),
                              ("every box, free space, colours, type floors", {"size": sz})], kind="model",
          logos=[], logo_size=0)
    f.box(864, 306, 332, 86, [("Three gates", {"bold": True}),
                              ("syntax tree: no import outside an allowlist;", {"size": sz}),
                              ("process: no network, no keys, rlimits;", {"size": sz}),
                              ("artefact: no macro, no OLE, no external link", {"size": sz})], kind="code",
          logos=["python"], logo_size=0)
    f.box(864, 406, 332, 70, [("Kept in the run's code log", {"bold": True}),
                              ("replayed through the same gates on every render", {"size": sz})], kind="code")
    f.arrow([(1030, 292), (1030, 306)])
    f.arrow([(1030, 392), (1030, 406)])

    # validate
    f.box(24, 526, 1192, 54, [("Validate the changed deck", {"bold": True}),
                              ("a finding the deck was handed over with is reported, not fixed again; "
                               "a new one goes back to the step that caused it, once", {"size": sz})],
          kind="vision")
    for x in (210, 620, 1030):
        f.arrow([(x, 496), (x, 526)])
    f.legend(24, 620, [("code", "deterministic code"), ("model", "language model"),
                       ("vision", "vision model"), ("user", "user"), ("group", "one path")], size=11.5)
    return f


# ── Figure 6 ───────────────────────────────────────────────────────────────


def evidence() -> Figure:
    f = Figure(1240, 560, "The evidence store",
               "Research is planned once, searched and fetched in parallel, and kept as "
               "sources, chunks and spans in PostgreSQL. Each storyline section and each "
               "slide retrieves its own slice by hybrid search, and every figure on a slide "
               "is bound to the span it came from.")
    sz = 11.5
    # gather
    f.group(24, 40, 300, 440, "Gather")
    f.box(44, 64, 260, 70, [("Query plan", {"bold": True}),
                            ("one call: queries per open question", {"size": sz})], kind="model")
    stack(f, 44, 166, 250, 70, [("Search × k", {"bold": True}),
                                ("Tavily or the provider's own", {"size": sz})], kind="code", n=2)
    stack(f, 44, 266, 250, 70, [("Fetch × m", {"bold": True}),
                                ("pages, robots respected, cached", {"size": sz})], kind="code", n=2)
    f.box(44, 366, 260, 92, [("User sources", {"bold": True}),
                             ("uploaded documents, pasted text,", {"size": sz}),
                             ("connected MCP servers", {"size": sz})], kind="user",
          logos=["modelcontextprotocol"], logo_size=16)
    f.arrow([(174, 134), (174, 166)])
    f.arrow([(169, 236), (169, 266)])

    # store
    f.group(364, 40, 470, 440, "Store")
    f.box(384, 64, 430, 70, [("Source registry", {"bold": True}),
                             ("URL or file hash, title, fetched-at, licence, who supplied it", {"size": sz})],
          kind="code")
    f.box(384, 160, 430, 70, [("Extraction", {"bold": True}),
                              ("text, tables and figures; cached by content hash", {"size": sz})],
          kind="code")
    f.box(384, 256, 430, 70, [("Chunking", {"bold": True}),
                              ("sections as parents, paragraphs as children, spans kept", {"size": sz})],
          kind="code")
    f.cylinder(384, 350, 430, 108, [("PostgreSQL", {"bold": True}),
                                    ("full-text index (tsvector) for exact terms and figures", {"size": sz}),
                                    ("pgvector HNSW index for meaning", {"size": sz})])
    f.logo("postgresql", 396, 382, 30)
    f.arrow([(299, 201), (330, 201), (330, 90), (384, 90)])
    f.arrow([(299, 301), (340, 301), (340, 104), (384, 104)])
    f.arrow([(304, 412), (350, 412), (350, 118), (384, 118)])
    f.arrow([(599, 134), (599, 160)])
    f.arrow([(599, 230), (599, 256)])
    f.arrow([(599, 326), (599, 350)])

    # use
    f.group(874, 40, 342, 440, "Use")
    f.box(894, 64, 302, 92, [("Hybrid retrieval", {"bold": True}),
                             ("both indexes, fused by reciprocal rank;", {"size": sz}),
                             ("children matched, parents returned", {"size": sz})], kind="code")
    f.box(894, 180, 302, 70, [("Storyline", {"bold": True}),
                              ("key facts per section, with span ids", {"size": sz})], kind="model")
    stack(f, 894, 276, 292, 70, [("Compile × N", {"bold": True}),
                                 ("each slide gets its own slice", {"size": sz})], kind="model", n=2)
    f.box(894, 372, 302, 86, [("Grounding gate", {"bold": True}),
                              ("a figure with no span behind it is", {"size": sz}),
                              ("refused, not drawn", {"size": sz})], kind="code")
    f.arrow([(814, 404), (854, 404), (854, 110), (894, 110)])
    f.arrow([(1045, 156), (1045, 180)])
    f.arrow([(1045, 250), (1045, 276)])
    f.arrow([(1040, 346), (1040, 372)])

    f.box(24, 500, 1192, 40, [("Citations are spans: the Sources appendix and every footnote link "
                               "back to the passage, not to a search snippet", {"size": 12})],
          kind="plain")
    f.arrow([(1045, 458), (1045, 500)])
    f.legend(24, 572, [("code", "deterministic code"), ("model", "language model"),
                       ("user", "user"), ("group", "stage")], size=11.5)
    f.text(1216, 572, "stacked boxes run in parallel", size=11.5, anchor="end", italic=True, color=MUTED)
    f.height = 588
    return f


# ── Figure 7 ───────────────────────────────────────────────────────────────


def data_model() -> Figure:
    f = Figure(1240, 760, "Data model",
               "PostgreSQL is the system of record. Tenancy scopes every row; a session's runs "
               "hold their checkpointed state, its events carry a per-session sequence for SSE "
               "replay, and the evidence store keeps sources and chunks with both a full-text "
               "and a vector index.")
    boxes = {}

    def table(key, x, y, w, name, cols):
        h = 40 + 15.5 * len(cols)
        f.rect(x, y, w, h, fill="#FFFFFF", stroke="#1F2D3D", r=5)
        f.parts.append(f'<path d="M{x},{y + 28} L{x + w},{y + 28}" stroke="#1F2D3D" stroke-width="1"/>')
        f.parts.append(f'<rect x="{x + 0.6}" y="{y + 0.6}" width="{w - 1.2}" height="27" rx="4.5" '
                       f'fill="#DEEBF7"/>')
        f.text(x + 10, y + 19, name, size=12.5, bold=True, anchor="start")
        for i, col in enumerate(cols):
            key_col = col.endswith("*")
            f.text(x + 10, y + 46 + i * 15.5, col.rstrip("*"), size=11,
                   anchor="start", color="#000000" if not key_col else BLUE, italic=False)
        boxes[key] = (x, y, w, h)

    def link(a, b, side_a="r", side_b="l", via=None, label=None):
        ax, ay, aw, ah = boxes[a]
        bx, by, bw, bh = boxes[b]
        pa = {"r": (ax + aw, ay + 14), "l": (ax, ay + 14), "b": (ax + aw / 2, ay + ah),
              "t": (ax + aw / 2, ay)}[side_a]
        pb = {"r": (bx + bw, by + 14), "l": (bx, by + 14), "b": (bx + bw / 2, by + bh),
              "t": (bx + bw / 2, by)}[side_b]
        pts = [pa] + (via or []) + [pb]
        f.arrow(pts, sw=1, label=label, size=10.5)

    f.group(24, 40, 290, 676, "Tenancy")
    table("org", 44, 64, 250, "organisations", ["id*", "name, slug", "settings"])
    table("users", 44, 170, 250, "users", ["id*", "organisation_id", "email, role, password_hash"])
    table("ai", 44, 290, 250, "organisation_ai_settings",
          ["organisation_id*", "provider and model per role", "keys, encrypted at rest"])
    table("conn", 44, 410, 250, "organisation_connections",
          ["id*", "organisation_id", "MCP, image and figure providers", "secrets, encrypted at rest"])
    f.text(169, 600, "every tenant-owned row carries", size=11, italic=True, color=MUTED)
    f.text(169, 615, "organisation_id, and every query", size=11, italic=True, color=MUTED)
    f.text(169, 630, "is scoped by the JWT's claim", size=11, italic=True, color=MUTED)

    f.group(344, 40, 300, 676, "Sessions and runs")
    table("sess", 364, 64, 260, "chat_sessions", ["id*", "organisation_id, user_id", "template_id", "title, status"])
    table("msg", 364, 186, 260, "chat_messages", ["id*", "session_id", "role, content", "attachments, metadata"])
    table("runs", 364, 308, 260, "runs", ["id*", "session_id, message_id", "status, phase, termination",
                                         "state (checkpoint, JSONB)", "stream entry id"])
    table("events", 364, 446, 260, "agent_events", ["id*", "session_id, seq (unique)", "run_id, event_type, phase",
                                                   "raw_event (JSONB)"])
    table("files", 364, 584, 260, "generated_files", ["id*", "session_id, run_id", "file_type, file_path",
                                                    "metadata"])

    f.group(674, 40, 260, 340, "Design")
    table("tpl", 694, 64, 220, "templates", ["id*", "organisation_id", "name, version, file_path"])
    table("dsb", 694, 172, 220, "design_system_bundles", ["id*", "template_id", "palette, typography",
                                                          "layouts, archetypes", "density, data_viz"])

    f.group(674, 420, 260, 296, "Telemetry")
    table("spans", 694, 444, 220, "run_spans", ["id*", "run_id, parent_id", "kind, name, reason, status",
                                               "tokens: input, cached, output", "attrs (JSONB)"])

    f.group(964, 40, 252, 676, "Evidence store")
    table("src", 984, 64, 212, "sources", ["id*", "organisation_id, session_id", "kind: web, upload,", "paste, mcp",
                                          "url or content_hash", "title, fetched_at, licence"])
    table("chunks", 984, 230, 212, "chunks", ["id*", "source_id, parent_id", "content, char span", "tsv (tsvector)",
                                             "embedding (vector)"])
    f.text(1090, 372, "GIN index on tsv", size=11, italic=True, color=MUTED)
    f.text(1090, 388, "HNSW index on embedding", size=11, italic=True, color=MUTED)
    f.text(1090, 404, "children matched,", size=11, italic=True, color=MUTED)
    f.text(1090, 420, "parents returned", size=11, italic=True, color=MUTED)

    link("org", "users", "b", "t")
    link("users", "ai", "b", "t")
    link("ai", "conn", "b", "t")
    link("org", "sess", "r", "l")
    link("sess", "msg", "b", "t")
    link("msg", "runs", "b", "t")
    link("runs", "events", "b", "t")
    link("events", "files", "b", "t")
    link("sess", "tpl", "r", "l")
    link("tpl", "dsb", "b", "t")
    link("runs", "spans", "r", "l", via=[(654, 322), (654, 458)])
    link("sess", "src", "r", "l", via=[(644, 78), (644, 12), (950, 12), (950, 78)])
    link("src", "chunks", "b", "t")
    f.legend(24, 744, [("arrow", "one to many"), ("plain", "table")], size=11.5)
    f.text(250, 748, "key columns in blue; JSONB holds what a run decides, so its shape can grow "
           "without a migration", size=11.5, anchor="start", color=MUTED)
    return f


# ── Figure 8 ───────────────────────────────────────────────────────────────


def evaluation() -> Figure:
    f = Figure(1240, 600, "How a run is measured",
               "Benchmark tasks run headless in one of four modes. Every run leaves a "
               "cassette, a span tree and the deck; deterministic checks and judged grades "
               "score it, and the results store draws the quality and cost frontier per "
               "category and per configuration.")
    sz = 11.5
    f.group(24, 40, 220, 470, "Tasks")
    f.box(44, 64, 180, 426, [("Benchmark suite", {"bold": True}), "",
                             ("per task, in YAML:", {"size": sz}),
                             ("category and brief", {"size": sz}),
                             ("a source pack, so", {"size": sz}),
                             ("research is repeatable", {"size": sz}),
                             ("a checklist of what", {"size": sz}),
                             ("the deck must do", {"size": sz}),
                             ("scripted answers to", {"size": sz}),
                             ("every consultation", {"size": sz}), "",
                             ("consulting, technical,", {"size": sz, "italic": True}),
                             ("academic, pitch, board,", {"size": sz, "italic": True}),
                             ("dashboard, poster ...", {"size": sz, "italic": True})], kind="plain")

    f.group(274, 40, 300, 470, "Runner: python -m app.research")
    f.logo("python", 520, 14, 20)
    modes = [("claude-code", "a Claude Code session answers each request:", "the frontier reference, no API bill",
              "model", ["claude"]),
             ("replay", "recorded answers, a miss fails loudly:", "regression tests at zero cost", "code", []),
             ("record", "real providers, answers kept as cassettes", "for later replay", "code", []),
             ("live", "real providers, per-role models; a spend", "ledger stops a run at its budget", "model",
              ["openai", "anthropic", "zdotai"])]
    for i, (name, a, b, kind, logos) in enumerate(modes):
        y = 64 + i * 108
        f.box(294, y, 260, 92, [(name, {"bold": True}), (a, {"size": 11}), (b, {"size": 11})],
              kind=kind)
        for j, logo in enumerate(logos):
            f.logo(logo, 530 - j * 22, y + 6, 18)
    f.arrow([(224, 277), (274, 277)])

    f.group(604, 40, 270, 470, "A run leaves")
    f.box(624, 64, 230, 426, [("runs/<task>/<run>/", {"bold": True}), "",
                              ("manifest: mode, model,", {"size": sz}),
                              ("commit, spend, termination", {"size": sz}), "",
                              ("cassette of every answer", {"size": sz}), "",
                              ("span tree: why each step ran,", {"size": sz}),
                              ("tokens, cached tokens, cost", {"size": sz}), "",
                              ("prefix ledger: what a cache", {"size": sz}),
                              ("could have answered", {"size": sz}), "",
                              ("the deck and its page images", {"size": sz})], kind="code",
          logos=["powerpoint"], logo_size=20)
    f.arrow([(574, 277), (604, 277)])

    f.group(904, 40, 312, 470, "Scored by")
    f.box(924, 64, 272, 128, [("Checks, in code", {"bold": True}),
                              ("geometry and overflow, body type", {"size": sz}),
                              ("at the floor, figures grounded,", {"size": sz}),
                              ("slide count, the render path,", {"size": sz}),
                              ("rejections kept apart from failures", {"size": sz})], kind="code")
    f.box(924, 206, 272, 112, [("Grades, judged", {"bold": True}),
                               ("the task's checklist, item by item;", {"size": sz}),
                               ("pairwise vision against category", {"size": sz}),
                               ("references, positions swapped", {"size": sz})], kind="vision")
    f.box(924, 332, 272, 70, [("Human calibration set", {"bold": True}),
                              ("checks that judges agree with people", {"size": sz})], kind="user")
    f.box(924, 416, 272, 74, [("No single composite score", {"bold": True}),
                              ("each dimension is reported apart", {"size": sz})], kind="plain")
    f.arrow([(854, 277), (904, 277)])

    f.box(24, 530, 1192, 46, [("Results store: quality, cost and runtime per category and per model configuration",
                               {"size": 12})],
          kind="code")
    f.arrow([(1060, 510), (1060, 530)])
    f.legend(24, 596, [("code", "deterministic code"), ("model", "language model"),
                       ("vision", "vision model"), ("user", "people"), ("plain", "data")], size=11.5)
    f.height = 610
    return f


FIGURES = {
    "system-architecture": system_architecture,
    "request-sse-flow": request_sse_flow,
    "pipeline": pipeline,
    "design-selection": design_selection,
    "amending": amending,
    "evidence": evidence,
    "data-model": data_model,
    "evaluation": evaluation,
}


if __name__ == "__main__":
    names = sys.argv[1:] or list(FIGURES)
    for name in names:
        print(FIGURES[name]().save(name))

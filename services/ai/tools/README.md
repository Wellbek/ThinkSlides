# Local harnesses

Two scripts for looking at real output rather than reading code. Neither is
imported by the service; both exist because the defects that mattered most in
the presentation-quality work (#58 deck ordering, #60 copy contradicting its
own chart, crowded tables) were invisible to unit tests and obvious the
moment a deck was rendered and looked at.

## `render_deck.py` - no LLM, no database

Renders a slide-JSON deck straight through `PPTXRenderer`, converts it with
the same LibreOffice call the preview pipeline uses, and writes one PNG per
slide.

```
cd services/ai
PYTHONPATH=. .venv/bin/python tools/render_deck.py <deck.json> <outdir> [--theme "#00704A"]
```

`<deck.json>` is `{"metadata": {...}, "colorTheme": "...", "slides": [...]}`.
The decks in `tests/test_regression_decks.py` are ready-made examples.

## `e2e.py` - the real agent loop, a real provider

Drives `AgentLoop` end to end against the dev Postgres/Redis and whichever
provider the organisation has configured in Settings, then renders previews.

```
docker compose up -d postgres redis

cd services/ai
env $(grep -E '^(SETTINGS_ENCRYPTION_KEY|TAVILY_API_KEY|WEB_SEARCH_PROVIDER|ZHIPU_API_KEY|LLM_PROVIDER)=' ../../.env | xargs) \
  PYTHONPATH=. .venv/bin/python tools/e2e.py "<prompt>" <outdir> [--mode deep] [--detail comprehensive]
```

The provider comes from `organisation_ai_settings`, **not** from `.env`;
`SETTINGS_ENCRYPTION_KEY` is what decrypts its stored API key. It picks the
first organisation that has a row there.

Every tool call and result is printed and written to `<outdir>/events.log`,
the deck lands at `<outdir>/deck.pptx`, and the slides at
`<outdir>/png/bench/slide_*.png`.

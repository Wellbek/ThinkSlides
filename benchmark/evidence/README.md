# Evidence runs

The headless runs that [`docs/DESIGN_DECISIONS.md`](../../docs/DESIGN_DECISIONS.md) cites, kept so every claim in it can be checked.
Ordinary runs land in `benchmark/runs/`, which is not committed; a run is copied here when a document or issue relies on it.

Each run keeps three files:

- `cassette.jsonl` - every model answer and search result the run received, in order;
- `manifest.json` - the task, commit, mode, model and spend;
- `checks.json` - the run's deterministic checks and metrics.

Rendered decks and page images are not kept; replaying the cassette regenerates them.

## Replaying a run

```
cd services/ai
PYTHONPATH=. .venv/bin/python -m app.research run ../../benchmark/tasks/<category>/<task>.yaml \
    --mode replay --cassette ../../benchmark/evidence/<task>/<run>/cassette.jsonl
```

A replay feeds the recorded answers to the current code, so it is free and exercises the deterministic engine.
It cannot show the effect of a prompt change, since the answers are fixed.

## The language gate audit

```
cd services/ai
PYTHONPATH=. .venv/bin/python -m app.research.gate_audit ../../benchmark/evidence
```

It lists every design language these runs authored, with what today's gate says about each.

# Benchmark

Tasks, prices and spend for measuring the pipeline headless.
The design is in `docs/PROJECT_PLAN.md` sections 12 and 17; the runner is `services/ai/app/research`, documented in `services/ai/tools/README.md`.

- `tasks/<category>/<id>.yaml` - one brief each: prompt, mode, depth, scripted answers and the binary checklist the deck is held to.
  The category is the directory name, and every deck is compared only against its own category.
- `prices.json` - dated, sourced per-model prices; a model not listed cannot run live.
- `budget.json` - the total all live runs may spend.
- `ledger.jsonl` - one line per live run: calls, tokens, cost. Appended by the runner, never edited by hand.
- `runs/` - run output, not committed.

Live runs validate what already passed in `claude-code` mode; they do not replace it.

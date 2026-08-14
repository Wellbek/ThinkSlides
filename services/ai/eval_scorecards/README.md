# Style-fidelity scorecards

Scorecards produced by the harness in `app/eval`, scoring rendered output against the reference deck's held-out validation pages.

Two scores per deck, deliberately never merged into one:

- **Style** - does the output look like the reference: density, type scale, palette, grounds, chrome, apparatus, chart treatment, archetype proximity.
- **Substance** - is there anything in it: headlines that state something, slides that say different things, figures that carry a source.

Substance is the guard on style.
Density can always be raised by repeating filler, and every style metric improves when it is; the substance group is what falls instead.
A change that lifts style while dropping substance made the deck worse, and the scorecard has to make that visible.

## `baseline-main/`

Recorded on `main`'s renderer before any of the consultancy-deck epic (#112) landed, so later runs are a diff rather than an assertion.

| deck | style | substance |
| --- | --- | --- |
| numbers | 0.384 | 0.500 |
| metrics | 0.365 | 0.500 |
| qualitative | 0.321 | 0.500 |

The interesting rows are the ones near zero: `blocks_per_slide` 2 against the reference's 11.5, `words_per_slide` 31 against 195, and `ground_split`, `apparatus`, `informative_titles` and `sourced_figures` all at 0.
That is the gap the epic exists to close, stated as numbers.

## Regenerating

```
python -m app.eval baseline                 # score the fixture decks
python -m app.eval score path/to/deck.pptx  # score any rendered deck
python -m app.eval build-reference          # remeasure the reference deck
```

`build-reference` needs the reference PDF, at `$THINKSLIDES_REFERENCE_PDF` or in `~/Downloads`.
Everything else runs off the committed measurements in `app/eval/fixtures/reference_profile.json` and needs no network and no PDF.

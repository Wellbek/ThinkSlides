# First baseline: technical/http-caching

Date: 2026-09-24.
Commit: `c660200` (integration branch, after #306 and #307).
One task, two conditions, one run each - a smoke test of the measurement loop, not a result anyone should generalise from.

## Conditions

| | frontier reference | cheap model |
| --- | --- | --- |
| Mode | `claude-code` | `live` |
| Model | Claude Code session | gpt-5.6-luna |
| Run | `20260924-152618-claude-code` (commit `0a3e8f7`, before #306/#307) | `20260924-191957-live` |
| Tokens are | estimated (3.5 characters per token) | measured |

## Efficiency

| | frontier reference | cheap model |
| --- | --- | --- |
| Model calls | 26 | 32 |
| Input tokens | ~153k (estimated) | 208k, of which 58k cached |
| Output tokens | ~7k (estimated) | 11k |
| Cost | $0 (subscription); ~$0.04 if billed at luna prices | $0.045 |
| Wall time | 10 min, paced by hand-answered requests | 3 min |
| Validation rounds | 3, ended at the cap unresolved | 3, ended at the cap unresolved |

The drafting part dominates both: 28 of the cheap run's 32 calls and 202k of its 208k input tokens.

## Checklist (task file)

| Item | frontier reference | cheap model |
| --- | --- | --- |
| Exactly 3 slides | yes | yes |
| max-age, no-cache and no-store explained and contrasted | yes | yes |
| ETag / If-None-Match / 304 exchange shown as a sequence | no - the table and sequence diagram were refused (#301) | no - prose only |
| stale-while-revalidate or stale-if-error at a CDN | yes | yes |
| No figure without a source | yes | yes |
| Titles state what the slide shows | yes | yes |
| **Passed** | **5 of 6** | **5 of 6** |

## Defects seen

- Frontier reference: two slides with the lower third to half of the page empty after their visuals were refused (#301); a flow diagram squeezed until terms break mid-word; a revision loop that redrafted one slide blind and drafted again after the cap (#299).
- Cheap model: the same flow diagram twice on one slide (#312); body copy on slide 3 shrunk to caption size; an accent rule under every title; one of three slides spent on a cover.
- Both: validation did not converge (#299).

## What this says

On a conceptual three-slide deck the cheap model matched the frontier reference on the checklist for about four and a half cents, in a third of the wall time.
The remaining failures on both sides are system failures, not model ones: refused visuals (#301), a blind revision loop (#299) and duplicated exhibits (#312).
That is consistent with the plan's working hypothesis and nothing more; a real comparison needs the ten tasks, repeats, and consulting briefs where research and figures matter.

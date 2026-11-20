[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Owlvans/gta6-trophy-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Owlvans/gta6-trophy-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

# GTA VI Trophy Rarity Forecaster

Entry for make.fun's **gta6-trophy-rarity-forecast-01** ("Forecast how many
GTA VI players earn each trophy in week one").

`main.py` (Python 3.13, standard library only, deterministic, offline) reads
each case at `/input/<id>.in` — the house's recording of Exophase's
Grand Theft Auto VI (PS5) trophy page: every read's per-trophy earn rate
(percent of tracked players who earned it) and the players-tracked count —
and writes `/output/<id>.out` with five quantiles (5%, 25%, 50%, 75%, 95%)
of each trophy's earn rate at the case's target. Scoring is the mean pinball
loss on the logit scale, so rare trophies count as much as common ones and
an honest spread beats a confident point guess.

## Method (v2)

An earn rate is a ratio, **earners / players tracked**, and Exophase does
not move it smoothly: the page's aggregates sit frozen between tracker
refreshes and jump at each refresh, when the tracked pool grows (dilution)
and the earners accumulated since the last refresh land at once
(progression). The forecaster therefore works on the case's **distinct
states** (the steps), decomposes every step into a players move and a
per-trophy earners move, and extrapolates the two separately:

* **Players** — a log-log growth fit over the recent states, blended with
  the recent per-hour growth rate damped over the horizon.
* **Earners** — per trophy, a through-origin fit of the step deltas
  `dE ~ a·dt + b·dN` (accrual of the existing pool plus new players
  earning), shrunk toward pooled priors; the launch catch-up step is
  excluded from the fit (a different regime whose `dN` dwarfs every later
  step). Accrual is integrated over a saturating effective horizon and
  capped by a per-trophy ceiling from the recent states.
* **Ensemble** — that step-decomposition median is blended 50/50 in logit
  space with the v1 dynamic median (shrunk logit trend + earners per
  player-hour), the two model families hedging each other.
* **Story funnel** — medians along the "Complete Chapter k" chain (and
  the final mission) are repaired to be non-increasing, and the platinum
  median is capped at the rarest other median, as both must hold in
  reality.
* **Spread** — per-step logit volatility of the rates scaled by the
  expected number of remaining steps (a jump process, not a diffusion),
  with a floor, plus players-growth uncertainty, calibrated on
  backtests.

**Sparse histories** (fewer than three distinct states, like the
launch-night practice cases) show no steps, so a launch-transition prior
takes over. One completion fraction `s(h)` — 0.62 at the 1.5 h practice
horizon, the value that reproduces both published practice moments of
the house baseline (loss ≈ 1.51, typical miss ≈ 0.68 pp), and 1.0 at
12.93 h — drives both sides of the observed transition (pool ×22 to the
14,028-player post-surge state; per-trophy earners growth fitted as
`ln G = 0.68 + 1.60 · ln(frozen rate)`, residual sd 0.53), because a
refresh lands the players growth and the earners growth together. v1's
prior applied the full transition at any horizon, which is why it
overshot the practice answers.

## Local validation

`score.py` reimplements the contest's exact pinball-on-logit loss and
both house baselines. Results (v2 vs v1):

| Backtest | v2 | v1 | Baselines |
|---|---|---|---|
| Real holdout: frozen history → live page 2026-11-19 15:56 UTC | **0.2529** | 0.2523 | persistence 2.3638 |
| Practice proxy (answers at the calibrated 62% transition point) | **0.307** | 0.624 | — |
| Synthetic finals, launch-fitted step simulator, 12 seeds | **0.179** | 0.221 | persistence 0.392, trend 0.322 |
| Synthetic finals, continued-surge stress regime, 6 seeds | **0.164** | 0.186 | persistence 0.294, trend 0.325 |

Determinism (byte-identical reruns), output format, edge cases (single
read, empty history, all-null rates, zero horizon) and runtime
(~0.06 s for both practice cases against an 8 s limit) all pass; see
`AUDIT.md` for the full audit, including two independent external model
audits of the design.

## Files

* `main.py` — the entry (v2).
* `score.py` — local scorer + house baselines (research only).
* `run_tests.py`, `synth_test.py`, `synth_test2.py` — v1 validation harnesses.
* `backtests/` — v2 step-process simulator, ensemble grid search, and
  the v1 entry kept for comparison.
* `live_20261119T1556Z.json` — the live Exophase snapshot used as holdout.
* `AUDIT.md` — method, numbers and audit checks.

## License

MIT — see `LICENSE`.

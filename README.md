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

## Method

An earn rate is a ratio, **earners / players tracked**, and the two sides
move for different reasons: players keep finishing trophies (progression
pushes rates up) while Exophase keeps adding tracked players (dilution
pushes rates down). The forecaster models both sides and blends them in
logit space:

* **Trend view** — per-trophy least-squares line of logit(rate) against
  ln(1 + hours since release), slopes shrunk toward the pooled
  cross-trophy slope, re-anchored at the last read.
* **Earners view** — earners (= rate × players / 100) grow with cumulative
  player-hours at a decelerating rate; the player count is extrapolated
  with a damped log-log fit; the rate is recomposed as earners / players
  at the target. This is what catches dilution when the tracked base
  surges. The earners view is winsorized to ±1 logit around the trend
  view and weighted 25%.
* **Spread** — pooled random-walk volatility per √day, the per-trophy fit
  residual, and a calibration factor, so the 5–95% band covers honestly.

**Frozen histories.** Exophase refreshes its aggregates in steps, and the
launch-night recording is a single frozen snapshot repeated (638 players,
every read identical). When a case's history carries almost no distinct
states, no dynamics are observable and the next refresh dilutes the rates.
A refresh prior — calibrated on the observed launch-night transition from
that frozen snapshot to the live page — shifts each trophy's logit by
−5.354 + 1.585 · ln(earners), blended in by a frozenness weight that
vanishes once the history shows real dynamics.

## Local validation

`score.py` reimplements the contest's exact pinball-on-logit loss and both
house baselines (persistence, recent-curve). On the one real holdout
available locally — the frozen launch-night history forecast to the live
Exophase page of 2026-11-19 15:56 UTC — the model scores **0.2523** against
**2.3638** for both baselines. On two synthetic dynamic-regime cases
(progression-driven and dilution-surge) it scores **0.120** and **0.124**
against the trend baseline's 0.225 / 0.230. See `AUDIT.md` for the full
audit: determinism (byte-identical reruns), format checks, edge cases and
runtime (~0.1 s per case against an 8 s limit).

## Files

* `main.py` — the entry.
* `score.py` — local scorer + house baselines (research only).
* `run_tests.py`, `synth_test.py`, `synth_test2.py` — validation harnesses.
* `live_20261119T1556Z.json` — the live Exophase snapshot used as holdout.
* `AUDIT.md` — method, numbers and audit checks.

## License

MIT — see `LICENSE`.

# Audit — gta6-trophy-rarity-forecast-01 entry

## Destination and submission status

* This repository is destined for the user's own GitHub account,
  **github.com/Owlvans**, and from there to be entered on make.fun under
  the user's account **@owl**.
* Whether `Owlvans` is the GitHub account linked to the make.fun account
  @owl **still needs verifying at submission time** (the bounty requires
  the repository be owned by the GitHub account linked to the entrant's
  make.fun account).
* **Nothing has been pushed and no remote repository has been created.**
  Submission is the user's decision alone; this build is local only.

## Rules and case format — sources

Verified 2026-11-19 from primary sources, not memory:

* `GET https://make.fun/api/competitions/gta6-trophy-rarity-forecast-01`
  — title, deadline **2026-11-21T11:30:00Z**, $250 pot over 20 places,
  resolution text (harness program/v1, reads every 10 min / 30 on launch
  night, rates clamped 0.01–99.99, score = mean of
  2·max(τ·d, (τ−1)·d) with d = logit(y) − logit(q), to 6 decimals).
* The competition kit (`https://make.fun/c/<slug>/kit`, bundle sha256
  bc675240…): `RULES.txt` (scoring §32–37, house baselines §45–47),
  `starter/main.py` (the persistence house baseline, number for number),
  `input/p00.in`, `input/p01.in` (practice cases), `input/NOTICE.txt`
  (data provenance: Exophase's GTA VI PS5 trophy page).
* `GET …/requirements` — deliverables are a repo URL + commit SHA +
  transcript bundle at submission time (not part of this local build).

**Case format** (`makefun/gta6-trophies-case/v1`): input JSON with
`trophies` (55: id, name, description, tier, points, secret), `history`
(reads oldest first: slot, at, players_tracked, trophies_earned, rates —
rates in trophy order, null where a read lacked the trophy), `at`,
`horizon_minutes`, `target`, `levels` [0.05, 0.25, 0.5, 0.75, 0.95].
Output: `{"trophies": [{"id": …, "q": [q05, q25, q50, q75, q95]}]}`,
one row per trophy, q finite in [0, 100], non-decreasing, ≤ 65,536 bytes.
Invalid output is scored as persistence and takes no prize. Limits:
8 s, 1 CPU, 1024 MB per case; two runs of a case must be byte-identical.
Final case f00 is cut at the deadline from all reads before it; target
2026-11-26T00:00:00Z.

## What the data actually looks like (key finding)

Both practice cases' histories are **one frozen snapshot repeated**:
638 players tracked, 943 trophies earned, identical rates in all 7–8
reads (2026-11-18T23:50Z → 2026-11-19T03:00Z). Earners per trophy are
tiny integers (4–13; one trophy at 100%). RULES.txt confirms the design:
every rate moved at "the refresh" after the history window, which is why
both house baselines score ~1.51 on the practice. The live Exophase page
fetched 2026-11-19T15:56Z (saved as `live_20261119T1556Z.json`) shows the
post-refresh world: almost all rates diluted ~10–20× (median logit shift
−2.94; shift correlates 0.64 with ln(earners); two early trophies rose on
progression). The final case's history (through 2026-11-21) will contain
real dynamics, so the model handles both regimes (see README "Method").

## Holdout results (local scorer = exact contest loss, lower is better)

| Holdout | This model | Persistence (house) | Trend (house) |
|---|---|---|---|
| H1: frozen history (p01) → live rates 2026-11-19T15:56Z, 746 min horizon | **0.252349** (coverage90 0.945, miss 0.06pp) | 2.3638 | 2.3638 |
| Synth 1: progression-driven dynamics, 119 reads, 6510 min horizon | **0.120187** (coverage90 0.945) | 0.30646 | 0.224738 |
| Synth 2: dilution surge (players ×~12 late), 6510 min horizon | **0.124317** (coverage90 1.000) | 0.3086 | 0.2295 |

Notes: the real practice answers (reads at 04:30Z / 05:00Z on launch
night) are not published in the kit, so H1 uses the live page ~11 h
after p01's target as the answer — same refresh regime, longer horizon.
The synthetic cases validate the dynamic-regime code paths and spread
calibration against known ground truth; they are not evidence about the
real final case. The third house entry (an AI model's median/spread)
cannot be replicated locally.

## Audit checks (run twice over, per instructions)

| Check | Result |
|---|---|
| Determinism: p00 + p01 run twice, outputs compared | PASS — byte-identical |
| Determinism: 355-read final-like case run twice | PASS — byte-identical, 2,917 bytes out |
| Output format: 55 rows, ids a permutation of the case's, 5 quantiles each, finite, in [0, 100], non-decreasing, ≤ 65,536 bytes | PASS on practice + all edge cases |
| Edge: single read / all-null rates / one trophy null throughout / empty history / all-zero rates with 0 players / final-like 6,510 min horizon from 3 reads | PASS — valid output every time |
| Runtime: p00+p01 together 0.06 s; 355-read case 0.12 s (limit 8 s per case) | PASS |
| Offline / stdlib only: imports are json, math, os, sys, datetime, pathlib; no network, no randomness, no wall-clock reads (times come only from the case), no file access beyond /input → /output | PASS |
| Spread honesty: coverage90 0.945 / 0.945 / 1.000 across holdouts (target ≈ 0.90) | PASS (slightly wide on synth 2) |
| Model beats both house baselines on every local holdout | PASS (table above) |

## Known limitations

* No local access to the house's post-launch-night recordings: the final
  case's real dynamics (refresh cadence, player growth over Nov 19–21)
  cannot be rehearsed exactly. The dynamic model is therefore kept
  conservative (shrunk slopes, winsorized earners view, capped
  extrapolation ±4 logit).
* The frozen-refresh prior is calibrated on a single observed transition
  (n = 54 trophies, residual sd 0.51 logit); it only governs histories
  with almost no distinct states, which the final case is not expected
  to be.
* Practice-case scores on make.fun's server may differ from H1 because
  the practice answers are the 04:30Z / 05:00Z reads, which are not
  public. Practice pays nothing; the final case decides the prizes.

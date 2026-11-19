#!/usr/bin/env python3
"""Synthetic dynamic-regime test: invents a plausible Exophase process
(stepwise player growth, per-trophy saturation in average player-hours),
cuts a case at 59.5h after release with target at 168h (mirroring the
final case f00), and scores main.py against both house baselines.
Research tool only; validates code paths and calibration, not real accuracy."""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score import BASELINES, pinball_loss, score_forecast_fn  # noqa: E402
from run_tests import run_main, rows_of  # noqa: E402

RELEASE_MIN = 0  # minutes since 2026-11-19T00:00Z


def ts(minutes):
    d = int(minutes // 1440)
    h = int((minutes % 1440) // 60)
    m = int(minutes % 60)
    return f"2026-11-{19 + d:02d}T{h:02d}:{m:02d}:00Z"


# archetypes: (ceiling share, tau in avg player-hours) x counts = 55 trophies
ARCH = ([(0.85, 6.0)] * 3 + [(0.6, 14.0)] * 6 + [(0.4, 30.0)] * 10
        + [(0.18, 60.0)] * 14 + [(0.07, 120.0)] * 12 + [(0.015, 260.0)] * 10)
assert len(ARCH) == 55


def players(t_min):
    """Tracked players: smooth growth with refresh steps every ~7h."""
    base = 900.0 * (1.0 + t_min / 1440.0) ** 1.25 + 600.0
    step = 1.0 + 0.06 * int(t_min // 420)
    return base * step


def rates_at(t_min):
    n = players(t_min)
    # average player-hours per tracked player grows ~ sqrt of time since release
    avg_h = 2.2 * math.sqrt(max(t_min, 1) / 60.0)
    out = []
    for ceil, tau in ARCH:
        share = ceil * (1.0 - math.exp(-avg_h / tau))
        out.append(round(min(99.99, max(0.0, 100.0 * share)), 2))
    return out, n


def build_case(cut_min=3570, step_min=30):
    hist = []
    t = 30
    while t <= cut_min:
        rates, n = rates_at(t)
        hist.append({
            "slot": ts(t), "at": ts(t), "players_tracked": int(n),
            "trophies_earned": int(sum(rates) * n / 100),
            "rates": rates,
        })
        t += step_min
    trophies = [{"id": i + 1, "name": f"T{i + 1}", "description": "", "tier": "bronze",
                 "points": 15, "secret": False} for i in range(55)]
    return {
        "schema": "makefun/gta6-trophies-case/v1", "id": "synth",
        "at": ts(cut_min), "horizon_minutes": 10080 - cut_min,
        "target": ts(10080), "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "game": {}, "trophies": trophies, "history": hist, "notes": "synthetic",
    }


def main():
    case = build_case()
    actual, _ = rates_at(10080)
    outs, dt = run_main({"synth": case}, "synth")
    loss, cov, miss = pinball_loss(rows_of(outs["synth"]), actual)
    print(f"synthetic dynamic case: {len(case['history'])} reads, horizon {case['horizon_minutes']} min, runtime {dt:.2f}s")
    print(f"{'MODEL':>12}: loss={loss} coverage90={cov:.3f} typical_miss={miss}pp")
    for name, fn in BASELINES.items():
        l2, c2, m2 = score_forecast_fn(fn, case, actual)
        print(f"{name:>12}: loss={l2} coverage90={c2:.3f} typical_miss={m2}pp")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Second synthetic scenario: dilution surge. The tracked-player count
explodes late (a big Exophase refresh wave) while progression is slow, so
most rates FALL toward the target despite earners growing. A pure trend
extrapolation should struggle; the earners/players view should help.
Research tool only."""
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score import BASELINES, pinball_loss, score_forecast_fn  # noqa: E402
from run_tests import run_main, rows_of  # noqa: E402
from synth_test import ts  # noqa: E402

ARCH = ([(0.5, 20.0)] * 8 + [(0.3, 45.0)] * 12 + [(0.15, 90.0)] * 15
        + [(0.05, 180.0)] * 12 + [(0.01, 400.0)] * 8)
assert len(ARCH) == 55


def players(t_min):
    base = 1500.0 * (1.0 + t_min / 1440.0) ** 0.8
    if t_min > 2400:  # refresh wave after 40h: x6 over the next day, then x2
        base *= 1.0 + 5.0 * min(1.0, (t_min - 2400) / 1440.0)
    if t_min > 5000:
        base *= 2.0
    return base


def rates_at(t_min):
    n = players(t_min)
    avg_h = 1.6 * math.sqrt(max(t_min, 1) / 60.0)
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
        hist.append({"slot": ts(t), "at": ts(t), "players_tracked": int(n),
                     "trophies_earned": int(sum(rates) * n / 100), "rates": rates})
        t += step_min
    trophies = [{"id": i + 1, "name": f"T{i + 1}", "description": "", "tier": "bronze",
                 "points": 15, "secret": False} for i in range(55)]
    return {"schema": "makefun/gta6-trophies-case/v1", "id": "synth2",
            "at": ts(cut_min), "horizon_minutes": 10080 - cut_min,
            "target": ts(10080), "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
            "game": {}, "trophies": trophies, "history": hist, "notes": "synthetic2"}


def main():
    case = build_case()
    actual, _ = rates_at(10080)
    outs, dt = run_main({"synth2": case}, "synth2")
    loss, cov, miss = pinball_loss(rows_of(outs["synth2"]), actual)
    print(f"synthetic dilution-surge case: horizon {case['horizon_minutes']} min, runtime {dt:.2f}s")
    print(f"{'MODEL':>12}: loss={loss} coverage90={cov:.3f} typical_miss={miss}pp")
    for name, fn in BASELINES.items():
        l2, c2, m2 = score_forecast_fn(fn, case, actual)
        print(f"{name:>12}: loss={l2} coverage90={c2:.3f} typical_miss={m2}pp")


if __name__ == "__main__":
    main()

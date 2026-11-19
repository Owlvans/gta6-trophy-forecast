#!/usr/bin/env python3
"""Local scoring harness for gta6-trophy-rarity-forecast-01.

Implements the exact contest score (RULES.txt): mean pinball loss on the logit
scale over every (trophy, level), rates clamped to 0.01..99.99, 6 decimals.
Also implements the two house baselines:
  - persistence: the kit starter, number for number.
  - trend: per RULES.txt line 46 -- OLS of logit(rate) vs ln(1 + hours since
    2026-11-19T00:00:00Z) over the case's last 24h of reads, read at the target,
    spread = random-walk spread combined with the fit's residual sd
    (root-sum-of-squares); fewer than 3 reads -> persistence.
Research tool only; not part of the entry.
"""
import json
import math
from datetime import datetime, timezone

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]
RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)


def clamp(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(l):
    return 100.0 / (1.0 + math.exp(-l))


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pinball_loss(forecast_rows, actual_rates):
    """forecast_rows: list of [q05..q95] per trophy; actual_rates: list of rates.
    Returns (loss rounded 6dp, coverage90, typical_miss_pp)."""
    total = 0.0
    n = 0
    covered = 0
    miss = 0.0
    for q, y in zip(forecast_rows, actual_rates):
        ly = logit(y)
        for tau, qq in zip(LEVELS, q):
            d = ly - logit(qq)
            total += 2.0 * max(tau * d, (tau - 1.0) * d)
            n += 1
        if q[0] <= y <= q[-1]:
            covered += 1
        miss += abs(y - q[2])
    nt = len(actual_rates)
    return round(total / n, 6), covered / nt, round(miss / nt, 2)


def round4(x):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    v = whole / 10000
    return int(v) if float(v).is_integer() else v


# ---------------- house baseline 1: persistence (kit starter) ----------------

def persistence_rows(case):
    last = case["history"][-1]
    spread = 0.5 * math.sqrt(case["horizon_minutes"] / 1440.0)
    rows = []
    for i in range(len(case["trophies"])):
        rate = last["rates"][i]
        lg = logit(rate if rate is not None else 0)
        rows.append([round4(inv_logit(lg + z * spread)) for z in Z])
    return rows


# ---------------- house baseline 2: recent-curve (trend) ----------------

def _ols(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return my, 0.0, [y - my for y in ys]
    b = sxy / sxx
    a = my - b * mx
    return a, b, [y - (a + b * x) for x, y in zip(xs, ys)]


def trend_rows(case):
    hist = case["history"]
    if len(hist) < 3:
        return persistence_rows(case)
    at = parse_ts(case["at"])
    target = parse_ts(case["target"])
    days = case["horizon_minutes"] / 1440.0
    rw = 0.5 * math.sqrt(days)
    rows = []
    for i in range(len(case["trophies"])):
        xs, ys = [], []
        for read in hist:
            t = parse_ts(read["at"])
            if (at - t).total_seconds() > 24 * 3600:
                continue
            r = read["rates"][i]
            if r is None:
                continue
            hrs = (t - RELEASE).total_seconds() / 3600.0
            xs.append(math.log(1.0 + max(0.0, hrs)))
            ys.append(logit(r))
        if len(xs) < 3:
            rate = hist[-1]["rates"][i]
            lg = logit(rate if rate is not None else 0)
            rows.append([round4(inv_logit(lg + z * rw)) for z in Z])
            continue
        a, b, resid = _ols(xs, ys)
        sd = math.sqrt(sum(e * e for e in resid) / max(1, len(resid) - 2))
        hrs_t = (target - RELEASE).total_seconds() / 3600.0
        med = a + b * math.log(1.0 + max(0.0, hrs_t))
        spread = math.sqrt(rw * rw + sd * sd)
        rows.append([round4(inv_logit(med + z * spread)) for z in Z])
    return rows


BASELINES = {"persistence": persistence_rows, "trend": trend_rows}


def score_forecast_fn(fn, case, actual_rates):
    rows = fn(case)
    return pinball_loss(rows, actual_rates)


if __name__ == "__main__":
    import sys

    case = json.load(open(sys.argv[1]))
    actual = json.load(open(sys.argv[2]))["rates"]
    for name, fn in BASELINES.items():
        loss, cov, miss = score_forecast_fn(fn, case, actual)
        print(f"{name:>12}: loss={loss} coverage90={cov:.3f} typical_miss={miss}pp")

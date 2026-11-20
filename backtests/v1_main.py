"""GTA VI trophy rarity forecaster for make.fun's gta6-trophy-rarity-forecast-01.

Python 3.13, standard library only, deterministic, offline. Reads every
<id>.in in the input directory and writes <id>.out with five quantiles
(0.05, 0.25, 0.5, 0.75, 0.95) of each trophy's Exophase earn rate (percent)
at the case's target.

Method
------
An earn rate is a ratio, earners / players_tracked, and the two move for
different reasons: players keep earning trophies (progression pushes rates
up) while Exophase keeps adding tracked players (dilution pushes rates
down). The forecaster therefore models both sides:

* a per-trophy trend of logit(rate) against ln(1 + hours since release),
  with slopes shrunk toward the pooled cross-trophy slope, re-anchored at
  the last read;
* an earners model: earners grow with cumulative player-hours, the player
  count is extrapolated on a log-log fit, and the rate is recomposed as
  earners / players at the target.

The two medians are blended in logit space. The spread combines a pooled
random-walk volatility (per sqrt day), the per-trophy fit residual and a
calibration factor, so the 5%-95% band is an honest one.

Frozen histories: Exophase refreshes its aggregates in steps, and the
launch-night recording is one frozen snapshot repeated. When a case's
history carries (almost) no distinct states, no dynamics are observable
and the next refresh dilutes the rates. A refresh prior, calibrated on
the observed launch-night transition (frozen snapshot of 2026-11-19
03:00Z vs the live page at 15:56Z), shifts each trophy's logit by
-5.354 + 1.585 * ln(earners) with its own spread, blended in by a
frozenness weight that vanishes once the history shows real dynamics.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# Frozen-refresh prior, calibrated on the launch-night transition (see docstring).
FROZEN_SHIFT_A = -5.354
FROZEN_SHIFT_B = 1.585
FROZEN_RESID_SD = 0.55

CAL = 1.05  # global spread calibration
RW_FALLBACK = 0.5  # random-walk logit sd per sqrt day when unmeasurable


def clamp_rate(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(l):
    if l >= 0:
        return 100.0 / (1.0 + math.exp(-l))
    e = math.exp(l)
    return 100.0 * e / (1.0 + e)


def round4(x):
    """Round to 4 decimals, half up (matches the kit starter)."""
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    v = whole / 10000
    return int(v) if float(v).is_integer() else v


def z_for_level(tau):
    """Standard normal quantile (Acklam's approximation, refined once)."""
    if tau <= 0.0:
        return -4.0
    if tau >= 1.0:
        return 4.0
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285984469534e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if tau < plow:
        q = math.sqrt(-2 * math.log(tau))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif tau <= phigh:
        q = tau - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - tau))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    # one Halley refinement step
    e = 0.5 * (1 + math.erf(x / math.sqrt(2))) - tau
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def ols(xs, ys):
    """Least squares y ~ a + b x. Returns (a, b, resid_sd)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 1e-12:
        b = 0.0
        a = my
    else:
        b = sxy / sxx
        a = my - b * mx
    if n > 2:
        sd = math.sqrt(sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / (n - 2))
    elif n == 2:
        sd = abs(ys[1] - ys[0]) * 0.5
    else:
        sd = 0.0
    return a, b, sd


def median(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return 0.0
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def forecast_case(case):
    trophies = case.get("trophies") or []
    history = case.get("history") or []
    levels = case.get("levels") or LEVELS
    zs = [z_for_level(t) for t in levels]

    target_dt = parse_ts(case.get("target")) or RELEASE
    horizon_min = float(case.get("horizon_minutes") or 0)
    if horizon_min <= 0:
        at_dt = parse_ts(case.get("at"))
        horizon_min = max(0.0, (target_dt - at_dt).total_seconds() / 60.0) if at_dt else 0.0
    horizon_days = horizon_min / 1440.0
    x_target = math.log(1.0 + max(0.0, (target_dt - RELEASE).total_seconds() / 3600.0))

    # ---- reads: (hours since release, players, [rates]) ----
    reads = []
    for read in history:
        t = parse_ts(read.get("at") or read.get("slot"))
        hrs = (t - RELEASE).total_seconds() / 3600.0 if t else (reads[-1][0] if reads else 0.0)
        n_players = read.get("players_tracked") or 0
        reads.append((hrs, float(n_players), read.get("rates") or []))
    if not reads:
        # No data at all: a wide, low prior for every trophy.
        rows = []
        for tr in trophies:
            m = logit(1.0)
            rows.append({"id": tr["id"], "q": [round4(clamp_rate(inv_logit(m + z * 1.6))) for z in zs]})
        return {"trophies": rows}

    nt = len(trophies)
    # carry-forward fill of nulls per trophy
    filled = []
    last_seen = [None] * nt
    for hrs, npl, rates in reads:
        row = []
        for i in range(nt):
            r = rates[i] if i < len(rates) else None
            if r is not None:
                last_seen[i] = float(r)
            row.append(last_seen[i])
        filled.append((hrs, npl, row))

    # ---- distinct states (N, rates) ----
    distinct = []
    prev_key = None
    for hrs, npl, row in filled:
        key = (npl, tuple(row))
        if key != prev_key:
            distinct.append((hrs, npl, row))
            prev_key = key
    if distinct[-1] is not filled[-1]:
        distinct.append(filled[-1])
    n_distinct = len(distinct)
    frozen_w = max(0.0, 1.0 - (n_distinct - 2) / 8.0)  # 1.0 at <=2 distinct, 0 at >=10

    last_hrs, last_n, last_row = filled[-1]
    last_n = last_n if last_n > 0 else 1.0

    # ---- cumulative player-hours over all reads (trapezoid) ----
    H = [0.0]
    for j in range(1, len(filled)):
        dt = max(0.0, filled[j][0] - filled[j - 1][0])
        H.append(H[-1] + 0.5 * (filled[j - 1][1] + filled[j][1]) * dt)
    H_last = H[-1]

    # ---- players forecast: log-log fit on distinct positive-N points ----
    n_target = last_n
    pts = [(math.log(1.0 + max(0.0, h)), math.log(npl)) for h, npl, _ in distinct if npl > 0]
    if len(pts) >= 2:
        a_n, b_n, _ = ols([p[0] for p in pts], [p[1] for p in pts])
        b_n = min(2.0, max(0.0, b_n)) * 0.85
        n_pred = math.exp(a_n + b_n * x_target)
        n_target = min(max(n_pred, last_n), last_n * 60.0)
    H_target = H_last + 0.5 * (last_n + n_target) * max(0.0, horizon_min / 60.0)

    # ---- pooled slope and pooled step volatility from distinct points ----
    slopes = []
    step_vars = []
    per_trophy = []
    for i in range(nt):
        xs, ys, es = [], [], []
        for hrs, npl, row in distinct:
            r = row[i]
            if r is None:
                continue
            xs.append(math.log(1.0 + max(0.0, hrs)))
            ys.append(logit(r))
            es.append(clamp_rate(r) * npl / 100.0 if npl > 0 else 0.0)
        per_trophy.append((xs, ys, es))
        if len(xs) >= 3:
            _, b_i, _ = ols(xs, ys)
            slopes.append(b_i)
        for j in range(1, len(xs)):
            dx = xs[j] - xs[j - 1]
            if dx > 1e-9:
                step_vars.append(((ys[j] - ys[j - 1]) ** 2) / (dx * 7.0))  # per ln-hour^2 -> rough daily scale
    pooled_slope = median(slopes) if slopes else 0.0
    if step_vars:
        rw = math.sqrt(median(step_vars))
        rw = min(1.5, max(0.15, rw))
    else:
        rw = RW_FALLBACK

    # cross-trophy fallback median logit (for never-observed trophies)
    seen_logits = [logit(r) for r in last_row if r is not None]
    fallback_logit = median(seen_logits) if seen_logits else logit(1.0)

    rows = []
    for i, tr in enumerate(trophies):
        xs, ys, es = per_trophy[i]
        r_last = last_row[i]
        if r_last is None or not xs:
            m = fallback_logit
            sigma = math.sqrt((rw * math.sqrt(max(horizon_days, 0.01))) ** 2 + 0.8 ** 2) * CAL
            rows.append({"id": tr["id"], "q": _quantiles(m, sigma, zs)})
            continue
        y_last = logit(r_last)
        e_last = clamp_rate(r_last) * last_n / 100.0

        # --- dynamic median: shrunk trend, re-anchored at last read ---
        if len(xs) >= 3:
            _, b_raw, sd_i = ols(xs, ys)
        elif len(xs) == 2:
            b_raw = (ys[1] - ys[0]) / (xs[1] - xs[0]) if xs[1] > xs[0] else 0.0
            sd_i = 0.3
        else:
            b_raw, sd_i = 0.0, 0.3
        w_slope = len(xs) / (len(xs) + 4.0)
        b_shrunk = min(3.0, max(-3.0, w_slope * b_raw + (1 - w_slope) * pooled_slope))
        m_line = y_last + b_shrunk * (x_target - xs[-1])

        # --- dynamic median: earners / player-hours ---
        m_earn = None
        # cumulative player-hours at each distinct read: walk all reads and
        # record the running trapezoid sum whenever the state changes
        Hd = [0.0]
        acc = 0.0
        prev_h, prev_n = filled[0][0], filled[0][1]
        state_key = (filled[0][1], tuple(filled[0][2]))
        Hd.append(0.0)
        cur_key = state_key
        for hrs, npl, row in filled[1:]:
            acc += 0.5 * (prev_n + npl) * max(0.0, hrs - prev_h)
            prev_h, prev_n = hrs, npl
            key = (npl, tuple(row))
            if key != cur_key:
                Hd.append(acc)
                cur_key = key
        if len(Hd) != len(distinct):
            Hd = [H_last * (j + 1) / max(1, len(distinct)) for j in range(len(distinct))]
        hds, eds = [], []
        for j, (hrs, npl, row) in enumerate(distinct):
            if row[i] is not None and npl > 0:
                hds.append(Hd[j])
                eds.append(clamp_rate(row[i]) * npl / 100.0)
        if len(hds) >= 2 and H_target > hds[-1]:
            # recent earners growth rate per player-hour (last up-to-6 points, through-origin on deltas)
            k = min(len(hds) - 1, 6)
            num = sum((eds[j] - eds[j - 1]) * (hds[j] - hds[j - 1]) for j in range(len(hds) - k, len(hds)))
            den = sum((hds[j] - hds[j - 1]) ** 2 for j in range(len(hds) - k, len(hds)))
            if den > 1e-9:
                s_e = max(0.0, num / den)
                # earning decelerates as the pool of unearned players drains:
                # damp the per-player-hour slope by sqrt(H_last / H_target)
                damp = math.sqrt(hds[-1] / H_target) if H_target > 0 else 1.0
                e_pred = e_last + s_e * damp * (H_target - hds[-1])
                # earners cannot outrun the player base by an unbounded factor
                e_pred = min(e_pred, n_target * 0.9999, e_last * (n_target / last_n) * 6.0 + 10.0)
                if e_pred > 0:
                    m_earn = logit(100.0 * e_pred / n_target)
                    # winsorize: the earners view may correct the trend view
                    # (dilution when players surge), never replace it wholesale
                    m_earn = min(m_line + 1.0, max(m_line - 1.0, m_earn))
        if m_earn is not None:
            m_dyn = 0.75 * m_line + 0.25 * m_earn
        else:
            m_dyn = m_line
        # damp wild extrapolations
        m_dyn = y_last + min(4.0, max(-4.0, m_dyn - y_last))

        # --- frozen-refresh prior ---
        if r_last >= 99.9:
            m_frozen = y_last
        else:
            shift = FROZEN_SHIFT_A + FROZEN_SHIFT_B * math.log(max(e_last, 0.5))
            shift = min(0.5, max(-4.5, shift))
            m_frozen = y_last + shift

        m = frozen_w * m_frozen + (1.0 - frozen_w) * m_dyn

        # --- spread ---
        resid_sd = min(1.2, sd_i) if len(xs) >= 3 else 0.3
        var = (rw * math.sqrt(max(horizon_days, 0.01))) ** 2
        var += ((1.0 - frozen_w) * resid_sd) ** 2
        var += (frozen_w * FROZEN_RESID_SD) ** 2
        sigma = math.sqrt(var) * CAL
        sigma = min(3.0, max(0.08, sigma))
        rows.append({"id": tr["id"], "q": _quantiles(m, sigma, zs)})
    return {"trophies": rows}


def _quantiles(m, sigma, zs):
    qs = [round4(clamp_rate(inv_logit(m + z * sigma))) for z in zs]
    for j in range(1, len(qs)):
        if qs[j] < qs[j - 1]:
            qs[j] = qs[j - 1]
    return qs


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()

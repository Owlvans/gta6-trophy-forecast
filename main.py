"""GTA VI trophy rarity forecaster v2 for make.fun's gta6-trophy-rarity-forecast-01.

Python 3.13, standard library only, deterministic, offline. Reads every
<id>.in in the input directory and writes <id>.out with five quantiles
(0.05, 0.25, 0.5, 0.75, 0.95) of each trophy's Exophase earn rate (percent)
at the case's target.

Method
------
An earn rate is a ratio, earners / players_tracked, and Exophase does not
move it smoothly: the page's aggregates sit frozen between refreshes and
jump at each refresh, when the tracker pool grows (dilution) and the
earners accumulated since the last refresh land at once (progression).
The forecaster therefore works on the case's distinct states (the steps),
decomposes every step into a players move and a per-trophy earners move,
and extrapolates the two separately to the target:

* Players: a log-log growth fit over the recent distinct states,
  blended with the recent per-hour growth rate damped over the horizon.
* Earners: every observed step is decomposed per trophy into accrual
  of the existing pool (a * dt) plus new players earning (b * dN) by a
  through-origin fit, shrunk toward pooled priors; the target earners
  integrate the accrual over a saturating effective horizon (the pool
  of future earners drains), add b * (players growth), and respect a
  per-trophy ceiling from its own observed rates. The resulting median
  is blended 50/50 with the v1 dynamic median (a shrunk logit trend
  blended with an earners/player-hours view, ported below) as a hedge
  between the two model families.
* Story funnel: rates along the "Complete Chapter k" chain (and the
  final mission) are repaired to be non-increasing, and the platinum
  rate is capped at the rarest other rate, as both must hold in reality.

Sparse histories (one or two distinct states, like the launch-night
practice cases) show no steps, so a launch-transition prior takes over,
calibrated on the one transition that is public: the frozen launch
snapshot (638 players, 2026-11-19T03:00Z) versus the refreshed page
(14,028 players, rates as printed from 2026-11-19T15:56Z on). Its two
parts are horizon-scaled, because a refresh's effect grows with the
time it has had to happen:

* one completion fraction s(h) (a saturating ramp, 0.62 at the 1.5 h
  practice horizon — the value that reproduces both published practice
  moments of the house baseline — and 1.0 at 12.93 h) drives both
  sides, because a refresh lands the players growth and the earners
  growth together: the players cover the fraction s of the remaining
  surge to the observed post-surge pool (x22 from the frozen pool);
* the earners growth G_i(h) = 1 + (Ghat_i - 1) * s(h), where the full
  transition growth Ghat_i = exp(0.68 + 1.60 * ln(rate0_i)) is the fit
  of the observed per-trophy earners growth on the frozen rate (the
  frozen rate measures how early in the game a trophy sits), with the
  tutorial trophy growing with the pool itself and the platinum barely
  growing at all.

Spreads are honest about the regime: step-to-step logit volatility of
the rates scaled by sqrt horizon, plus players-forecast uncertainty,
plus the transition fit's residual (0.53 in log earners) in the sparse
regime; a global calibration factor tuned on backtests closes the loop.
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# --- launch-transition prior (calibration: see docstring) ---
SURGE_FULL = 22.0        # players multiplier of the observed full transition
SURGE_TAU_H = 1.55       # ramp constant of the transition in log space:
                         # s(1.5 h) = 0.62 reproduces both published
                         # practice moments of the house baseline
SURGE_H_FULL = 12.93     # hours over which the full transition was observed
POOL_POST = 14028.0      # players in the observed post-surge state
POOL_PRE = 638.0         # players in the frozen launch snapshot
GROWTH_A = 0.68          # ln Ghat = A + B * ln(rate0 in percent)
GROWTH_B = 1.60
GROWTH_RESID_SD = 0.53   # residual sd of that fit, in log earners
CAL = 0.6                # dynamic-path spread calibration (grid-tuned;
                         # the sims alone would take it lower, 0.6 keeps
                         # margin for real-world model risk)
CAL_SPARSE = 0.9         # sparse-path (launch prior) spread calibration
TAIL_SLOPE = 0.35        # late log-log slope of players growth
# dynamic-ensemble weights (grid-searched on synthetic finals in both
# regimes plus the real launch-transition holdout)
W_STEP = 0.5             # step-decomposition median
W_V1 = 0.5               # v1 dynamic median (ported below)
EXCL_FIRST_STEP = True   # exclude the launch catch-up step from the
                         # a/b decomposition fit: it is a different
                         # regime and its dN dwarfs every later step
CAP_TIGHT = True         # ceiling from the recent states, not from
                         # pre-surge rates that can never bind again


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


def chapter_of(desc):
    """Story order from a trophy description: chapter k -> k, the final
    mission -> 90, anything else -> 0 (not on the story chain)."""
    if not desc:
        return 0
    m = re.search(r"Chapter\s+(\d+)", desc)
    if m:
        return int(m.group(1))
    if "final mission" in desc and "Complete" in desc:
        return 90
    return 0


def _quantiles(m, sigma, zs):
    qs = [round4(clamp_rate(inv_logit(m + z * sigma))) for z in zs]
    for j in range(1, len(qs)):
        if qs[j] < qs[j - 1]:
            qs[j] = qs[j - 1]
    return qs


def _states(case, nt):
    """Distinct states of the history: (hours since release, players,
    [rates]) with nulls carried forward, oldest first."""
    history = case.get("history") or []
    reads = []
    for read in history:
        t = parse_ts(read.get("at") or read.get("slot"))
        hrs = (t - RELEASE).total_seconds() / 3600.0 if t else (reads[-1][0] if reads else 0.0)
        reads.append((hrs, float(read.get("players_tracked") or 0), read.get("rates") or []))
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
    states = []
    prev_key = None
    for hrs, npl, row in filled:
        key = (npl, tuple(row))
        if key != prev_key:
            states.append((hrs, npl, row))
            prev_key = key
    if filled and states and states[-1] is not filled[-1]:
        states.append(filled[-1])
    return filled, states


def _forecast_players(states, x_last, x_target, h_hours):
    """Players at the target: a log-log fit over the recent states
    blended with the recent per-hour growth rate damped over the
    horizon. Returns (n_target, ln_growth)."""
    last_n = states[-1][1] if states[-1][1] > 0 else 1.0
    pts = [(math.log(1.0 + max(0.0, h)), math.log(n)) for h, n, _ in states if n > 0]
    growths = []
    if len(pts) >= 2:
        recent = pts[-5:]
        a_n, b_n, _ = ols([p[0] for p in recent], [p[1] for p in recent])
        b_fit = min(2.5, max(0.0, b_n)) * 0.85
        growths.append(b_fit * (x_target - x_last))
        # recent per-hour growth across the last two steps, damped
        (h1, n1, _), (h2, n2, _) = (states[-3] if len(states) >= 3 else states[0]), states[-1]
        if h2 > h1 and n1 > 0 and n2 > 0:
            rho = (math.log(n2) - math.log(n1)) / (h2 - h1)
            rho = min(0.05, max(0.0, rho))
            growths.append(rho * h_hours / (1.0 + h_hours / 72.0))
    if not growths:
        growths.append(TAIL_SLOPE * max(0.0, x_target - x_last))
    ln_growth = sum(growths) / len(growths)
    ln_growth = min(max(0.0, ln_growth), math.log(8.0))
    return last_n * math.exp(ln_growth), ln_growth


def _sparse_forecast(i, tr, r_last, n_last, h_hours, x_last, x_target, zs,
                     fallback_logit):
    """Launch-transition prior for histories with < 3 distinct states."""
    if r_last is None:
        sigma = 1.6
        return fallback_logit, sigma
    e_last = clamp_rate(r_last) * n_last / 100.0
    # --- one completion fraction s(h) drives both sides: a refresh
    # lands the players growth and the earners growth together ---
    s = (1.0 - math.exp(-h_hours / SURGE_TAU_H)) / (1.0 - math.exp(-SURGE_H_FULL / SURGE_TAU_H))
    s = min(1.0, max(0.0, s))
    # --- players: cover the fraction s of the remaining surge to the
    # observed post-surge pool; late tail growth only past the surge
    # window, so the two never double-count ---
    if n_last < POOL_POST:
        ln_mult = s * math.log(POOL_POST / max(n_last, 1.0))
    else:
        ln_mult = 0.0
    last_hrs = (math.exp(x_last) - 1.0)
    x_surge_end = math.log(1.0 + last_hrs + SURGE_H_FULL)
    tail = math.exp(TAIL_SLOPE * max(0.0, x_target - x_surge_end))
    tail = min(tail, 4.0)
    n_target = n_last * math.exp(ln_mult) * tail
    # --- earners: fitted full-transition growth, same ramp s ---
    if r_last >= 99.9:
        g_full = math.exp(ln_mult) * tail  # the tutorial trophy grows with the pool
    elif tr.get("tier") == "platinum":
        g_full = 1.05
    else:
        g_full = math.exp(GROWTH_A + GROWTH_B * math.log(max(r_last, 0.05)))
        g_full = min(g_full, 25.0)
    g = 1.0 + (g_full - 1.0) * s
    e_target = e_last * g
    rate = 100.0 * e_target / max(n_target, 1.0)
    m = logit(rate)
    # --- spread: transition-fit residual (earners side), path/timing
    # uncertainty (peaks mid-transition), and a floor ---
    var = (GROWTH_RESID_SD * s) ** 2
    var += (0.9 * s * (1.0 - s)) ** 2
    var += 0.12 ** 2
    sigma = min(2.5, max(0.1, math.sqrt(var) * CAL_SPARSE))
    return m, sigma


def forecast_case(case):
    trophies = case.get("trophies") or []
    levels = case.get("levels") or LEVELS
    zs = [z_for_level(t) for t in levels]
    nt = len(trophies)

    target_dt = parse_ts(case.get("target")) or RELEASE
    horizon_min = float(case.get("horizon_minutes") or 0)
    if horizon_min <= 0:
        at_dt = parse_ts(case.get("at"))
        horizon_min = max(0.0, (target_dt - at_dt).total_seconds() / 60.0) if at_dt else 0.0
    h_hours = horizon_min / 60.0
    h_days = horizon_min / 1440.0
    x_target = math.log(1.0 + max(0.0, (target_dt - RELEASE).total_seconds() / 3600.0))

    filled, states = _states(case, nt)
    if not filled or not states:
        rows = []
        for tr in trophies:
            m = logit(1.0)
            rows.append({"id": tr["id"], "q": _quantiles(m, 1.6, zs)})
        return {"trophies": rows}

    last_hrs, last_n_raw, last_row = states[-1]
    last_n = last_n_raw if last_n_raw > 0 else 1.0
    x_last = math.log(1.0 + max(0.0, last_hrs))
    seen_logits = [logit(r) for r in last_row if r is not None]
    fallback_logit = median(seen_logits) if seen_logits else logit(1.0)

    n_states = len(states)
    sparse = n_states < 3

    # ---- players forecast (dynamic path; sparse path recomputes its own)
    n_target, ln_growth_n = _forecast_players(states, x_last, x_target, h_hours)

    # ---- step statistics for the dynamic path ----
    step_dlogit = []          # per-step logit(rate) moves, time-normalised
    step_moves = []           # raw per-step logit moves after the first
    step_dts_after = []       # durations of those steps
    steps_i = [[] for _ in range(nt)]   # per-trophy (dt_hours, dN, dE)
    steps_fit = [[] for _ in range(nt)]  # steps used for the a/b fit
    for k in range(1, n_states):
        h1, n1, row1 = states[k - 1]
        h2, n2, row2 = states[k]
        dt = max(0.0, h2 - h1)
        if dt <= 0:
            continue
        for i in range(nt):
            r1, r2 = row1[i], row2[i]
            if r1 is None or r2 is None:
                continue
            step_dlogit.append((logit(r2) - logit(r1)) / math.sqrt(dt / 24.0))
            if k >= 2:
                step_moves.append(logit(r2) - logit(r1))
        if k >= 2:
            step_dts_after.append(dt)
            if n1 > 0 and n2 > 0:
                e1 = clamp_rate(r1) * n1 / 100.0
                e2 = clamp_rate(r2) * n2 / 100.0
                steps_i[i].append((dt, n2 - n1, e2 - e1))
                if not (EXCL_FIRST_STEP and k == 1 and n_states >= 4):
                    steps_fit[i].append((dt, n2 - n1, e2 - e1))
    if step_moves and step_dts_after:
        # Jump-process spread from the post-launch steps: raw per-step
        # moves scaled by the expected number of remaining steps. (The
        # launch step and short-step sqrt(dt) normalisation both explode
        # this estimate; the first step is excluded, as in the a/b fit.)
        rw_raw = math.sqrt(median([d * d for d in step_moves]))
        rw_raw = min(1.5, max(0.05, rw_raw))
        med_dt = median(step_dts_after)
        n_steps = h_hours / med_dt if med_dt > 0 else 1.0
        step_var = (rw_raw * math.sqrt(max(n_steps, 0.25))) ** 2
        # floor: at least one more launch-era-sized step may still come
        step_var = max(step_var, (0.35 * math.sqrt(max(h_days, 0.01))) ** 2)
    elif step_dlogit:
        rw = math.sqrt(median([d * d for d in step_dlogit]))
        rw = min(1.5, max(0.1, rw))
        step_var = (rw * math.sqrt(max(h_days, 0.01))) ** 2
    else:
        step_var = (0.5 * math.sqrt(max(h_days, 0.01))) ** 2

    # pooled step-decomposition priors: dE ~ a * dt (accrual of the
    # existing pool, scaled by headroom) + b * dN (new players earning)
    def _decomp(steps):
        """Through-origin 2-param fit of dE on (dt, dN); None if the
        design is degenerate."""
        s_tt = sum(d * d for d, _, _ in steps)
        s_tn = sum(d * dn for d, dn, _ in steps)
        s_nn = sum(dn * dn for _, dn, _ in steps)
        s_te = sum(d * de for d, _, de in steps)
        s_ne = sum(dn * de for _, dn, de in steps)
        det = s_tt * s_nn - s_tn * s_tn
        if abs(det) < 1e-9 or s_tt <= 0:
            return None
        a = (s_te * s_nn - s_ne * s_tn) / det
        b = (s_ne * s_tt - s_te * s_tn) / det
        return a, b

    raw_fits = [_decomp(steps_fit[i]) if len(steps_fit[i]) >= 3 else None
                for i in range(nt)]
    headrooms = []
    for i in range(nt):
        obs = [row[i] for _, _, row in states if row[i] is not None]
        if CAP_TIGHT and len(obs) >= 2:
            cap_r = min(99.99, max(obs[-2:]) * 2.0 + 1.0, max(obs) * 1.5 + 2.0)
        else:
            cap_r = min(99.99, max(obs) * 1.5 + 2.0) if obs else 99.99
        headrooms.append(max(1.0, cap_r / 100.0 * last_n -
                             (clamp_rate(last_row[i]) * last_n / 100.0 if last_row[i] is not None else 0.0)))
    a_rel = [f[0] / headrooms[i] for i, f in enumerate(raw_fits)
             if f is not None and f[0] > 0]
    b_raw = [f[1] for _, f in enumerate(raw_fits) if f is not None and f[1] > 0]
    pool_a_rel = median(a_rel) if a_rel else 0.0
    pool_b = median(b_raw) if b_raw else 0.0
    rates_seen = [r for r in last_row if r is not None]
    med_rate = median(rates_seen) if rates_seen else 1.0

    # effective accrual horizon: saturating (future-earner pool drains)
    h_eff = 96.0 * (1.0 - math.exp(-h_hours / 96.0)) if h_hours > 0 else 0.0
    d_n = max(0.0, n_target - last_n)

    v1_meds = None
    if not sparse:
        v1_meds = _v1_medians(filled, states, nt, x_target, horizon_min,
                              last_n, fallback_logit)

    medians = [None] * nt
    sigmas = [0.5] * nt
    for i, tr in enumerate(trophies):
        r_last = last_row[i]
        if sparse:
            m, sigma = _sparse_forecast(i, tr, r_last, last_n, h_hours,
                                        x_last, x_target, zs, fallback_logit)
            medians[i], sigmas[i] = m, sigma
            continue
        if r_last is None:
            medians[i] = fallback_logit
            sigmas[i] = math.sqrt(step_var + 0.8 ** 2) * CAL
            continue
        e_last = clamp_rate(r_last) * last_n / 100.0
        # --- step-decomposition median ---
        steps = steps_fit[i]
        prior_a = pool_a_rel * headrooms[i]
        prior_b = min(0.95, pool_b * (r_last / med_rate)) if med_rate > 0 else pool_b
        fit = _decomp(steps) if len(steps) >= 3 else None
        if fit is not None:
            w = len(steps) / (len(steps) + 2.0)
            a_i = w * fit[0] + (1 - w) * prior_a
            b_i = w * fit[1] + (1 - w) * prior_b
        else:
            a_i, b_i = prior_a, prior_b
        a_i = max(0.0, a_i)
        b_i = min(0.98, max(0.0, b_i))
        e_pred = e_last + a_i * h_eff + b_i * d_n
        obs_rates = [row[i] for _, _, row in states if row[i] is not None]
        if CAP_TIGHT and len(obs_rates) >= 2:
            cap_rate = min(99.99, max(obs_rates[-2:]) * 2.0 + 1.0,
                           max(obs_rates) * 1.5 + 2.0)
        else:
            cap_rate = min(99.99, max(obs_rates) * 1.5 + 2.0) if obs_rates else 99.99
        e_pred = min(e_pred, n_target * cap_rate / 100.0)
        if r_last < 99.9:
            e_pred = max(e_pred, e_last)
        m_step = logit(100.0 * e_pred / max(n_target, 1.0))
        medians[i] = W_STEP * m_step + W_V1 * v1_meds[i]
        var = step_var
        var += (0.30 * ln_growth_n) ** 2
        var += 0.15 ** 2
        sigmas[i] = min(2.5, max(0.1, math.sqrt(var) * CAL))

    # ---- story-funnel repair on the medians (logit space) ----
    chain = sorted((chapter_of(tr.get("description")), i)
                   for i, tr in enumerate(trophies) if chapter_of(tr.get("description")) > 0)
    run_min = None
    for _, i in chain:
        if medians[i] is None:
            continue
        if run_min is None or medians[i] < run_min:
            run_min = medians[i]
        else:
            medians[i] = run_min
    plat = [i for i, tr in enumerate(trophies) if tr.get("tier") == "platinum"]
    others = [medians[i] for i in range(nt) if i not in plat and medians[i] is not None]
    if plat and others:
        cap = min(others)
        for i in plat:
            if medians[i] is not None and medians[i] > cap:
                medians[i] = cap

    rows = []
    for i, tr in enumerate(trophies):
        m = medians[i] if medians[i] is not None else fallback_logit
        rows.append({"id": tr["id"], "q": _quantiles(m, sigmas[i], zs)})
    return {"trophies": rows}


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


# ---------------------------------------------------------------------
# v1 dynamic component, ported unchanged in behaviour: a shrunk
# logit-trend median blended with an earners/player-hours median, mixed
# with the v1 frozen prior by the frozenness weight. It is one of three
# components of the v2 dynamic ensemble (see forecast_case).
# ---------------------------------------------------------------------
V1_FROZEN_A = -5.354
V1_FROZEN_B = 1.585


def _v1_medians(filled, states, nt, x_target, horizon_min, last_n,
                fallback_logit):
    n_distinct = len(states)
    frozen_w = max(0.0, 1.0 - (n_distinct - 2) / 8.0)
    last_row = states[-1][2]

    H = [0.0]
    for j in range(1, len(filled)):
        dt = max(0.0, filled[j][0] - filled[j - 1][0])
        H.append(H[-1] + 0.5 * (filled[j - 1][1] + filled[j][1]) * dt)
    H_last = H[-1]

    n_target = last_n
    pts = [(math.log(1.0 + max(0.0, h)), math.log(n)) for h, n, _ in states if n > 0]
    if len(pts) >= 2:
        a_n, b_n, _ = ols([p[0] for p in pts], [p[1] for p in pts])
        b_n = min(2.0, max(0.0, b_n)) * 0.85
        n_pred = math.exp(a_n + b_n * x_target)
        n_target = min(max(n_pred, last_n), last_n * 60.0)
    H_target = H_last + 0.5 * (last_n + n_target) * max(0.0, horizon_min / 60.0)

    slopes = []
    per_trophy = []
    for i in range(nt):
        xs, ys = [], []
        for hrs, npl, row in states:
            r = row[i]
            if r is None:
                continue
            xs.append(math.log(1.0 + max(0.0, hrs)))
            ys.append(logit(r))
        per_trophy.append((xs, ys))
        if len(xs) >= 3:
            _, b_i, _ = ols(xs, ys)
            slopes.append(b_i)
    pooled_slope = median(slopes) if slopes else 0.0

    # player-hours at each distinct state (v1's walk, kept as it was)
    Hd = [0.0]
    acc = 0.0
    prev_h, prev_n = filled[0][0], filled[0][1]
    Hd.append(0.0)
    cur_key = (filled[0][1], tuple(filled[0][2]))
    for hrs, npl, row in filled[1:]:
        acc += 0.5 * (prev_n + npl) * max(0.0, hrs - prev_h)
        prev_h, prev_n = hrs, npl
        key = (npl, tuple(row))
        if key != cur_key:
            Hd.append(acc)
            cur_key = key
    if len(Hd) != len(states):
        Hd = [H_last * (j + 1) / max(1, len(states)) for j in range(len(states))]

    out = []
    for i in range(nt):
        xs, ys = per_trophy[i]
        r_last = last_row[i]
        if r_last is None or not xs:
            out.append(fallback_logit)
            continue
        y_last = logit(r_last)
        e_last = clamp_rate(r_last) * last_n / 100.0
        if len(xs) >= 3:
            _, b_raw, _sd = ols(xs, ys)
        elif len(xs) == 2:
            b_raw = (ys[1] - ys[0]) / (xs[1] - xs[0]) if xs[1] > xs[0] else 0.0
        else:
            b_raw = 0.0
        w_slope = len(xs) / (len(xs) + 4.0)
        b_shrunk = min(3.0, max(-3.0, w_slope * b_raw + (1 - w_slope) * pooled_slope))
        m_line = y_last + b_shrunk * (x_target - xs[-1])
        m_earn = None
        hds, eds = [], []
        for j, (hrs, npl, row) in enumerate(states):
            if row[i] is not None and npl > 0:
                hds.append(Hd[j])
                eds.append(clamp_rate(row[i]) * npl / 100.0)
        if len(hds) >= 2 and H_target > hds[-1]:
            k = min(len(hds) - 1, 6)
            num = sum((eds[j] - eds[j - 1]) * (hds[j] - hds[j - 1])
                      for j in range(len(hds) - k, len(hds)))
            den = sum((hds[j] - hds[j - 1]) ** 2
                      for j in range(len(hds) - k, len(hds)))
            if den > 1e-9:
                s_e = max(0.0, num / den)
                damp = math.sqrt(hds[-1] / H_target) if H_target > 0 else 1.0
                e_pred = e_last + s_e * damp * (H_target - hds[-1])
                e_pred = min(e_pred, n_target * 0.9999,
                             e_last * (n_target / last_n) * 6.0 + 10.0)
                if e_pred > 0:
                    m_earn = logit(100.0 * e_pred / n_target)
                    m_earn = min(m_line + 1.0, max(m_line - 1.0, m_earn))
        m_dyn = 0.75 * m_line + 0.25 * m_earn if m_earn is not None else m_line
        m_dyn = y_last + min(4.0, max(-4.0, m_dyn - y_last))
        if r_last >= 99.9:
            m_frozen = y_last
        else:
            shift = V1_FROZEN_A + V1_FROZEN_B * math.log(max(e_last, 0.5))
            shift = min(0.5, max(-4.5, shift))
            m_frozen = y_last + shift
        out.append(frozen_w * m_frozen + (1.0 - frozen_w) * m_dyn)
    return out

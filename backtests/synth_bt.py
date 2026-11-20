#!/usr/bin/env python3
"""Synthetic step-process backtest for the GTA VI forecast rebuild.

Simulates the Exophase page as it actually behaves: the tracked pool and
the earners sit frozen between refreshes and jump at each refresh. The
simulation starts from the real frozen launch state (p01's last read:
638 players, earners from the printed rates) and runs the step process
forward with randomized latent parameters. A case is cut at +59.5 h
(like the final case f00) with the target at +168 h; the answer is the
simulated page state at the target.

Regimes:
  realistic - first refresh catches the pool up to ~14k (as observed),
              then slow steps every 10-30 h with small multipliers.
  boom      - stress regime: the crawl keeps surging all week.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
from score import pinball_loss, persistence_rows, trend_rows  # noqa: E402
import importlib.util  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


v1 = load(os.path.join(_HERE, 'v1_main.py'), 'v1')
v2 = load(os.path.join(os.path.dirname(_HERE), 'main.py'), 'v2')

BASE = json.load(open(os.path.join(os.path.dirname(_HERE), 'kit',
                      'gta6-trophy-rarity-forecast-01-kit', 'input', 'p01.in')))
TROPHIES = BASE['trophies']
REL = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
CHAIN = sorted((i for i, tr in enumerate(TROPHIES)
                if v2.chapter_of(tr.get('description')) > 0),
               key=lambda i: v2.chapter_of(TROPHIES[i].get('description')))


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def simulate(seed, regime='realistic'):
    rng = random.Random(seed * 7919 + (0 if regime == 'realistic' else 13))
    last = BASE['history'][-1]
    n = float(last['players_tracked'])
    earn = [max(0.3, r * n / 100.0) for r in last['rates']]
    cap, kappa, newbie = [], [], []
    for i, tr in enumerate(TROPHIES):
        r0 = last['rates'][i] / 100.0
        if i == 1:
            cap.append(1.0); kappa.append(0.0); newbie.append(1.0)
        elif tr.get('tier') == 'platinum':
            cap.append(0.004); kappa.append(0.001); newbie.append(0.0)
        else:
            cap.append(min(0.95, r0 * rng.uniform(6.0, 26.0)))
            kappa.append(rng.uniform(0.0015, 0.012) * (0.4 + r0 * 30.0))
            newbie.append(min(0.9, r0 * rng.uniform(4.0, 22.0)))
    if regime == 'boom':
        n_cap = rng.uniform(70000.0, 160000.0)
        refreshes, t = [4.5], 4.5
        while t < 168.0:
            t += rng.uniform(7.0, 22.0)
            refreshes.append(t)
        mult = (0.05, 0.75)
        first_jump = None
    else:
        n_cap = rng.uniform(26000.0, 60000.0)
        refreshes, t = [4.5], 4.5
        while t < 168.0:
            t += rng.uniform(10.0, 30.0)
            refreshes.append(t)
        mult = (0.02, 0.22)
        first_jump = 14028.0 * rng.uniform(0.9, 1.1)
    states = [(3.0, n, list(earn))]
    prev_t = 3.0
    for k, rt in enumerate(refreshes):
        dt = rt - prev_t
        if k == 0 and first_jump is not None:
            n_new = first_jump
        else:
            dn = n * (math.exp(rng.uniform(*mult)) - 1.0) * max(0.05, 1.0 - n / n_cap)
            n_new = n + dn
        dn = n_new - n
        e_new = []
        for i in range(len(TROPHIES)):
            acc = kappa[i] * max(0.0, cap[i] * n - earn[i]) * dt
            from_new = dn * newbie[i] * (1.0 - math.exp(-dt / 30.0))
            e_new.append(min(n_new * 0.9999, earn[i] + acc + from_new))
        run = None
        for i in CHAIN:
            if run is not None and e_new[i] > run:
                e_new[i] = run
            run = e_new[i] if run is None else min(run, e_new[i])
        n, earn = n_new, e_new
        states.append((rt, n, list(earn)))
        prev_t = rt
    return states


def page_rates(state):
    _, n, earn = state
    return [round(min(99.99, max(0.0, 100.0 * e / n)), 2) for e in earn]


def build_case(states, cut_h=59.5, target_h=168.0):
    hist = []
    t = -0.16
    si = 0
    while t <= cut_h:
        while si + 1 < len(states) and states[si + 1][0] <= t:
            si += 1
        st = states[si]
        hist.append({'at': iso(REL + timedelta(hours=t)),
                     'players_tracked': int(round(st[1])),
                     'trophies_earned': int(round(sum(st[2]))),
                     'rates': page_rates(st)})
        t += 0.5
    case = {'trophies': TROPHIES, 'history': hist,
            'at': iso(REL + timedelta(hours=cut_h)),
            'target': iso(REL + timedelta(hours=target_h)),
            'horizon_minutes': int((target_h - cut_h) * 60),
            'levels': [0.05, 0.25, 0.5, 0.75, 0.95]}
    si = 0
    for k, st in enumerate(states):
        if st[0] <= target_h:
            si = k
    return case, page_rates(states[si])


def rows_of(mod, case):
    return [r['q'] for r in mod.forecast_case(case)['trophies']]


def run_suite(regime, seeds):
    agg = {'v2': [], 'v1': [], 'persistence': [], 'trend': []}
    cov = {'v2': [], 'v1': []}
    for s in seeds:
        states = simulate(s, regime)
        case, answer = build_case(states)
        for name, fn in (('v2', lambda c: rows_of(v2, c)),
                         ('v1', lambda c: rows_of(v1, c)),
                         ('persistence', persistence_rows),
                         ('trend', trend_rows)):
            loss, c90, miss = pinball_loss(fn(case), answer)
            agg[name].append(loss)
            if name in cov:
                cov[name].append(c90)
    print(f"[{regime}] mean loss over {len(agg['v2'])} seeds: " +
          '  '.join(f"{k} {sum(v) / len(v):.4f}" for k, v in agg.items()) +
          f"  | coverage90 v2 {sum(cov['v2']) / len(cov['v2']):.3f} v1 {sum(cov['v1']) / len(cov['v1']):.3f}")
    return agg


if __name__ == '__main__':
    run_suite('realistic', range(1, 13))
    run_suite('boom', range(1, 7))

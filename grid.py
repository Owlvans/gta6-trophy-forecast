#!/usr/bin/env python3
"""Grid-search the v2 dynamic-ensemble weights and spread calibration
on the synthetic finals (both regimes). Objective: mean of the two
suite means plus half the worse suite mean (robustness-weighted)."""
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import synth_bt as S  # noqa: E402
from score import pinball_loss  # noqa: E402

v2 = S.v2

CASES = []
for regime, seeds in (('realistic', range(1, 13)), ('boom', range(1, 7))):
    for s in seeds:
        states = S.simulate(s, regime)
        case, answer = S.build_case(states)
        CASES.append((regime, case, answer))
print('cases:', len(CASES))


def evaluate(ws, wt, wv, cal):
    v2.W_STEP, v2.W_TREND, v2.W_V1, v2.CAL = ws, wt, wv, cal
    per = {'realistic': [], 'boom': []}
    for regime, case, answer in CASES:
        rows = [r['q'] for r in v2.forecast_case(case)['trophies']]
        per[regime].append(pinball_loss(rows, answer)[0])
    mr = sum(per['realistic']) / len(per['realistic'])
    mb = sum(per['boom']) / len(per['boom'])
    return mr, mb, 0.5 * (mr + mb) + 0.5 * max(mr, mb)


results = []
for cal in (0.75, 0.9, 1.0):
    for i in range(11):
        for j in range(11 - i):
            ws, wt = i / 10.0, j / 10.0
            wv = 1.0 - ws - wt
            mr, mb, obj = evaluate(ws, wt, wv, cal)
            results.append((obj, mr, mb, ws, wt, wv, cal))
results.sort()
print('\ntop 8 (obj, realistic, boom, W_STEP, W_TREND, W_V1, CAL):')
for r in results[:8]:
    print('  obj %.4f  real %.4f  boom %.4f  w=(%.1f, %.1f, %.1f)  cal=%.2f' % r)
print('\nreference configs:')
for ws, wt, wv, cal in ((0.35, 0.25, 0.40, 1.0), (0.0, 0.0, 1.0, 1.0),
                        (1.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0, 1.0)):
    mr, mb, obj = evaluate(ws, wt, wv, cal)
    print('  w=(%.2f, %.2f, %.2f) cal=%.2f -> real %.4f boom %.4f obj %.4f'
          % (ws, wt, wv, cal, mr, mb, obj))

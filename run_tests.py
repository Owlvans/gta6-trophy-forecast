#!/usr/bin/env python3
"""Local tests for main.py: practice-case runs, determinism, H1 holdout
(frozen launch-night history -> live Exophase rates at 2026-11-19T15:56Z),
and edge cases. Research tool only; not part of the entry."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score import BASELINES, pinball_loss, score_forecast_fn, parse_ts  # noqa: E402

KIT_IN = HERE / "kit" / "gta6-trophy-rarity-forecast-01-kit" / "input"


def run_main(cases, tag):
    """cases: {stem: case-dict}. Returns {stem: output-dict} and wall seconds."""
    with tempfile.TemporaryDirectory() as td:
        ind, outd = Path(td) / "in", Path(td) / "out"
        ind.mkdir()
        outd.mkdir()
        for stem, case in cases.items():
            (ind / f"{stem}.in").write_text(json.dumps(case))
        env = dict(os.environ, MAKEFUN_INPUT_DIR=str(ind), MAKEFUN_OUTPUT_DIR=str(outd))
        t0 = time.time()
        subprocess.run([sys.executable, str(HERE / "main.py")], env=env, check=True)
        dt = time.time() - t0
        return {stem: json.loads((outd / f"{stem}.out").read_text()) for stem in cases}, dt


def rows_of(out):
    return [r["q"] for r in out["trophies"]]


def validate(case, out):
    errs = []
    trophies = case["trophies"]
    rows = out.get("trophies")
    if not isinstance(rows, list) or len(rows) != len(trophies):
        return ["row count mismatch"]
    ids = [r["id"] for r in rows]
    if sorted(ids) != sorted(t["id"] for t in trophies) or len(set(ids)) != len(ids):
        errs.append("ids not a permutation of case trophies")
    for r in rows:
        q = r["q"]
        if len(q) != 5 or any(not isinstance(x, (int, float)) or x < 0 or x > 100 for x in q):
            errs.append(f"bad q for id {r['id']}: {q}")
        if any(q[j] < q[j - 1] for j in range(1, 5)):
            errs.append(f"non-monotone q for id {r['id']}: {q}")
    blob = json.dumps(out, separators=(",", ":")).encode()
    if len(blob) > 65536:
        errs.append(f"output too large: {len(blob)}")
    return errs


def main():
    p00 = json.loads((KIT_IN / "p00.in").read_text())
    p01 = json.loads((KIT_IN / "p01.in").read_text())
    live = json.loads((HERE / "live_20261119T1556Z.json").read_text())

    # 1) practice cases run + validate + determinism
    outs1, dt1 = run_main({"p00": p00, "p01": p01}, "a")
    outs2, _ = run_main({"p00": p00, "p01": p01}, "b")
    for stem, case in (("p00", p00), ("p01", p01)):
        errs = validate(case, outs1[stem])
        print(f"{stem}: valid={'YES' if not errs else errs}")
    b1 = json.dumps(outs1, sort_keys=True).encode()
    b2 = json.dumps(outs2, sort_keys=True).encode()
    print(f"determinism on practice cases: {'IDENTICAL' if b1 == b2 else 'DIFFERS'}")
    print(f"runtime p00+p01 together: {dt1:.2f}s")

    # 2) H1 holdout: p01 history, target = live snapshot time
    h1 = copy.deepcopy(p01)
    h1["id"] = "h1"
    h1["target"] = "2026-11-19T15:56:00Z"
    h1["horizon_minutes"] = int((parse_ts(h1["target"]) - parse_ts(h1["at"])).total_seconds() // 60)
    outs, _ = run_main({"h1": h1}, "h1")
    loss, cov, miss = pinball_loss(rows_of(outs["h1"]), live["rates"])
    print(f"\nH1 holdout (frozen -> live 15:56Z, {h1['horizon_minutes']} min horizon):")
    print(f"{'MODEL':>12}: loss={loss} coverage90={cov:.3f} typical_miss={miss}pp")
    for name, fn in BASELINES.items():
        l2, c2, m2 = score_forecast_fn(fn, h1, live["rates"])
        print(f"{name:>12}: loss={l2} coverage90={c2:.3f} typical_miss={m2}pp")

    # 3) edge cases
    base = copy.deepcopy(p01)
    edge = {}
    c = copy.deepcopy(base); c["history"] = base["history"][:1]; edge["single_read"] = c
    c = copy.deepcopy(base)
    for r in c["history"]:
        r["rates"] = [None] * 55
    edge["all_null"] = c
    c = copy.deepcopy(base)
    for r in c["history"]:
        r["rates"][7] = None
    edge["one_trophy_null"] = c
    c = copy.deepcopy(base); c["history"] = []; edge["no_history"] = c
    c = copy.deepcopy(base)
    for r in c["history"]:
        r["rates"] = [0.0] * 55
        r["players_tracked"] = 0
    edge["zero_rates_zero_players"] = c
    c = copy.deepcopy(base)
    c["history"] = base["history"][:3]
    c["horizon_minutes"] = 6510  # final-like horizon from a short history
    c["target"] = "2026-11-26T00:00:00Z"
    edge["final_like_horizon"] = c
    outs, dt = run_main(edge, "edge")
    for stem, case in edge.items():
        errs = validate(case, outs[stem])
        print(f"edge {stem}: valid={'YES' if not errs else errs}")
    print(f"edge runtime (6 cases): {dt:.2f}s")


if __name__ == "__main__":
    main()

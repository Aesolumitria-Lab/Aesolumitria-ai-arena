#!/usr/bin/env python3
"""Test the one knob that matches the data: give the inner seats a protected flank.

WHY: docs/fairness-audit.md 3.6 shows (a) HP +40 moves the inner death rate 0.8pp, (b) raising the
ground N=1..6 does nothing, (c) the bias follows the PLATFORM, not the seat label
(relabel test), and (d) wind direction does not explain it. What is left is topology:
in a side-view 2D line, seats 1/2 have TWO neighbours 21 tiles away, seats 0/3 have
ONE. This tool tests whether shielding one flank of seats 1/2 moves the numbers.

VARIANTS (all pure map edits, engine untouched, committed maps untouched):
  w0  baseline
  w1  a bedrock wall on seat1's left flank (x=31) and seat2's right flank (x=64)
  w2  same wall, extended toward the border (x=31..24 and x=64..71)
  w3  w1 wall plus a 3-row roof over the inner platforms (hard cover from above)

MEASUREMENT: 4 identical TIDE-NORMAL, seed sweep, deaths per seat + drownings.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
BUILD = GAME / "build"

H, W = 54, 96


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def edit(rows, variant: str):
    out = [list(r) for r in rows]
    if variant == "w0":
        return rows
    # 墙从地表往上长，高 12 行，不出边界
    sy = 25            # 本章图集的 spawn 行（各席同高）
    def wall(x0, x1):
        for y in range(sy - 11, sy + 1):
            for x in range(x0, x1 + 1):
                if 2 <= x < W - 2 and out[y][x] == ".":
                    out[y][x] = "X"
    def roof(x0, x1):
        for y in range(sy - 4, sy - 2):
            for x in range(x0, x1 + 1):
                if out[y][x] == ".":
                    out[y][x] = "X"
    if variant in ("w1", "w3"):
        wall(31, 31)      # seat1 左翼（朝 seat0 那侧）
        wall(64, 64)      # seat2 右翼（朝 seat3 那侧）
    if variant == "w2":
        wall(24, 31)
        wall(64, 71)
    if variant == "w3":
        roof(32, 42)
        roof(53, 63)
    return ["".join(r) for r in out]


def pick_maps(n: int):
    man = json.loads((PUB / "maps4" / "manifest.json").read_text(encoding="utf-8"))
    by_el = collections.OrderedDict()
    for rec in man["maps"]:
        tc = rec.get("type_code") or ""
        el = tc.split("|")[1] if "|" in tc else "?"
        p = PUB / "maps4" / rec["file"]
        if p.is_file():
            by_el.setdefault(el, []).append(p)
    out, i = [], 0
    while len(out) < n and any(len(v) > i for v in by_el.values()):
        for v in by_el.values():
            if len(out) >= n:
                break
            if len(v) > i:
                out.append(v[i])
        i += 1
    return out


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="w0,w1,w2,w3")
    ap.add_argument("--maps", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=24)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "fj")
    j4 = load(PUB / "judge" / "judge4.py", "fj4")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    maps = pick_maps(args.maps)
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    print("maps=%d seeds=%d jobs=%d  4 identical tide_normal" % (len(maps), args.seeds, jobs))
    print("")
    print("%-4s %6s %10s %10s %7s %9s %9s %8s %6s"
          % ("var", "games", "inner死", "outer死", "ratio", "inner胜%", "判血%", "淹死", "可用图"))

    rows = []
    for v in [x for x in args.variants.split(",") if x.strip()]:
        vdir = BUILD / ("span_" + v)
        if vdir.is_dir():
            shutil.rmtree(str(vdir))
        vdir.mkdir(parents=True)
        usable = []
        for mp in maps:
            out = vdir / mp.name
            out.write_text("\n".join(edit(
                mp.read_text(encoding="utf-8").splitlines(), v)) + "\n",
                encoding="utf-8", newline="\n")
            # strict LOAD 校验（含连通区/侧出口/可存活移动）
            import subprocess
            p = subprocess.run([str(eng)], input="LOAD %s 1 15\nSTATE\n" % out,
                               capture_output=True, text=True)
            err = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("ERR")]
            if err:
                print("   reject %s %s: %s" % (v, out.name, err[0]))
                continue
            usable.append(out)
        if not usable:
            print("%-4s  全部拒收，跳过" % v)
            continue

        rd = REPO / "build" / ("spanrun_" + v)
        if rd.is_dir():
            shutil.rmtree(str(rd))
        rd.mkdir(parents=True)
        tasks = [(mi, mp, sd) for mi, mp in enumerate(usable) for sd in range(args.seeds)]

        def run(t):
            mi, mp, sd = t
            rp = rd / ("%d-%d.jsonl" % (mi, sd))
            j4.play_one(j, eng, mp, 1000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, tasks))

        dead = collections.Counter(); drown = collections.Counter()
        wins = collections.Counter(); reasons = collections.Counter(); games = 0
        for rp in rps:
            if not rp.is_file():
                continue
            turns = []
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") == "turn":
                        turns.append(rec)
                    elif rec.get("type") == "result":
                        wins[rec.get("winner", -1)] += 1
                        reasons[rec.get("reason", "?")] += 1
                        games += 1
            prev = None
            for rec in turns:
                worms = rec.get("worms") or []
                t = rec.get("t", 0)
                if prev is not None:
                    for wid in range(min(len(worms), len(prev))):
                        if prev[wid][5] and not worms[wid][5]:
                            seat = wid // 3
                            dead[seat] += 1
                            if prev[wid][1] >= water_at(t):
                                drown[seat] += 1
                prev = worms

        di = 100.0 * (dead[1] + dead[2]) / max(1, 6 * games)
        do_ = 100.0 * (dead[0] + dead[3]) / max(1, 6 * games)
        ratio = di / do_ if do_ else float("nan")
        iw = 100.0 * (wins[1] + wins[2]) / max(1, games)
        tb = 100.0 * reasons.get("HP", 0) / max(1, games)
        print("%-4s %6d %9.1f%% %9.1f%% %7.2f %8.1f%% %8.1f%% %7d %6d"
              % (v, games, di, do_, ratio, iw, tb, sum(drown.values()), len(usable)), flush=True)
        rows.append({"variant": v, "games": games, "maps": len(usable),
                     "inner_death_pct": round(di, 2), "outer_death_pct": round(do_, 2),
                     "ratio": None if ratio != ratio else round(ratio, 3),
                     "inner_win_pct": round(iw, 2), "tiebreak_pct": round(tb, 2),
                     "drown_total": sum(drown.values()),
                     "dead_by_seat": {str(k): dead[k] for k in range(4)},
                     "wins_by_seat": {str(k): wins[k] for k in range(4)}})
        shutil.rmtree(str(rd), ignore_errors=True)

    p = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "spanscan.json"
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("")
    print("wrote " + str(p.relative_to(REPO)))
    print("w0 必须与 docs/data/raise-scan.json raise=0 一致（no-op 基线）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

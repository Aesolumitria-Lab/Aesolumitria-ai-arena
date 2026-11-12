#!/usr/bin/env python3
"""What actually kills the inner seats? Classify every death by its last-on-record state.

WHY (2026-11-10): the HP sweep went to +200 per worm (300 HP, 900 per seat) and the
inner death rate did not move (95.4% -> 95.7%) while outer death dropped (71.1% ->
57.5%). That means HP is not the limiting factor. This tool finds out what is.

Each death is classified by the state of the worm on the TURN BEFORE it died:
   water     -- it was standing at/under the water line  (instant kill, HP irrelevant)
   full_hp   -- hp was still WORM_HP or above  (instant kill, HP irrelevant)
   chipped   -- hp was below full but above 0  (accumulated damage)
   also records hp_before and round, so the distribution is visible.
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
CONST = "static const int FALL_DAMAGE = 10;"
ADD = """

#ifndef P01_INNER_HP
#define P01_INNER_HP 0
#endif
static const int INNER_HP_BONUS = P01_INNER_HP;"""
WANCH = "                G.worm[wid].x = x;\n                G.worm[wid].y = y;"
WADD = WANCH + """

                if (seat == 1 || seat == 2) G.worm[wid].hp += INNER_HP_BONUS;"""


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=int, default=0)
    ap.add_argument("--maps", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=24)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "kj")
    j4 = load(PUB / "judge" / "judge4.py", "kj4")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"

    src = (PUB / "engine" / "engine.cpp").read_text(encoding="utf-8")
    src = src.replace(CONST, CONST + ADD, 1).replace(WANCH, WADD, 1)
    BUILD.mkdir(parents=True, exist_ok=True)
    exp = BUILD / "engine_diag.cpp"
    exp.write_text(src, encoding="utf-8", newline="\n")
    import subprocess
    exe = BUILD / ("engine_diag_%d.exe" % args.delta)
    if not exe.is_file():
        r = subprocess.run(["g++", "-O2", "-std=c++17", "-static", "-I", str(PUB / "engine"),
                            "-DP01_INNER_HP=%d" % args.delta, "-o", str(exe), str(exp)],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr[-1000:])

    man = json.loads((PUB / "maps4" / "manifest.json").read_text(encoding="utf-8"))
    maps = [PUB / "maps4" / rec["file"] for rec in man["maps"]][:args.maps]
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    rd = REPO / "build" / ("diag_%d" % args.delta)
    if rd.is_dir():
        shutil.rmtree(str(rd))
    rd.mkdir(parents=True)
    tasks = [(mi, mp, sd) for mi, mp in enumerate(maps) for sd in range(args.seeds)]

    def run(t):
        mi, mp, sd = t
        rp = rd / ("%d-%d.jsonl" % (mi, sd))
        j4.play_one(j, exe, mp, 1000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
                    (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                    wall_allow_s=wa, jobs=jobs)
        return rp

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rps = list(ex.map(run, tasks))

    kind = collections.defaultdict(collections.Counter)
    hpdist = collections.defaultdict(list)
    rounds = collections.defaultdict(list)
    games = 0
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
                    games += 1
        prev = None
        for rec in turns:
            worms = rec.get("worms") or []
            t = rec.get("t", 0)
            if prev is not None:
                for wid in range(min(len(worms), len(prev))):
                    if prev[wid][5] and not worms[wid][5]:
                        seat = wid // 3
                        grp = "inner" if seat in (1, 2) else "outer"
                        y, hp = prev[wid][1], prev[wid][2]
                        if y >= water_at(t - 1):
                            c = "water"
                        elif hp >= 100 + (args.delta if seat in (1, 2) else 0):
                            c = "full_hp"
                        elif hp > 0:
                            c = "chipped"
                        else:
                            c = "zero"
                        kind[grp][c] += 1
                        hpdist[grp].append(hp)
                        rounds[grp].append(t)
            prev = worms

    print("delta=+%d  games=%d" % (args.delta, games))
    print("")
    print("%-8s %8s %8s %8s %8s %8s %8s" %
          ("group", "deaths", "water", "full_hp", "chipped", "zero", "mean_round"))
    for grp in ("inner", "outer"):
        d = kind[grp]
        tot = sum(d.values())
        mround = sum(rounds[grp]) / max(1, len(rounds[grp]))
        print("%-8s %8d %8d %8d %8d %8d %8.1f"
              % (grp, tot, d["water"], d["full_hp"], d["chipped"], d["zero"], mround))
    print("")
    print("hp distribution at the turn before death:")
    for grp in ("inner", "outer"):
        v = sorted(hpdist[grp])
        if not v:
            continue
        n = len(v)
        print("  %-6s  min=%3d p10=%3d median=%3d p90=%3d max=%3d"
              % (grp, v[0], v[n // 10], v[n // 2], v[9 * n // 10], v[-1]))
    print("")
    print("读法：water/full_hp = 血量管不了的死法；chipped = 掉血死")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

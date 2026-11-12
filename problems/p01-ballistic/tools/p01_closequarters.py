#!/usr/bin/env python3
"""Is the square layout ACTUALLY stacked? Correct the threshold bug first.

The previous run (problems/p01-ballistic/tools/p01_collapse.py) reported "raised seat first reaches low
level: median round 1" for BOTH shapes. That is wrong: LOW_Y=30 was taken from
the LOW platform row, but that row is at DF-1=30 and the closest gas-filled row
is 29, and worms on the raised perch start at y=13. The test fired on round 1
for the line too, where there is no raised seat at all — so it was measuring
nothing but the fact that 30 is a big number.

Correct version: measure the actual WORST-CASE vertical gap between any two
worms of DIFFERENT seats that share a column, per turn, and report the share of
turns where that gap is small enough to matter (<=2 tiles = one blast radius).
Blast radius: rocket 2, grenade 3, drill 1 (engine.cpp boom_of). So <=2 means
one rocket hits both.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
BUILD = GAME / "build"


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def close_pairs(worms, limit=2):
    """Cross-seat worm pairs within `limit` Chebyshev distance, and sharing a blast."""
    same_col = 0
    same_blast = 0
    for i in range(12):
        if not worms[i][5]:
            continue
        for k in range(i + 1, 12):
            if not worms[k][5] or i // 3 == k // 3:
                continue
            dx = abs(worms[i][0] - worms[k][0])
            dy = abs(worms[i][1] - worms[k][1])
            if dx <= 1:
                same_col += 1
            if max(dx, dy) <= limit:
                same_blast += 1
                break
    return same_col, same_blast


def main() -> int:
    sq = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_layoutsq.py", "sqz")
    j = loadmod(PUB / "judge" / "judge.py", "qjc")
    j4 = loadmod(PUB / "judge" / "judge4.py", "qjd")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    seeds = 96

    print("%-6s %6s %12s %12s %12s" % ("shape", "games", "近列占比", "同爆占比", "平均近列对/轮"))
    for name, rows_txt in (("line", sq.make_line()), ("sq17", sq.make_square(17))):
        d = BUILD / "closeq" / name
        if d.is_dir():
            shutil.rmtree(str(d))
        d.mkdir(parents=True)
        mp = d / (name + ".txt")
        mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
        p = subprocess.run([str(eng)], input="LOAD %s 1 15\nSTATE\n" % mp,
                           capture_output=True, text=True)
        if any(ln.startswith("ERR") for ln in p.stdout.splitlines()):
            print(name, "REJECT", [l for l in p.stdout.splitlines() if l.startswith("ERR")][0])
            continue

        def run(sd, d=d, mp=mp):
            rp = d / ("r%d.jsonl" % sd)
            j4.play_one(j, eng, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, list(range(seeds))))

        turns = 0
        col_turns = 0
        blast_turns = 0
        col_pairs = 0
        games = 0
        for rp in rps:
            if not rp.is_file():
                continue
            games += 1
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") != "turn":
                        continue
                    turns += 1
                    sc, sb = close_pairs(rec.get("worms") or [])
                    col_pairs += sc
                    if sc:
                        col_turns += 1
                    if sb:
                        blast_turns += 1
        if turns:
            print("%-6s %6d %11.1f%% %11.1f%% %12.2f"
                  % (name, games, 100.0 * col_turns / turns,
                     100.0 * blast_turns / turns, col_pairs / turns))
        shutil.rmtree(str(d), ignore_errors=True)
    print("")
    print("近列 = 不同席的两条虫 |dx|<=1；同爆 = 两者最大轴距<=2（一发火箭都够到）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

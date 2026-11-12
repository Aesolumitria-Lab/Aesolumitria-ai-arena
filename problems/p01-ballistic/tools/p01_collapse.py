#!/usr/bin/env python3
"""When does the square collapse into one level? And how often are different
seats in point-blank contact, line vs square?

WHY (2026-11-10, owner): "the square fall -- they overlap, right?" The first
measurement (problems/p01-ballistic/tools/p01_stacking.py) answered partly: in sq17, 35% of turns have
a raised-seat worm and a low-seat worm within 1 tile, vs 0.0% on the line.
This tool pins down the mechanism and the timeline:
  1. first turn at which any raised worm reaches the low platform level
  2. how many turns of the game remain after that
  3. how many DIFFERENT-seat worm pairs are 4-adjacent, per turn, both shapes
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
RAISED = (0, 1)
LOW_Y = 30


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def adjacent_pairs(worms):
    n = 0
    for i in range(12):
        if not worms[i][5]:
            continue
        for k in range(i + 1, 12):
            if not worms[k][5]:
                continue
            if i // 3 == k // 3:
                continue
            dx = abs(worms[i][0] - worms[k][0])
            dy = abs(worms[i][1] - worms[k][1])
            if dx + dy <= 1:
                n += 1
    return n


def main() -> int:
    sq = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_layoutsq.py", "sqm")
    j = loadmod(PUB / "judge" / "judge.py", "qja")
    j4 = loadmod(PUB / "judge" / "judge4.py", "qjb")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    seeds = 96

    for name, rows_txt in (("line", sq.make_line()), ("sq17", sq.make_square(17))):
        d = BUILD / "collapse" / name
        if d.is_dir():
            shutil.rmtree(str(d))
        d.mkdir(parents=True)
        mp = d / (name + ".txt")
        mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
        p = subprocess.run([str(eng)], input="LOAD %s 1 15\nSTATE\n" % mp,
                           capture_output=True, text=True)
        if any(ln.startswith("ERR") for ln in p.stdout.splitlines()):
            print(name, "REJECT")
            continue

        def run(sd, d=d, mp=mp):
            rp = d / ("r%d.jsonl" % sd)
            j4.play_one(j, eng, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, list(range(seeds))))

        first = []
        adj_by_t = collections.Counter()
        turns_by_t = collections.Counter()
        games = 0
        for rp in rps:
            if not rp.is_file():
                continue
            games += 1
            hit = None
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") != "turn":
                        continue
                    t = rec.get("t", 0)
                    worms = rec.get("worms") or []
                    turns_by_t[t] += 1
                    adj_by_t[t] += adjacent_pairs(worms)
                    if hit is None:
                        for s in RAISED:
                            for i in range(3):
                                w = worms[s * 3 + i]
                                if w[5] and w[1] >= LOW_Y - 1:
                                    hit = t
                                    break
                            if hit is not None:
                                break
            if hit is not None:
                first.append(hit)

        print("")
        print("shape=%s games=%d" % (name, games))
        if first:
            f = sorted(first)
            print("  raised seat first reaches low level: median round %d, n=%d/%d"
                  % (f[len(f) // 2], len(f), games))
            print("  (game is 40 rounds; remaining after collapse: %d)"
                  % (40 - f[len(f) // 2]))
        else:
            print("  raised seat never reaches low level")
        tot_adj = sum(adj_by_t.values())
        tot_t = sum(turns_by_t.values())
        print("  avg 4-adjacent cross-seat pairs per turn = %.2f"
              % (tot_adj / max(1, tot_t)))
        shutil.rmtree(str(d), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

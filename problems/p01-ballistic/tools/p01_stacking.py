#!/usr/bin/env python3
"""Does the square make the raised seats fall ON TOP of the low seats?

WHY (2026-11-10, owner): "the square fall -- they overlap, right?"

Confirmed geometrically: the square puts seats 0/2 at the SAME x range
([36,44]) and seats 1/3 at the SAME x range ([52,60]). Seat0 sits 17 rows
DIRECTLY ABOVE seat2, seat1 above seat3. When the upper perch breaks the worm
falls straight down the same columns onto the low seat platform.
engine.cpp settle_all() (L246-260) stops the fall when worm_at() finds a worm
in the tile below, so they never share a tile -- but they do end up adjacent.

This measures the vertical gap between a raised worm and a low worm in the
same column, per turn.
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
PAIRS = ((0, 2), (1, 3))


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main() -> int:
    sq = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_layoutsq.py", "sqmod")
    j = loadmod(PUB / "judge" / "judge.py", "qj1")
    j4 = loadmod(PUB / "judge" / "judge4.py", "qj2")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    seeds = 96

    shapes = (("line", sq.make_line()), ("sq17", sq.make_square(17)))
    for name, rows_txt in shapes:
        d = BUILD / "stack" / name
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

        gap = collections.Counter()
        stacked = 0
        tot = 0
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
                    tot += 1
                    worms = rec.get("worms") or []
                    best = -1
                    for hi, lo in PAIRS:
                        hs = [worms[hi * 3 + i] for i in range(3) if worms[hi * 3 + i][5]]
                        ls = [worms[lo * 3 + i] for i in range(3) if worms[lo * 3 + i][5]]
                        for a in hs:
                            for b in ls:
                                if abs(b[0] - a[0]) > 1:
                                    continue
                                dy = b[1] - a[1]
                                if dy < 0:
                                    continue
                                if best < 0 or dy < best:
                                    best = dy
                    if best >= 0:
                        gap[best] += 1
                        if best <= 1:
                            stacked += 1

        print("")
        print("shape=%s games=%d" % (name, games))
        n = max(1, sum(gap.values()))
        print("  same-column (|dx|<=1) nearest vertical gap:")
        for k in sorted(gap)[:6]:
            print("    dy=%2d  %6d worm-turns (%.1f%%)" % (k, gap[k], 100.0 * gap[k] / n))
        print("  dy<=1 (stacked/adjacent) share of turns = %.1f%%"
              % (100.0 * stacked / max(1, tot)))
        shutil.rmtree(str(d), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

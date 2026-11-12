#!/usr/bin/env python3
"""Ring vs line with a bot that actually roams: TYPHON.

WHY (2026-11-10): TIDE-NORMAL only moves 3-4 columns from spawn over a whole
game (problems/p01-ballistic/tools/p01_travel.py), so the ring seam was never reached (0 crossings in
4864 steps). TYPHON is the strongest challenge bot and is being tested to see
whether a roaming bot shows any ring benefit at all.

The torus engine wrap has been locally refactored (problems/p01-ballistic/tools/p01_torusarena.py):
border columns x<2 and x>=94 are now treated as bedrock and never wrapped, so
wrap_x(0)=92 aliasing is gone. Verified: seating a worm at x=0/1/94/95 is
rejected, x=2/93 accepted, x=93 step R -> x=2, x=2 step L -> x=93.

Compares, same seeds, same bots: line arena vs ring arena, plain vs torus engine.
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
H, W = 54, 96
PX0, PX1 = 2, 93
DF = 31


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def blank(open_seam):
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if y < 2 or y >= H - 2 or x < 2:
                g[y][x] = "X"
            elif x >= W - 2 and not open_seam:
                g[y][x] = "X"
    for y in range(DF, H - 2):
        for x in range(2, W - 2):
            g[y][x] = "#"
    return g


def perch(g, seat, cx, cy, half=5):
    lo, hi = cx - half, cx + half
    if lo < PX0 or hi > PX1:
        raise SystemExit("perch %d outside ring [%d,%d]" % (cx, PX0, PX1))
    for y in range(cy + 1, min(H - 2, cy + 4)):
        for x in range(lo, hi + 1):
            if g[y][x] != "X":
                g[y][x] = "#"
    for x in range(lo, hi + 1):
        g[cy][x] = "."
    for dx in (-4, 0, 4):
        g[cy][cx + dx] = str(seat)


def make_line(open_seam=False):
    g = blank(open_seam)
    for seat, cx in enumerate((16, 37, 58, 79)):
        perch(g, seat, cx, DF - 1)
    return ["".join(r) for r in g]


def make_ring():
    g = blank(True)
    step = (PX1 - PX0 + 1) // 4
    for seat in range(4):
        perch(g, seat, PX0 + 5 + seat * step, DF - 1)
    return ["".join(r) for r in g]


def main() -> int:
    ap_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 48
    ta = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py", "ty_ta")
    j = ta.load(PUB / "judge" / "judge.py", "ty_j")
    j4 = ta.load(PUB / "judge" / "judge4.py", "ty_j4")
    plain = ta.build_scratch(False)
    torus = ta.build_scratch(True)
    bot = PUB / "challenges" / "bin" / "typhon.exe"
    if not bot.is_file():
        print("typhon not found at", bot)
        return 1
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    cases = (("line/plain", make_line(), plain),
             ("ring/torus", make_ring(), torus))

    print("%-12s %6s %8s %8s %8s %8s %9s %8s"
          % ("case", "games", "seat0", "seat1", "seat2", "seat3", "spread", "跨缝"))
    for name, rows_txt, exe in cases:
        d = BUILD / "typhon" / name.replace("/", "_")
        if d.is_dir():
            shutil.rmtree(str(d))
        d.mkdir(parents=True)
        mp = d / "arena.txt"
        mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
        p = subprocess.run([str(exe)], input="LOAD %s 1 15\nSTATE\n" % mp,
                           capture_output=True, text=True)
        err = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("ERR")]
        if err:
            print("%-12s REJECT %s" % (name, err[0]))
            continue

        def run(sd, d=d, mp=mp, exe=exe):
            rp = d / ("r%d.jsonl" % sd)
            j4.play_one(j, exe, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 1.0, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, list(range(ap_seeds))))

        wins = collections.Counter()
        games = 0
        cross = 0
        steps = 0
        for rp in rps:
            if not rp.is_file():
                continue
            prev = None
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") == "result":
                        wins[rec.get("winner", -1)] += 1
                        games += 1
                        continue
                    if rec.get("type") != "turn":
                        continue
                    worms = rec.get("worms") or []
                    if prev is not None:
                        for i in range(min(len(worms), len(prev))):
                            if prev[i][5] and worms[i][5] and worms[i][0] != prev[i][0]:
                                steps += 1
                                if abs(worms[i][0] - prev[i][0]) > 40:
                                    cross += 1
                    prev = worms

        if not games:
            print("%-12s 0 games" % name)
            continue
        pct = [100.0 * wins[i] / games for i in range(4)]
        print("%-12s %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %8.1f%% %8d"
              % (name, games, pct[0], pct[1], pct[2], pct[3],
                 max(pct) - min(pct), cross))
        shutil.rmtree(str(d), ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

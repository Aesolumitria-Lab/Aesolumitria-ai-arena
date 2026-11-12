#!/usr/bin/env python3
"""Ring arena with an OPEN seam: x=94/95 are air, so x=93 and x=2 really touch.

WHY the previous ring test failed: the arena kept the bedrock columns at x=94,95
(strict LOAD demands it), so from a worm's point of view the map was still a
rectangle with walls at both ends. problems/p01-ballistic/tools/p01_seamcount.py measured 0 seam
crossings in 2405 worm steps -- the bots never noticed they were on a ring.

This variant punches the right border open (with the torus engine's relaxed
strict-LOAD check), so the two end columns are genuinely adjacent.
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
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def open_ring():
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if y < 2 or y >= H - 2 or x < 2:
                g[y][x] = "X"
    # ★ 右侧两列(x=94,95)留空 —— 环缝
    #   seam 列只允许空气或基岩（BORDER_NEW 的检查），铺泥土会被判
    #   "seam column must be air or bedrock"
    for y in range(DF, H - 2):
        for x in range(2, W - 2):
            g[y][x] = "#"
    pw = PX1 - PX0 + 1
    step = pw // 4
    for seat in range(4):
        cx = PX0 + 5 + seat * step
        perch(g, seat, cx, DF - 1)
    return ["".join(r) for r in g]


def perch(g, seat, cx, cy, half=5):
    lo, hi = cx - half, cx + half
    for y in range(cy + 1, min(H - 2, cy + 4)):
        for x in range(lo, hi + 1):
            if g[y][x] != "X":
                g[y][x] = "#"
    for x in range(lo, hi + 1):
        g[cy][x] = "."
    for dx in (-4, 0, 4):
        g[cy][cx + dx] = str(seat)


def main() -> int:
    ta = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py", "tao")
    j = ta.load(PUB / "judge" / "judge.py", "op1")
    j4 = ta.load(PUB / "judge" / "judge4.py", "op2")
    torus = ta.build_scratch(True)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    rows_txt = open_ring()
    d = BUILD / "openring"
    if d.is_dir():
        shutil.rmtree(str(d))
    d.mkdir(parents=True)
    mp = d / "ring.txt"
    mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
    p = subprocess.run([str(torus)], input="LOAD %s 1 15\nSTATE\n" % mp,
                       capture_output=True, text=True)
    err = [ln for ln in p.stdout.splitlines() if ln.startswith("ERR")]
    print("strict LOAD:", err[0] if err else "OK")
    if err:
        return 1

    def run(sd):
        rp = d / ("r%d.jsonl" % sd)
        j4.play_one(j, torus, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                    (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                    wall_allow_s=wa, jobs=jobs)
        return rp

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rps = list(ex.map(run, list(range(96))))

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

    pct = [100.0 * wins[i] / max(1, games) for i in range(4)]
    print("games=%d  seat%%=%s  spread=%.1fpp"
          % (games, ["%.1f" % x for x in pct], max(pct) - min(pct)))
    print("worm steps=%d  seam crossings=%d  (%.2f%%)"
          % (steps, cross, 100.0 * cross / max(1, steps)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

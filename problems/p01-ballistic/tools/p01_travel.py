#!/usr/bin/env python3
"""How far does a worm actually travel in a whole game?

FINDING (2026-11-10): the torus engine patch is correct -- problems/p01-ballistic/tools/p01_seamtest.py
shows x=93 step R lands on x=2. But problems/p01-ballistic/tools/p01_seamcount.py measured 0 seam
crossings in 4864 worm steps, and max worm x was 88 over 24 games. The bots
never go near the seam.

This measures the x-span of every worm over its lifetime on the REAL map set.
"""
from __future__ import annotations

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


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ta = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py", "taz")
    j = ta.load(PUB / "judge" / "judge.py", "tr1")
    j4 = ta.load(PUB / "judge" / "judge4.py", "tr2")
    torus = ta.build_scratch(True)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    maps = sorted((PUB / "maps4").glob("*.txt"))[:4]
    d = BUILD / "travel"
    if d.is_dir():
        shutil.rmtree(str(d))
    d.mkdir(parents=True)

    print("%-14s %6s %9s %9s %9s" % ("map", "games", "maxdx", "p90dx", "median"))
    for mi, mp in enumerate(maps):
        def run(sd, mp=mp, mi=mi):
            rp = d / ("%d-%d.jsonl" % (mi, sd))
            j4.play_one(j, torus, mp, 7000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, list(range(8))))

        spans = []
        games = 0
        for rp in rps:
            if not rp.is_file():
                continue
            games += 1
            xs = collections.defaultdict(list)
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") != "turn":
                        continue
                    for i, w in enumerate(rec.get("worms") or []):
                        if w[5]:
                            xs[i].append(w[0])
            for i, v in xs.items():
                spans.append(max(v) - min(v))

        if not spans:
            continue
        srt = sorted(spans)
        n = len(srt)
        print("%-14s %6d %9d %9d %9d"
              % (mp.stem, games, srt[-1], srt[9 * n // 10], srt[n // 2]))

    shutil.rmtree(str(d), ignore_errors=True)
    print("")
    print("maxdx = x-span of one worm over its whole life, in columns.")
    print("A single-digit number means the seam is outside its range entirely.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

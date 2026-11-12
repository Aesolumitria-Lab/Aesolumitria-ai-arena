#!/usr/bin/env python3
"""Retest docs/fairness-audit.md 3.3: seed/permutation sampling designs vs a full reference grid.

WHY REDO (2026-11-10 review): the old table row 2 ("24 perms x 2 seed = 3.53pp") and
row 3 ("6 circular orders x 4 rotations x 2 seed = 0.17pp") are THE SAME DESIGN by
judge4.schedule() (6 circular x 4 rotations == all 24 permutations). One design cannot
give two numbers, and the old text never said how the reference grid was built.

Now: run 12 maps x 24 perms x 6 seeds = 1728 games ONCE as the reference; every other
design is a SUBSET of it (same map/perm/seed), so deviation is a sampling-design effect
on one batch of games, not three independent experiments.

Designs (split by schedule() seed_abs and by perm membership):
  A  24 perms x 1 seed      : seed_abs in [0,24)      (24 games/map)
  B  24 perms x 2 seed      : seed_abs in [0,48)      (48 games/map)
  C   4 cyclic x 6 seed     : only the 4 pure cyclic shifts of (0,1,2,3), all seeds
                              (24 games/map) -- neighbours fixed, seeds varied
  D   6 circular x 4 rot x 1 seed : the 24 adjacency-balanced perms, ONE seed only
                              (24 games/map) -- neighbours balanced, seed single
"""
from __future__ import annotations

import collections
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"


def load(p: Path, n: str):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def main() -> int:
    judge2 = PUB / "judge" / "judge2.py"
    j = load(judge2 if judge2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)
    B = PUB / "baselines" / "bin"
    maps = sorted((PUB / "maps4").glob("*.txt"))[:12]
    bots = [B / "tide_hard.exe", B / "tide_normal.exe", B / "tide_easy.exe", B / "random4.exe"]
    names = ["hard", "normal", "easy", "random"]

    plan = j4.schedule((0, 1, 2, 3), 24, 6)
    # 纯循环位移：4 个（不是 rotations() 那 24 个全排列）
    base = (0, 1, 2, 3)
    cyclic = set(tuple(base[(i + r) % 4] for i in range(4)) for r in range(4))
    balanced = set(p for p, _, _ in j4.schedule((0, 1, 2, 3), 24, 1))
    tasks = [(m, p, 1 + seed_abs, si) for m in maps for (p, si, seed_abs) in plan]
    jobs = 16
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    def run(t):
        m, p, seed, si = t
        r = j4.play_one(j, eng, m, seed, bots, (0, 1, 2, 3), p, 0.5,
                        replay_grid=False, wall_allow_s=wa)
        return (m.stem, p, si, r.get("winner_bot", -1), r.get("ok", False))

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run, tasks))
    print("reference grid: %d games (%d maps x 24 perms x 6 seeds)" % (len(rows), len(maps)))

    def winpct(sub):
        c = collections.Counter(r[3] for r in sub if r[4])
        n = sum(c.values()) or 1
        return dict((names[k], round(100.0 * c.get(k, 0) / n, 2)) for k in range(4)), n

    ref, ref_n = winpct(rows)
    designs = [
        ("A 24 perms x 1 seed", lambda r: r[2] == 0),
        ("B 24 perms x 2 seed", lambda r: r[2] in (0, 1)),
        ("C 4 cyclic x 6 seed", lambda r: r[1] in cyclic),
        ("D 6circ x4rot x 1 seed", lambda r: r[2] == 0 and r[1] in balanced),
    ]
    print("reference win%%: %s (n=%d)" % (ref, ref_n))
    out = {"maps": [m.stem for m in maps], "reference": ref, "n_reference": ref_n, "designs": {}}
    print("")
    print("%-26s %8s %7s %7s %7s %7s  maxdev" % ("design", "games/map", "hard", "normal", "easy", "random"))
    for label, keep in designs:
        sub = [r for r in rows if keep(r)]
        w, n = winpct(sub)
        dev = dict((k, round(w[k] - ref[k], 2)) for k in ("hard", "normal", "easy"))
        mx = max(abs(v) for v in dev.values())
        print("%-26s %8.0f %7.2f %7.2f %7.2f %7.2f  %.2fpp"
              % (label, n / float(len(maps)), w["hard"], w["normal"], w["easy"],
                 w["random"], mx))
        out["designs"][label] = {"games_per_map": n / float(len(maps)),
                                 "win_pct": w, "dev_pp": dev, "max_dev_pp": mx}
    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "seeddev.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

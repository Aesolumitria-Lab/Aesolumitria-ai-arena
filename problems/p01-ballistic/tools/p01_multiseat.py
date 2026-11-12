#!/usr/bin/env python3
"""Reproduce docs/fairness-audit.md 3.1: how many maps have MORE THAN ONE winning seat.

WHY: the original table (2026-11-07) was measured on the OLD pair2x2 map set. v2.4.2
regenerated all 216 maps, so the row "new mirror4" no longer means what it says.

CAREFUL: there are TWO different things that both look like "1 seed":
  S  shared : all 24 perms use the SAME seed (seed_base+0) -- the old "24 perms x 1 seed"
  P  spread : each perm gets its own seed (seed_abs 0..23) -- what schedule(24,1) emits
             AFTER the 2026-11-10 seed-wiring fix
Confusing them is exactly the error the old table made; both are measured here.

Same 4 identical bots (tide_normal), same 12 maps, so the rows are comparable.
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


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="tide_normal",
                    help="which binary to use 4 times; baselines/bin/<name>.exe or an absolute path")
    ap.add_argument("--maps", type=int, default=12)
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    j2 = PUB / "judge" / "judge2.py"
    j = load(j2 if j2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)
    # baseline / challenge 两个目录都要找：TYPHON/PSEUDO/CHIMERA 在 challenges/bin，
    # TIDE/random4 在 baselines/bin。找错目录会静默用错 bot。
    bp = Path(args.bot)
    cands = [bp] if bp.is_file() else []
    cands += [PUB / "baselines" / "bin" / (args.bot + ".exe"),
              PUB / "challenges" / "bin" / (args.bot + ".exe")]
    bot = next((c for c in cands if c.is_file()), None)
    if bot is None:
        ap.error("bot not found (tried %s)" % ", ".join(str(c) for c in cands))
    maps = sorted((PUB / "maps4").glob("*.txt"))[:args.maps]
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    perms24 = [p for p, _si, _sa in j4.schedule((0, 1, 2, 3), 24, 1)]

    designs = [
        ("S shared seed (old format)", [(p, 0) for p in perms24]),
        ("P 24 perms, own seed",       [(p, i) for i, p in enumerate(perms24)]),
        ("P2 24 perms x 2 seed",       [(p, i) for i, p in enumerate(perms24)] +
                                        [(p, i + 24) for i, p in enumerate(perms24)]),
    ]
    out = {"maps": [m.stem for m in maps], "bot": bot.name, "designs": {}}
    for label, pairs in designs:
        tasks = [(m, p, 1 + sa) for m in maps for (p, sa) in pairs]

        def run(t):
            m, p, seed = t
            r = j4.play_one(j, eng, m, seed, [bot] * 4, (0, 1, 2, 3), p, 0.5,
                            replay_grid=False, wall_allow_s=wa)
            return (m.stem, r.get("winner", -1), r.get("ok", False))

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rows = list(ex.map(run, tasks))
        per_map = collections.defaultdict(set)
        seat = collections.Counter()
        for stem, w, ok in rows:
            if not ok or w < 0:
                continue
            per_map[stem].add(w)
            seat[w] += 1
        multi = sum(1 for s in per_map.values() if len(s) >= 2)
        n = sum(seat.values()) or 1
        share = [round(100.0 * seat.get(i, 0) / n, 1) for i in range(4)]
        print("%-28s multi-seat %2d/%d   seat share %s   games %d"
              % (label, multi, len(per_map), share, n))
        out["designs"][label] = {"multi_seat_maps": multi, "of_maps": len(per_map),
                                  "seat_share": share, "games": n}
    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "multiseat.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

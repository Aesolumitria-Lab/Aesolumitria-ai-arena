#!/usr/bin/env python3
"""Decisive test on the ring: is the residual bias LABEL or GEOMETRY?

The ring arena has FOUR EQUIVALENT platforms (evenly spaced at 7/30/53/76, all the
same height, every neighbour 23 columns away). So on the ring there is no geometric
asymmetry left -- any remaining seat bias must come from the worm-id LABEL
(settle / blast / flood all walk worms in ascending id order).

This runs the ring with 4 relabels and reports the seat share TWICE:
  by_label    -- winners tallied by the seat NUMBER that won
  by_geometry -- winners tallied by which platform actually hosted the winner
If by_label is flat across shifts while by_geometry rotates, the bias is the label.
If by_geometry is flat while by_label rotates, the bias is the geometry.

Also verifies the torus patch really wraps: a worm at x=93 stepping R must land at
x=2 under the torus build and must not move under the plain build.
"""
from __future__ import annotations

import argparse
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


def load_torus_module():
    spec = importlib.util.spec_from_file_location("ta", REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["ta"] = m
    spec.loader.exec_module(m)
    return m


def seam_check(plain, torus, ta) -> str:
    """A worm at the right edge stepping R must wrap under the torus build."""
    rows = [list(r) for r in ta.make_ring()]
    # 清一条 y=28 的走廊横穿全图，并把右侧尽头打通
    for x in range(PX0, PX1 + 1):
        if rows[28][x] in "#.":
            rows[28][x] = "."
    mp = BUILD / "seam.txt"
    mp.write_text("\n".join("".join(r) for r in rows) + "\n",
                  encoding="utf-8", newline="\n")

    lines = []
    for exe, label in ((plain, "plain"), (torus, "torus")):
        # 移动 seat0 的 3 条虫向右；最右那条会撞边
        inp = "LOADX %s 1 15\nSETWIND 0\n" % mp
        inp += "STATE\n"
        inp += "STEP 0 M R | 1 WAIT | 2 WAIT | 3 WAIT\n"
        inp += "STEP 0 M R | 1 WAIT | 2 WAIT | 3 WAIT\n"
        out = subprocess.run([str(exe)], input=inp, capture_output=True,
                             text=True).stdout
        st = [l for l in out.splitlines() if l.startswith("STATE")]
        if len(st) < 3:
            lines.append("%s: %s" % (label, out.strip().splitlines()[-1][:90]))
            continue
        parts = st[-1].split()
        xs = [parts[20 + i * 6] for i in range(3)]
        lines.append("%s: seat0 worm x = %s" % (label, xs))
    return "  ".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=96)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    ta = load_torus_module()
    j = load_torus_module() and load_torus_module()   # keep namespace warm
    jj = ta.load(PUB / "judge" / "judge.py", "w1")
    j4 = ta.load(PUB / "judge" / "judge4.py", "w2")
    plain = ta.build_scratch(False)
    torus = ta.build_scratch(True)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    print("=== 环缝验证（worm 在 x=93 一路 M R）===")
    print(seam_check(plain, torus, ta))
    print("")

    rows_txt = ta.make_ring()
    d = BUILD / "ringrelabel"
    if d.is_dir():
        shutil.rmtree(str(d))
    d.mkdir(parents=True)
    mp = d / "ring.txt"

    print("=== 环形场地：4 次重贴号，按【标签】与按【几何】各统计一次 ===")
    print("%-6s %-28s %-28s" % ("shift", "by LABEL (seat0..3)", "by GEOMETRY (platform0..3)"))
    lab_all = []
    geo_all = []
    for shift in range(4):
        rows2 = ["".join(str((int(c) + shift) % 4) if c in "0123" else c for c in r)
                 for r in rows_txt]
        mp.write_text("\n".join(rows2) + "\n", encoding="utf-8", newline="\n")
        p = subprocess.run([str(torus)], input="LOAD %s 1 15\nSTATE\n" % mp,
                           capture_output=True, text=True)
        if any(ln.startswith("ERR") for ln in p.stdout.splitlines()):
            print("REJECT", [l for l in p.stdout.splitlines() if l.startswith("ERR")][0])
            return 1
        tasks = list(range(args.seeds))

        def run(sd):
            rp = d / ("r%d-%d.jsonl" % (shift, sd))
            j4.play_one(jj, torus, mp, 1000 + sd * 37 + shift, [bot] * 4,
                        (0, 1, 2, 3), (0, 1, 2, 3), 0.5, replay_path=rp,
                        replay_grid=False, wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, tasks))
        lab = collections.Counter()
        geo = collections.Counter()
        games = 0
        for rp in rps:
            if not rp.is_file():
                continue
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") == "result":
                        w = rec.get("winner", -1)
                        if 0 <= w < 4:
                            lab[w] += 1
                            geo[(w - shift) % 4] += 1
                        games += 1
        lp = [100.0 * lab.get(i, 0) / max(1, games) for i in range(4)]
        gp = [100.0 * geo.get(i, 0) / max(1, games) for i in range(4)]
        lab_all.append(lp)
        geo_all.append(gp)
        print("%-6d %-28s %-28s"
              % (shift, " ".join("%5.1f" % x for x in lp),
                 " ".join("%5.1f" % x for x in gp)))

    print("")
    print("=== 汇总（4 次重贴号平均）===")
    labm = [sum(r[i] for r in lab_all) / 4 for i in range(4)]
    geom = [sum(r[i] for r in geo_all) / 4 for i in range(4)]
    print("  by LABEL    : %s   spread = %.1f pp"
          % (" ".join("%5.1f" % x for x in labm), max(labm) - min(labm)))
    print("  by GEOMETRY : %s   spread = %.1f pp"
          % (" ".join("%5.1f" % x for x in geom), max(geom) - min(geom)))
    print("")
    print("判读：by LABEL 稳定而 by GEOMETRY 跟着 shift 转 => 偏袒是标签；反之是几何。")

    (REPO / "problems" / "p01-ballistic" / "docs" / "data" / "ring-label.json").write_text(
        json.dumps({"by_label": [round(x, 1) for x in labm],
                    "by_geometry": [round(x, 1) for x in geom]}, indent=2),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

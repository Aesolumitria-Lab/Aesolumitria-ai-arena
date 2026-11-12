#!/usr/bin/env python3
"""Is the torus patch actually a torus? Direct seam test on worm movement.

The decisive question: a worm standing at the right edge of the ring -- does it
step ACROSS the seam into x=PX0, or is it blocked like on a plain rectangle?

Method: build a ring arena, punch a clear corridor at y=28..29 all the way to both
edges and lay a floor at y=31, then use SETWORM (debug) to place seat0 worm0 at the
edge and step one move toward the seam.
  plain engine -> the worm must NOT move
  torus engine -> the worm must appear at the opposite end
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "problems" / "p01-ballistic" / "build"
PX0, PX1 = 2, 93


def main() -> int:
    spec = importlib.util.spec_from_file_location("ta", REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py")
    ta = importlib.util.module_from_spec(spec)
    sys.modules["ta"] = ta
    spec.loader.exec_module(ta)

    plain = ta.build_scratch(False)
    torus = ta.build_scratch(True)

    rows = [list(r) for r in ta.make_ring()]
    for y in (28, 29, 30):
        for x in range(PX0, PX1 + 1):
            if rows[y][x] in "#.":
                rows[y][x] = "."
    for x in range(PX0, PX1 + 1):
        if rows[31][x] in "#.":
            rows[31][x] = "#"
    mp = BUILD / "seam2.txt"
    mp.write_text("\n".join("".join(r) for r in rows) + "\n",
                  encoding="utf-8", newline="\n")

    def step_one(exe, x_start, dirn):
        inp = "LOADX %s 1 15\nSETWIND 0\n" % mp
        # ★ SETWORM 的签名是  id x y（engine.cpp:1198 `ss >> id >> x >> y`）。
        #   写成 "SETWORM 0 0 <x> 30" 会把 x 当 id、30 当 x —— 上一版就是这么
        #   把虫放到错误位置的。
        inp += "SETWORM 0 %d 30\n" % x_start
        inp += "STEP 0 M %s | 1 WAIT | 2 WAIT | 3 WAIT\n" % dirn
        out = subprocess.run([str(exe)], input=inp, capture_output=True,
                             text=True).stdout
        st = [ln for ln in out.splitlines() if ln.startswith("STATE")]
        if not st:
            return "ERR " + out.strip().splitlines()[-1][:70]
        parts = st[-1].split()
        return "x=%s y=%s" % (parts[20], parts[21])

    print("=== 环缝直接测试 ===")
    print("%-28s %-14s %-14s" % ("case", "plain", "torus"))
    cases = (("right edge x=92 step R", 92, "R"),
             ("right edge x=93 step R", 93, "R"),
             ("left edge  x=3  step L", 3, "L"),
             ("left edge  x=2  step L", 2, "L"))
    for label, xs, dirn in cases:
        print("%-28s %-14s %-14s"
              % (label, step_one(plain, xs, dirn), step_one(torus, xs, dirn)))
    print("")
    print("判读：torus 那列出现回绕（x=92 -> 2 / x=3 -> 93）= 环缝真的通了；")
    print("      两列一样 = 补丁没生效，这只是个换了边界的普通地图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

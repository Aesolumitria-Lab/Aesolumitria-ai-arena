#!/usr/bin/env python3
"""Torus (wrap-around x) prototype: scratch engine + torus-shaped arenas.

WHY (2026-11-10, owner): "try the ring". The square buys degree balance (2,2,2,2)
but pays a fall tax (measured 14010 fall HP vs 11170 on the line; the raised seats
fall 1.7x more tiles, docs/data/fall.json). A torus gets degree 2 for all four seats
with NO height difference at all, so no fall tax.

WHAT THE PATCH TOUCHES (scratch copy in build/, committed engine.cpp untouched):
  Only four accessors, because everything else already routes through them:
    in_map      -- y-only now (x has no left/right edge)
    is_solid    -- reads the grid through wrap_x
    is_bedrock  -- same
    worm_at     -- compares wrapped x, so a shot across the seam still hits
  Blast radius still uses straight-line max(|dx|,|dy|); near the seam a blast does
  NOT reach across. That is a deliberate, documented simplification for the
  prototype, not an accident.

ARENA: playable ring is x in [2, 93] (92 columns). Border columns x=0,1,94,95 stay
bedrock so strict LOAD still accepts the file; they are simply never reachable.
Four seats sit at the quarter points x = 2, 25, 48, 71 -> every neighbour is 23
columns away, which is what a ring is supposed to buy.
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
PW = PX1 - PX0 + 1
DF = 31

IN_MAP_OLD = "static bool in_map(int x, int y) { return x >= 0 && x < W && y >= 0 && y < H; }"
# ★ 关键：in_map 必须【只判 y】。第一版写成 y 正常 + x 在 [PX0,PX1] 内，
#   于是 x=94 直接判越界 => do_move 走不过环缝 => 环面等于没做（seamtest 实测两列一致）。
IN_MAP_NEW = """// TORUS PROTOTYPE: x has no left/right edge -- it wraps over [PX0, PX1].
static int wrap_x(int x) {
    // 只对可玩区做环回；围栏列（x < PX0 或 x > PX1）由调用方另行处理
    int r = (x - PX0) % PXW;
    if (r < 0) r += PXW;
    return r + PX0;
}
static bool in_ring(int x) { return x >= PX0 && x <= PX1; }
static bool in_map(int x, int y) { return y >= 0 && y < H; }"""

SOLID_OLD = """static bool is_solid(const Game &G, int x, int y) {
    if (!in_map(x, y)) return true;
    const char c = G.grid[y][x];
    return c == '#' || c == 'X';
}"""
SOLID_NEW = """static bool is_solid(const Game &G, int x, int y) {
    if (y < 0 || y >= H) return true;
    // 围栏列（x<PX0 或 x>PX1）视为基岩，不进 wrap —— 否则 wrap_x(0)=92 会把
    // 围栏格读成真实列 92（别名）。SSE 2026-11-10.
    if (!in_ring(x)) return true;
    const char c = G.grid[y][wrap_x(x)];
    return c == '#' || c == 'X';
}"""

BEDROCK_OLD = """static bool is_bedrock(const Game &G, int x, int y) {
    if (!in_map(x, y)) return true;
    return G.grid[y][x] == 'X';
}"""
BEDROCK_NEW = """static bool is_bedrock(const Game &G, int x, int y) {
    if (y < 0 || y >= H) return true;
    if (!in_ring(x)) return true;
    return G.grid[y][wrap_x(x)] == 'X';
}"""

WORM_OLD = """static int worm_at(const Game &G, int x, int y) {
    for (int i = 0; i < NW; i++) {
        if (G.worm[i].alive && G.worm[i].x == x && G.worm[i].y == y) return i;
    }
    return -1;
}"""
WORM_NEW = """static int worm_at(const Game &G, int x, int y) {
    if (!in_ring(x)) return -1;
    const int wx = wrap_x(x);
    for (int i = 0; i < NW; i++) {
        if (G.worm[i].alive && in_ring(G.worm[i].x)
            && wrap_x(G.worm[i].x) == wx && G.worm[i].y == y) return i;
    }
    return -1;
}"""

EXPL_OLD = """            if (is_bedrock(G, tx, ty)) continue;
            if (G.grid[ty][tx] == '#') G.grid[ty][tx] = '.';"""
# ★★ 环面上「离爆炸点多远」必须用【环形最短距离】，不是矩形直线距离。
#    原版 d = max(|dx|,|dy|)，在 x=93 炸、目标 x=2 时算出 d=91（够不到），
#    而环面真实距离只有 2 —— 这就是「拓扑等价但行为不等价」的根源。
DIST_OLD = """            const int d = std::max(dx < 0 ? -dx : dx, dy < 0 ? -dy : dy);
            if (d > spec.radius) continue;"""
DIST_NEW = """            const int ax = dx < 0 ? -dx : dx;
            const int rx = ax < PXW - ax ? ax : PXW - ax;   // 环形最短
            const int ay = dy < 0 ? -dy : dy;
            const int d = std::max(rx, ay);
            if (d > spec.radius) continue;"""
EXPL_NEW = """            if (is_bedrock(G, tx, ty)) continue;
            const int ux = wrap_x(tx);
            if (G.grid[ty][ux] == '#') G.grid[ty][ux] = '.';"""

DIG_OLD = """                if (G.grid[gy][gx] == '#' && p.dug < 3) {
                    G.grid[gy][gx] = '.';"""
DIG_NEW = """                const int ux2 = wrap_x(gx);
                if (G.grid[gy][ux2] == '#' && p.dug < 3) {
                    G.grid[gy][ux2] = '.';"""

MOVE_OLD = """    const int tx = w.x + dx;
    if (is_air(G, tx, w.y) && worm_at(G, tx, w.y) < 0) {
        w.x = tx;
        return true;
    }
    // 上一格台阶
    if (is_air(G, tx, w.y - 1) && worm_at(G, tx, w.y - 1) < 0) {
        w.x = tx;
        w.y -= 1;
        return true;
    }
    return false;"""
MOVE_NEW = """    const int tx = wrap_x(w.x + dx);
    if (is_air(G, tx, w.y) && worm_at(G, tx, w.y) < 0) {
        w.x = tx;
        return true;
    }
    // 上一格台阶
    if (is_air(G, tx, w.y - 1) && worm_at(G, tx, w.y - 1) < 0) {
        w.x = tx;
        w.y -= 1;
        return true;
    }
    return false;"""

JUMP_OLD = """    const int tx = w.x + dx;
    const int ty = w.y - 1;"""
JUMP_NEW = """    const int tx = wrap_x(w.x + dx);
    const int ty = w.y - 1;"""

# ★★ 真正决定路径的是 parse_move_intent（engine.cpp:760-778），不是 do_move：
#    STEP 走 apply_orders -> parse_move_intent -> G.worm[].x = mv.x
#    前几版只改了 do_move/do_jump（那只被 LOAD 的存活校验用到），所以虫走到 94 就停了
INTENT_OLD_A = """    if (jump) {
        m.x = w.x + 2 * dx;
        m.y = w.y - 1;"""
INTENT_NEW_A = """    if (jump) {
        m.x = wrap_x(w.x + 2 * dx);
        m.y = w.y - 1;"""

INTENT_OLD_B = """    m.x = w.x + dx;
    m.y = w.y;"""
INTENT_NEW_B = """    m.x = wrap_x(w.x + dx);
    m.y = w.y;"""

# 环形场地在 strict LOAD 里还有一道坎：border 必须全是 X。
# 环缝要通，x=94/95 就不能是基岩 —— 但那会被判 "border must be bedrock"。
# 用正则定位整段并替换，避免手抄缩进出错（已经错过两次）。
import re as _re
BORDER_RE = _re.compile(
    r"    if \(strict\) \{\n"
    r"        for \(int y = 0; y < H; y\+\+\)\n"
    r"            for \(int x = 0; x < W; x\+\+\)\n"
    r"                if \(x < 2 \|\| x >= W - 2 \|\| y < 2 \|\| y >= H - 2\)\n"
    r"                    if \(rows\[y\]\[x\] != 'X'\) return \"border must be bedrock\";\n"
    r"    \}")
BORDER_NEW = """    if (strict) {
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++) {
                // TORUS: x >= W-2 是环缝列，允许是空气；其余边界仍必须基岩
                const bool side_seam = (x >= W - 2) && y >= 2 && y < H - 2;
                if (side_seam) {
                    if (rows[y][x] == '#') return "seam column must be air or bedrock";
                } else if (x < 2 || x >= W - 2 || y < 2 || y >= H - 2) {
                    if (rows[y][x] != 'X') return "border must be bedrock";
                }
            }
    }"""

GUARD_ANCHOR = "static const int WORM_HP = 100;"
GUARD = """

#ifndef P01_TORUS
#define P01_TORUS 0
#endif
#if P01_TORUS
// 环形 = 整宽 96 列都参与 wrap。早期版本用 PX0=2 / PXW=92，导致
// wrap_x(0)=92 与真实列 92 别名（围栏格被读成地形）。改成整宽后
// 不存在围栏列，坐标 0..95 一一对应，无别名。
static const int PX0 = 0;
static const int PXW = W;
static const int PX1 = PX0 + PXW - 1;
#endif"""


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def build_scratch(enable: bool) -> Path:
    """Build a scratch copy. enable=True applies the torus patch (spec section 3.8)."""
    src = (PUB / "engine" / "engine.cpp").read_text(encoding="utf-8")
    for name, anchor in (("in_map", IN_MAP_OLD), ("is_solid", SOLID_OLD),
                         ("is_bedrock", BEDROCK_OLD), ("worm_at", WORM_OLD),
                         ("WORM_HP", GUARD_ANCHOR)):
        if anchor not in src:
            raise SystemExit("anchor for %s not found -- engine.cpp changed?" % name)
    src = src.replace(GUARD_ANCHOR, GUARD_ANCHOR + GUARD, 1)
    if enable:
        patches = (("in_map", IN_MAP_OLD, IN_MAP_NEW),
                   ("is_solid", SOLID_OLD, SOLID_NEW),
                   ("is_bedrock", BEDROCK_OLD, BEDROCK_NEW),
                   ("worm_at", WORM_OLD, WORM_NEW),
                   ("explode", EXPL_OLD, EXPL_NEW),                   ("blast_dist", DIST_OLD, DIST_NEW),
                   ("drill", DIG_OLD, DIG_NEW),
                   ("move", MOVE_OLD, MOVE_NEW),
                   ("jump", JUMP_OLD, JUMP_NEW),
                   ("intent_jump", INTENT_OLD_A, INTENT_NEW_A),
                   ("intent_move", INTENT_OLD_B, INTENT_NEW_B))
        for name, old, new in patches:
            if old not in src:
                raise SystemExit("patch anchor for " + name + " not found")
            src = src.replace(old, new, 1)
        src, nsub = BORDER_RE.subn(BORDER_NEW, src, count=1)
        if nsub != 1:
            raise SystemExit("border patch did not match (nsub=%d)" % nsub)
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / ("engine_torus.cpp" if enable else "engine_plain.cpp")
    out.write_text(src, encoding="utf-8", newline="\n")
    exe = BUILD / ("engine_torus.exe" if enable else "engine_plain.exe")
    flag = "-DP01_TORUS=1" if enable else "-DP01_TORUS=0"
    r = subprocess.run(["g++", "-O2", "-std=c++17", "-static",
                        "-I", str(PUB / "engine"), flag, "-o", str(exe), str(out)],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-1500:])
    return exe


def blank():
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < 2 or x >= W - 2 or y < 2 or y >= H - 2:
                g[y][x] = "X"
    for y in range(DF, H - 2):
        for x in range(2, W - 2):
            g[y][x] = "#"
    return g


def perch(g, seat, cx, cy, half=5):
    """Perch on the ring. ★ 平台必须整段落在 [PX0, PX1] 之内：
    strict LOAD 要求 x<2 / x>=94 是基岩，越界一格就报 "border must be bedrock"。
    seat0 摆在 x=PX0 时，perch 的 -5..+5 会伸到 x=-3 —— 上一版就是这么被拒收的。
    """
    lo, hi = cx - half, cx + half
    if lo < PX0 or hi > PX1:
        raise SystemExit("perch at %d spans [%d,%d], outside ring [%d,%d]"
                         % (cx, lo, hi, PX0, PX1))
    for y in range(cy + 1, min(H - 2, cy + 4)):
        for x in range(lo, hi + 1):
            if g[y][x] != "X":
                g[y][x] = "#"
    for x in range(lo, hi + 1):
        g[cy][x] = "."
    for dx in (-4, 0, 4):
        if not (lo <= cx + dx <= hi):
            raise SystemExit("spawn offset %d outside perch" % dx)
        g[cy][cx + dx] = str(seat)


def make_ring():
    """Four seats at the quarter points of the [2,93] ring: neighbours all 23 apart."""
    g = blank()
    step = (PW) // 4          # 23
    # 四席放在 7 + seat*23 => 7, 30, 53, 76；perch ±5 => [2,93] 内，且步距相等
    for seat in range(4):
        perch(g, seat, PX0 + 5 + seat * step, DF - 1)
    return ["".join(r) for r in g]


def make_line():
    g = blank()
    for seat, cx in enumerate((16, 37, 58, 79)):
        perch(g, seat, cx, DF - 1)
    return ["".join(r) for r in g]


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=96)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--shifts", type=int, default=4)
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "v1")
    j4 = load(PUB / "judge" / "judge4.py", "v2")
    plain = build_scratch(False)
    torus = build_scratch(True)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    cases = [("line", make_line(), plain),
             ("ring", make_ring(), torus)]

    print("%-6s %6s %8s %8s %8s %8s %9s %7s"
          % ("case", "games", "seat0%", "seat1%", "seat2%", "seat3%", "spread", "淹死"))
    out = []
    for name, rows_txt, exe in cases:
        d = BUILD / "torarena" / name
        if d.is_dir():
            shutil.rmtree(str(d))
        d.mkdir(parents=True)
        mp = d / (name + ".txt")
        wins_all = collections.Counter()
        geo_all = collections.Counter()
        games_all = 0
        drown_all = 0
        rejected = ""
        for shift in range(args.shifts):
            rows2 = ["".join(str((int(c) + shift) % 4) if c in "0123" else c for c in r)
                     for r in rows_txt]
            mp.write_text("\n".join(rows2) + "\n", encoding="utf-8", newline="\n")
            p = subprocess.run([str(exe)], input="LOAD %s 1 15\nSTATE\n" % mp,
                               capture_output=True, text=True)
            err = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("ERR")]
            if err:
                rejected = err[0]
                break
            tasks = list(range(args.seeds))

            def run(sd):
                rp = d / ("r%d-%d.jsonl" % (shift, sd))
                j4.play_one(j, exe, mp, 1000 + sd * 37 + shift, [bot] * 4,
                            (0, 1, 2, 3), (0, 1, 2, 3), 0.5, replay_path=rp,
                            replay_grid=False, wall_allow_s=wa, jobs=jobs)
                return rp

            with ThreadPoolExecutor(max_workers=jobs) as ex:
                rps = list(ex.map(run, tasks))
            for rp in rps:
                if not rp.is_file():
                    continue
                turns = []
                with rp.open(encoding="utf-8") as fh:
                    for line in fh:
                        rec = json.loads(line)
                        if rec.get("type") == "turn":
                            turns.append(rec)
                        elif rec.get("type") == "result":
                            w = rec.get("winner", -1)
                            wins_all[w] += 1
                            if 0 <= w < 4:
                                geo_all[(w - shift) % 4] += 1
                            games_all += 1
                prev = None
                for rec in turns:
                    worms = rec.get("worms") or []
                    t = rec.get("t", 0)
                    if prev is not None:
                        for wid in range(min(len(worms), len(prev))):
                            if prev[wid][5] and not worms[wid][5]:
                                if prev[wid][1] >= water_at(t - 1):
                                    drown_all += 1
                    prev = worms
        if rejected:
            print("%-6s  REJECT %s" % (name, rejected))
            out.append({"case": name, "rejected": rejected})
            continue
        if not games_all:
            print("%-6s  0 games" % name)
            continue
        pct = [100.0 * geo_all.get(i, 0) / games_all for i in range(4)]
        spread = max(pct) - min(pct)
        print("%-6s %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %8.1f%% %7d"
              % (name, games_all, pct[0], pct[1], pct[2], pct[3], spread, drown_all))
        out.append({"case": name, "games": games_all,
                    "seat_pct_by_geometry": [round(x, 1) for x in pct],
                    "spread": round(spread, 1), "drown": drown_all,
                    "shifts": args.shifts, "seeds": args.seeds})
        shutil.rmtree(str(d), ignore_errors=True)

    p = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "torus.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("")
    print("wrote " + str(p.relative_to(REPO)))
    print("两组的席次份额都已折算回【几何位置】（消除重贴号的标签影响）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

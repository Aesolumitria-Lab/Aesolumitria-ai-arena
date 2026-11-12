#!/usr/bin/env python3
"""P01 跨平台「整局」对账：bot + 引擎一起比。

`p01_trace.py` 只比引擎（喂固定动作）。这个脚本比的是**整局**：
同一批 bot、同一批地图/seed/排列，跑完后每一局的 `state_hash` / 胜者 / 回合数
必须两边一模一样。它同时验三件事：

  1. 引擎跨平台一致（逐局终局 hash）
  2. bot 跨平台一致（bot 是决策源，它的输出不同会让 hash 提前分叉）
  3. 裁判的判罚口径一致（faults / forfeit 计数）

用法：

    # 两边各跑一次（地图目录用绝对路径）
    python3 p01_crossplatform.py run --game <包根> --bins "hard=...,normal=...,easy=...,random=..." \
        --maps <maps4 目录> --out side.json

    python3 p01_crossplatform.py compare a.json b.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def load(p: Path, n: str):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def resolve_judge(game: Path):
    judge_dir = game / "public" / "judge"
    j4 = load(judge_dir / "judge4.py", "p01_j4")
    return j4, j4._judge()


def do_run(args) -> int:
    game = Path(args.game).resolve()
    j4, j = resolve_judge(game)
    engine = j.build_engine(game)
    bots = []
    for spec in args.bins.split(","):
        name, _, path = spec.partition("=")
        bots.append((name, Path(path).resolve()))
    if len(bots) != 4:
        print("need exactly 4 bots", file=sys.stderr)
        return 2
    # 第 4 席用 random（下限），前三席按给定顺序
    maps = sorted(Path(args.maps).glob("*.txt"))[:args.maps_count]
    # perms=0 表示"全部"；judge4.schedule 里 0 会退化成 0 局，所以这里先换算
    perms = args.perms if args.perms > 0 else len(j4.circular_orders((0, 1, 2, 3))) * 4
    plan = j4.schedule((0, 1, 2, 3), perms, args.seeds_per_perm)
    tasks = [(mp, perm, 1 + off) for mp in maps for (perm, off, _) in plan]
    wa = j4.WALL_PER_JOB_S * max(0, args.jobs - 1)
    binpaths = [b for _, b in bots]

    def run(t):
        r = j4.play_one(j, engine, t[0], t[2], binpaths, (0, 1, 2, 3), t[1], 0.5,
                        replay_grid=False, wall_allow_s=wa)
        return {
            "map": t[0].stem, "perm": list(t[1]), "seed": t[2],
            "hash": r.get("state_hash"), "winner": r.get("winner"),
            "winner_bot": r.get("winner_bot"), "reason": r.get("reason"),
            "rounds": r.get("rounds"), "faults": r.get("faults", 0),
            "faults_by_seat": r.get("faults_by_seat"),
        }

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(run, tasks))
    out = {"bots": [n for n, _ in bots], "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    faults = sum(r["faults"] for r in rows)
    print(f"{len(rows)} games, {faults} faults, wrote {args.out}")
    return 0


def do_compare(a_path: str, b_path: str) -> int:
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    failed = False
    if a["bots"] != b["bots"]:
        print(f"bot name order differs: {a['bots']} vs {b['bots']}")
        failed = True
    ra, rb = a["rows"], b["rows"]
    if len(ra) != len(rb):
        print(f"row count differs: {len(ra)} vs {len(rb)}")
        return 1
    keys = ("hash", "winner", "winner_bot", "rounds")
    diffs = []
    for x, y in zip(ra, rb):
        if (x["map"], x["perm"], x["seed"]) != (y["map"], y["perm"], y["seed"]):
            print("row order mismatch")
            return 1
        for k in keys:
            if x[k] != y[k]:
                diffs.append((x["map"], x["perm"], x["seed"], k, x[k], y[k]))
    if diffs:
        failed = True
        print(f"\nMISMATCH {len(diffs)} fields over {len(ra)} games")
        for d in diffs[:12]:
            print("  %s perm=%s seed=%s  %s:  A=%s  B=%s" % d)
    else:
        print(f"hash/winner/rounds identical on all {len(ra)} games")
    # faults are reported, not compared: they may be genuine illegal moves
    fa = sum(r["faults"] for r in ra)
    fb = sum(r["faults"] for r in rb)
    print(f"faults: A={fa}  B={fb}" + ("  (相同)" if fa == fb else "  (不同，需人工看)"))
    n_forfeit_a = sum(1 for r in ra if r["reason"] == "FORFEIT")
    n_forfeit_b = sum(1 for r in rb if r["reason"] == "FORFEIT")
    print(f"forfeits: A={n_forfeit_a}  B={n_forfeit_b}")
    if n_forfeit_a != n_forfeit_b:
        failed = True
    print("\nPASS" if not failed else "\nFAIL")
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--game", required=True)
    r.add_argument("--bins", required=True, help="name=path,name=path,...（4 个）")
    r.add_argument("--maps", required=True)
    r.add_argument("--maps-count", type=int, default=3)
    r.add_argument("--perms", type=int, default=0, help="0 = 全部 24 种排列")
    r.add_argument("--seeds-per-perm", type=int, default=2)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    return do_run(args) if args.cmd == "run" else do_compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())

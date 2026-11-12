#!/usr/bin/env python3
"""P01 跨平台「bot 决策」对账：引擎状态相同，比 bot 的答复字符串。

做法：由引擎驱动一局（LOAD 固定图/seed），每回合把 STATE + GRID 按协议
包成 4 个 TURN 块，依次问 4 个 bot，把答复原样记下来，再用这 4 个答复 STEP。
→ 引擎侧已被 `p01_trace.py` 证明逐 hash 相同，所以两边答复若有差异，
  差异源必然是 bot 二进制（而不是引擎、不是裁判）。

    python3 p01_askcmp.py --game <包根> --bins a,b,c,d --map <图> --seed 1 --rounds 40 --out side.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _read_state(proc) -> str:
    proc.stdin.write("STATE\n")
    proc.stdin.flush()
    return proc.stdout.readline().rstrip("\r\n")


def _read_grid(proc) -> list[str]:
    proc.stdin.write("GRID\n")
    proc.stdin.flush()
    rows = []
    while True:
        line = proc.stdout.readline().rstrip("\r\n")
        if line == "GEND":
            return rows
        if line.startswith("G "):
            rows.append(line[2:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--bins", required=True, help="逗号分隔 4 个 bot 路径")
    ap.add_argument("--map", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mask", type=int, default=15)
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--perm", default="0,1,2,3",
                    help="座位排列 perm[i]=bot i 坐哪个席（和 judge4 的 perm 同义）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    perm = [int(x) for x in args.perm.split(",")]
    if len(perm) != 4:
        print("--perm needs 4 comma-separated seats", file=sys.stderr)
        return 2
    bots = [str(Path(b).resolve()) for b in args.bins.split(",")]
    if len(bots) != 4:
        print("need 4 bots", file=sys.stderr)
        return 2
    procs = [subprocess.Popen([b], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, bufsize=1) for b in bots]
    eng = subprocess.Popen([str(Path(args.engine).resolve())], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True, bufsize=1)
    eng.stdin.write(f"LOAD {Path(args.map).resolve()} {args.seed} {args.mask}\n")
    eng.stdin.flush()
    head = eng.stdout.readline().rstrip("\r\n")
    if not head.startswith("OK"):
        print(f"load failed: {head}", file=sys.stderr)
        return 2

    rounds = []
    for _ in range(args.rounds):
        state = _read_state(eng)
        parts = state.split()
        if len(parts) < 20 or parts[4] == "1":
            break
        grid = _read_grid(eng)
        grid_text = "GRID " + "\nGRID ".join(grid) + "\nEND\n"
        answers = []
        for i, seat in enumerate(perm):
            block = f"TURN {seat} {len(grid)}\n{state}\n{grid_text}"
            procs[i].stdin.write(block)
            procs[i].stdin.flush()
            line = procs[i].stdout.readline().rstrip("\r\n")
            answers.append(line)
        step = " | ".join(answers)
        eng.stdin.write(f"STEP {step}\n")
        eng.stdin.flush()
        new_state = eng.stdout.readline().rstrip("\r\n")
        rounds.append({
            "state": state.split()[19],        # hash of the state the bots saw
            "answers": answers,
            "after": new_state.split()[19] if new_state.startswith("STATE") else new_state,
            "after_round": new_state.split()[1] if new_state.startswith("STATE") else None,
        })

    try:
        eng.stdin.write("QUIT\n")
        eng.stdin.flush()
    except Exception:
        pass
    eng.kill()
    for p in procs:
        p.kill()

    Path(args.out).write_text(json.dumps({"rounds": rounds}, indent=1), encoding="utf-8")
    print(f"{len(rounds)} rounds recorded -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""P01 预算标定：三种「尺子」的对比实验。

这个公式是对的：

    time_budget = 50 ms × T_target / T_ref

但**用什么当 T** 决定了这个公式灵不灵。这台工具把三种候选尺子一次性量出来：

  R1  engine   —— 引擎自己跑一轮的耗时（"tick 时间"）。同一份 engine.cpp 两端都编。
                 优点：引擎就在包里、不可替换、量的正是"定点物理"。
                 风险：引擎是纯 ALU，bot 的耗时里缓存/内存占大头，可能量不出来。
  R2  bot      —— 标尺 bot（默认 TIDE-NORMAL）在固定局面上的单回合 CPU 中位。
                 优点：量的就是选手要干的那种活。
                 风险：需要包里带一个能在目标平台跑的基准二进制。
  R3  alu      —— 纯整数混合运算循环（跟题目无关的纯算力）。
                 优点：零依赖，任何机器都能跑。
                 风险：最可能与真实负载的比值脱节。

判据很简单：**在 A 机器上量出的比值，和在 B 机器上量出的比值，应该一致**。
如果 R3 说"慢 1.5×"而 R2 说"慢 2.7×"，那 R3 就是错的那把尺子 ——
用它换算预算会让慢机器上的预算仍然偏紧。

用法：
    python3 p01_ruler.py --game . --engine ./build/engine --bot ./tide_normal \\
        --map ./public/maps4/public4-01.txt --out host.json
    python3 p01_ruler.py --compare ref.json host.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------- R1 引擎

ENGINE_SCRIPT = (
    "0 M R; F ROCKET 42 89 | 3 M L; F ROCKET 138 89 | "
    "6 M L; F ROCKET 160 70 | 9 M R; F ROCKET 20 70"
)


def ruler_engine(engine_bin: Path, map_path: Path, seed: int, mask: int,
                 rounds: int) -> dict:
    """整轮耗时 = 从发 STEP 到收到 STATE 的墙钟（引擎无 I/O 时墙钟≈CPU）。

    引擎太便宜（一轮几十 microsecond），所以量 rounds 轮再平均，
    避免被系统时钟分辨率吃掉。
    """
    eng = subprocess.Popen([str(engine_bin)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True, bufsize=1)
    eng.stdin.write(f"LOAD {map_path} {seed} {mask}\n")
    eng.stdin.flush()
    head = eng.stdout.readline()
    if not head.startswith("OK"):
        raise SystemExit(f"引擎拒绝地图：{head.strip()}")

    # 先热身，避免把首次 cache miss 记进去
    for _ in range(50):
        eng.stdin.write(f"STEP {ENGINE_SCRIPT}\n")
        eng.stdin.flush()
        eng.stdout.readline()

    t0 = time.perf_counter()
    for _ in range(rounds):
        eng.stdin.write(f"STEP {ENGINE_SCRIPT}\n")
        eng.stdin.flush()
        eng.stdout.readline()
    t1 = time.perf_counter()
    eng.stdin.write("QUIT\n")
    eng.stdin.flush()
    try:
        eng.wait(timeout=3)
    except Exception:
        eng.kill()
    per_round_us = (t1 - t0) * 1e6 / rounds
    return {"ruler": "engine", "engine_us": round(per_round_us, 3),
            "per_round_us": round(per_round_us, 3), "rounds": rounds}


# ---------------------------------------------------------------- R3 纯算力

def ruler_alu(repeat: int) -> dict:
    """纯整数循环：和题目无关，只用来暴露"纯 ALU 看不出多少差异"这件事。"""
    code = (
        "unsigned long long x=1;"
        "for(unsigned long long i=0;i<%dULL;i++){x=x*6364136223846793005ULL+1;x^=x>>29;}"
        "return (int)(x&1);" % repeat
    )
    # 不依赖任何编译器：用一个极小的动态分配器 + 计时
    t0 = time.perf_counter()
    acc = 0
    for _ in range(repeat // 1000):
        acc ^= acc * 6364136223846793005 + 1
        acc ^= acc >> 29
    t1 = time.perf_counter()
    return {"ruler": "alu", "ns_per_unit": round((t1 - t0) * 1e9 / max(1, repeat // 1000), 3),
            "note": "python 循环，只能做粗粒度跨机对比", "_code_len": len(code),
            "_acc": acc & 1}


# ---------------------------------------------------------------- 汇总

def pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 预算标定：三种尺子")
    ap.add_argument("--engine", default="")
    ap.add_argument("--match", default="", help="p01_calibrate.py 的输出（R2 的读数）")
    ap.add_argument("--map", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mask", type=int, default=15)
    ap.add_argument("--rounds", type=int, default=20000)
    ap.add_argument("--alu-repeat", type=int, default=1000000)
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs=2, metavar=("REF", "HOST"))
    ap.add_argument("--ref-ms", type=float, default=50.0)
    args = ap.parse_args()

    if args.compare:
        ref = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        host = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        print(f"{'尺子':10s} {'参考机':>12s} {'本机':>12s} {'比值 T_target/T_ref':>22s}")
        print("-" * 62)
        table = {}
        for key, label, unit in (("engine_us", "R1 engine", "us/round"),
                                 ("bot_median_ms", "R2 bot", "ms/turn"),
                                 ("alu_ns", "R3 alu", "ns/unit")):
            a, b = ref.get(key), host.get(key)
            if a is None or b is None or a <= 0:
                print(f"{label:10s} {'n/a':>12s} {'n/a':>12s} {'—':>22s}")
                continue
            ratio = b / a
            table[key] = ratio
            print(f"{label:10s} {a:12.3f} {b:12.3f} {ratio:22.3f}   ({unit})")
        print()
        if "bot_median_ms" in table:
            s = table["bot_median_ms"]
            print(f"★ 推荐用 R2（bot）：budget = {args.ref_ms:.0f} × {s:.3f} "
                  f"= {args.ref_ms * s:.1f} ms")
            for k, label in (("engine_us", "R1 engine"), ("alu_ns", "R3 alu")):
                if k in table:
                    d = abs(table[k] - s) / max(s, 1e-9) * 100
                    verdict = "接近" if d <= 25 else "★ 偏差过大，不能当尺子"
                    print(f"   {label}: {table[k]:.3f}，与 R2 差 {d:.0f}%  ⇒ {verdict}")
                    if d > 25:
                        print(f"      （用它会算出 {args.ref_ms * table[k]:.1f} ms 的预算，"
                              f"而不是 {args.ref_ms * s:.1f} ms）")
        return 0

    if not (args.engine and args.map and args.out):
        ap.error("run 模式需要 --engine --map --out")
    payload = {"tool": "p01_ruler", "host": str(Path(args.out).stem)}
    payload.update(ruler_engine(Path(args.engine).resolve(),
                                Path(args.map).resolve(), args.seed, args.mask,
                                args.rounds))
    payload["alu_ns"] = ruler_alu(args.alu_repeat)["ns_per_unit"]
    if args.match:
        m = json.loads(Path(args.match).read_text(encoding="utf-8"))
        payload["bot_median_ms"] = m["median_ms"]
        payload["bot_p95_ms"] = m["p95_ms"]
        payload["scale_id"] = m.get("scale_id")
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"engine {payload['per_round_us']:.2f} us/round | alu {payload['alu_ns']:.2f} ns/unit"
          + (f" | bot {payload['bot_median_ms']:.3f} ms/turn" if args.match else ""))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

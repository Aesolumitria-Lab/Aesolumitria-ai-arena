#!/usr/bin/env python3
"""P01 单回合预算的宿主标定（landmark calibration）。

★ 为什么需要这个
----------------
RULES 里的「50 ms / 回合」是**绝对 CPU 时间**，所以它随评测机速度变化。
同一份 TIDE 二进制，32 核桌面 5.9 ms/回合，2 vCPU 云主机 14.5 ms/回合，
尾部（p99）差距更大。于是「及格线」在慢宿主上会自己 fault。

★ 这个工具做什么
----------------
用**同一个标尺 bot**（默认 TIDE-NORMAL）在**同一批固定局面上**实测它的
单回合 CPU，得到一个与宿主无关的比较基线：

    s = 本机标尺中位成本 / 参考机标尺中位成本
    budget = 50 ms × s

参考机（定义 s=1 的那台）写在 `meta.json` 的 `turn_budget.landmark` 里。

★ 为什么不能用「纯算力微基准」或「CPU 型号」
--------------------------------------------
实测（见 docs/crossplatform-and-host-speed.md）：纯整数 ALU 微基准两边差 1.5×，TIDE 差 2.5×，尾部更大。
因为 bot 的耗时里缓存/内存带宽占比很高，ALU 基准量不出来。
⇒ **标尺必须是真正要跑的那种 bot**，而且局面必须固定，否则量到的是棋局差异。

★ 为什么用「批量求平均」而不是逐次采样
--------------------------------------
Windows `GetProcessTimes` 粒度 15.625 ms、Linux `/proc` 粒度 10 ms。
单次采样会把 5 ms 的回合记成 0 或 15.6。所以每个局面连问 N 次、
量总 CPU 再除以 N，精度 = 粒度/N。

用法：
    python p01_calibrate.py --engine ./engine --bot ./tide_normal \\
        --map ./public/maps4/public4-01.txt --repeat 20 --out host.json
    python p01_calibrate.py --compare ref.json host.json     # 打印 s

把 --json 的 s 传给 judge4：`--turn-scale <s>`（或环境变量 P01_TURN_SCALE）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# 固定局面脚本：与 p01_trace.py 的 4 席动作同源，保证两边看到的地形/血量一致。
SCRIPT = [
    (1, "0 M R; F ROCKET 42 89 | 3 M L; F ROCKET 138 89 | 6 M L; F ROCKET 160 70 | 9 M R; F ROCKET 20 70"),
    (2, "0 WAIT | 3 WAIT | 6 WAIT | 9 WAIT"),
    (3, "0 M R; F GRENADE 60 70 | 3 J L; F GRENADE 120 70 | 6 F GRENADE 30 90 | 9 F GRENADE 150 90"),
    (4, "0 M R; M R; M R | 3 M L; M L; M L | 6 M R; M R; M R | 9 M L; M L; M L"),
    (5, "0 F DRILL 45 80 | 3 F DRILL 135 80 | 6 F DRILL 80 90 | 9 F DRILL 100 90"),
    (6, "0 M R; F ROCKET 90 100 | 3 M L; F ROCKET 90 100 | 6 WAIT | 9 WAIT"),
    (7, "0 M R; M R; F GRENADE 30 100 | 3 M L; M L; F GRENADE 150 100 | 6 WAIT | 9 WAIT"),
    (8, "0 J R; J R; F ROCKET 90 40 | 3 J L; J L; F ROCKET 90 40 | 6 WAIT | 9 WAIT"),
]


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def _load_processcpu(game_dir: Path):
    """复用题库自己的 CPU 计时器，避免两套口径。"""
    judge_dir = game_dir / "public" / "judge"
    for name in ("judge2.py", "judge.py"):
        cand = judge_dir / name
        if cand.is_file():
            s = importlib.util.spec_from_file_location("p01_cal_judge", cand)
            m = importlib.util.module_from_spec(s)
            s.loader.exec_module(m)
            return m.ProcessCPU
    raise SystemExit(f"找不到 judge.py / judge2.py：{judge_dir}")


def read_lines(proc) -> list[str]:
    rows = []
    while True:
        line = proc.stdout.readline().rstrip("\r\n")
        if line == "GEND":
            return rows
        if line.startswith("G "):
            rows.append(line[2:])


def collect_blocks(engine_bin: Path, map_path: Path, seed: int, mask: int,
                   rounds: int) -> list[tuple[str, str]]:
    """用固定答句把引擎推到 N 个确定性的局面，返回 [(state, turn_block), ...]。"""
    eng = subprocess.Popen([str(engine_bin)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True, bufsize=1)
    eng.stdin.write(f"LOAD {map_path} {seed} {mask}\n")
    eng.stdin.flush()
    head = eng.stdout.readline().rstrip("\r\n")
    if not head.startswith("OK"):
        raise SystemExit(f"引擎拒绝地图：{head}")
    eng.stdin.write("STATE\n")
    eng.stdin.flush()
    state = eng.stdout.readline().rstrip("\r\n")
    eng.stdin.write("GRID\n")
    eng.stdin.flush()
    grid = read_lines(eng)
    grid_text = "GRID " + "\nGRID ".join(grid) + "\nEND\n"

    blocks = []
    for i in range(rounds):
        parts = state.split()
        if len(parts) < 20 or parts[4] == "1":
            break
        # 每席的 TURN 块不同（seat 不同），但同一 fixed script 下四席局面固定
        for seat in range(4):
            if len(parts) < 21 + seat:
                continue
            blocks.append((f"r{i}s{seat}", f"TURN {seat} {len(grid)}\n{state}\n{grid_text}"))
        seq = SCRIPT[i % len(SCRIPT)][1]
        eng.stdin.write(f"STEP {seq}\n")
        eng.stdin.flush()
        state = eng.stdout.readline().rstrip("\r\n")
        eng.stdin.write("GRID\n")
        eng.stdin.flush()
        grid = read_lines(eng)
        grid_text = "GRID " + "\nGRID ".join(grid) + "\nEND\n"
    eng.stdin.write("QUIT\n")
    eng.stdin.flush()
    try:
        eng.wait(timeout=3)
    except Exception:
        eng.kill()
    return blocks


def measure(bot_bin: Path, blocks: list[tuple[str, str]], repeat: int,
            cpu_meter, granularity_ms: float) -> list[dict]:
    """一个 bot 进程；每个局面连问 repeat 次，量总 CPU 再除以 repeat。"""
    bot = subprocess.Popen([str(bot_bin)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True,
                           bufsize=1, encoding="utf-8")
    cpu = cpu_meter(bot.pid)
    out = []
    for tag, block in blocks:
        base = cpu.sample()
        if base is None:
            raise SystemExit("这台机器读不到进程 CPU（ProcessCPU.sample() 返回 None）")
        for _ in range(repeat):
            bot.stdin.write(block)
            bot.stdin.flush()
            line = bot.stdout.readline()
            if not line:
                raise SystemExit(f"bot 提前退出（局面 {tag}）")
        used = cpu.sample()
        per_turn = (used - base) * 1000.0 / repeat
        out.append({"pos": tag, "ms": per_turn,
                    # 批量平均后仍受粒度约束：单次采样分辨率 = 粒度/repeat
                    "res_ms": granularity_ms / repeat})
    bot.kill()
    cpu.close()
    return out


def cmd_run(args) -> int:
    game = Path(args.game).resolve()
    engine_bin = Path(args.engine).resolve()
    bot_bin = Path(args.bot).resolve()
    map_path = Path(args.map).resolve()
    cpu_meter = _load_processcpu(game)
    # 用一个空进程量粒度，读到的是 ProcessCPU 自己的 granularity_ms 属性
    probe = subprocess.Popen([str(bot_bin)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, bufsize=1)
    gran = cpu_meter(probe.pid).granularity_ms
    probe.kill()

    blocks = collect_blocks(engine_bin, map_path, args.seed, args.mask, args.rounds)
    if not blocks:
        raise SystemExit("没采集到任何局面")
    samples = measure(bot_bin, blocks, args.repeat, cpu_meter, gran)
    ms = [s["ms"] for s in samples]
    payload = {
        "tool": "p01_calibrate",
        # scale_id 是「这是同一把尺子」的唯一凭据。两边必须给同一个值，
        # 否则比值不可比（不同 bot 的耗时形状完全不同）。
        "scale_id": args.scale_id,
        "bot": bot_bin.name,
        "map": map_path.stem,
        "seed": args.seed,
        "mask": args.mask,
        "positions": len(samples),
        "repeat": args.repeat,
        "granularity_ms": gran,
        "resolution_ms": gran / args.repeat,
        "median_ms": round(pct(ms, 0.50), 4),
        "p95_ms": round(pct(ms, 0.95), 4),
        "p99_ms": round(pct(ms, 0.99), 4),
        "max_ms": round(max(ms), 4),
        "min_ms": round(min(ms), 4),
        "samples": samples,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    print(f"{payload['bot']}: {payload['positions']} 局面 × {args.repeat} 次, "
          f"中位 {payload['median_ms']:.3f} ms  p95 {payload['p95_ms']:.3f} ms "
          f"(分辨率 {payload['resolution_ms']:.3f} ms)")
    print(f"wrote {args.out}")
    return 0


def cmd_compare(ref_path: str, host_path: str, ref_ms: float) -> int:
    ref = json.loads(Path(ref_path).read_text(encoding="utf-8"))
    host = json.loads(Path(host_path).read_text(encoding="utf-8"))
    # 同一把尺子 = 同一个 scale_id（不同文件名/不同平台后缀都无所谓）。
    if ref.get("scale_id") != host.get("scale_id"):
        print(f"★ 标尺不同（{ref.get('scale_id')!r} vs {host.get('scale_id')!r}），"
              "比值不可用", file=sys.stderr)
        return 2
    if ref["positions"] != host["positions"] or ref["repeat"] != host["repeat"]:
        print(f"★ 局面数/repeat 不同（{ref['positions']}×{ref['repeat']} vs "
              f"{host['positions']}×{host['repeat']}），比值不可用", file=sys.stderr)
        return 2
    for q, key in (("中位", "median_ms"), ("p95", "p95_ms"), ("p99", "p99_ms")):
        a, b = ref[key], host[key]
        if a <= 0:
            continue
        print(f"  {q:4s}: 参考 {a:8.3f} ms   本机 {b:8.3f} ms   s = {b / a:6.3f}")
    s = host["median_ms"] / ref["median_ms"] if ref["median_ms"] > 0 else 1.0
    print(f"\n建议 --turn-scale {s:.3f}  ⇒  本机预算 = {ref_ms:.0f} × {s:.3f} "
          f"= {ref_ms * s:.1f} ms")
    p95 = host["p95_ms"] / ref["p95_ms"] if ref["p95_ms"] > 0 else s
    if abs(s - p95) / max(s, 1e-9) > 0.25:
        print(f"★ 注意：p95 的比值是 {p95:.3f}，与中位比值 {s:.3f} 相差 "
              f"{abs(s - p95) / max(s, 1e-9) * 100:.0f}%。")
        print("  这通常意味着本机有争用/抖动（核少、内存小、有别的活在跑），")
        print("  而不是单纯的 CPU 慢。标量转换只能还原「速度」，还原不了「抖动」。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 单回合预算的宿主标定")
    ap.add_argument("--game", default=str(Path(__file__).resolve().parents[1] /
                                          "problems" / "p01-ballistic"))
    ap.add_argument("--engine", default="")
    ap.add_argument("--bot", default="")
    ap.add_argument("--map", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mask", type=int, default=15)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=20)
    ap.add_argument("--scale-id", default="tide-normal@public4-01",
                    help="这把尺子的身份；两边必须一致，默认按 bot+图 生成")
    ap.add_argument("--ref-ms", type=float, default=50.0,
                    help="参考机上的单回合预算（默认 50）")
    ap.add_argument("--out", default="")
    ap.add_argument("--compare", nargs=2, metavar=("REF.json", "HOST.json"))
    args = ap.parse_args()

    if args.compare:
        return cmd_compare(args.compare[0], args.compare[1], args.ref_ms)
    if not (args.engine and args.bot and args.map and args.out):
        ap.error("run 模式需要 --engine --bot --map --out")
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())

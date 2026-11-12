#!/usr/bin/env python3
"""P01 v2 FFA anchor 档位审计：单调性、区分度、座位偏差、faults。

    python tools/anchor_audit4.py --seeds 1-2
    python tools/anchor_audit4.py --hidden --seeds 1-2 --strict
    python tools/anchor_audit4.py --seats 3 --seeds 1

`--anchors` 的顺序就是档位阶梯（默认 meta anchors4 的前 seats 个，弱 → 强）。
每个 (地图, seed) 跑 judge4 的全部座位全排列，因此每人在每个座位上的局数相同。

判定口径
--------
- 相邻档位比较**原始胜率差** Δ = 上档 − 下档：
  Δ < 0 记 INVERTED（倒挂），0 ≤ Δ < min-gap 记 FLAT，其余 GAP_OK；
- FFA 的胜率基线是 1/seats（4 席 25%），不是两两对局的 50%；
  不要把 0.5 当参照，也不要跨座位数比较绝对胜率；
- `--min-gap`（默认 5pp）是**描述性门槛，不是显著性检验**：4 席 24 局/人时
  Wilson 95% 半宽约 ±17pp，5pp 的差距远小于噪声。判定只用来暴露倒挂和
  完全贴住的档位，GAP_OK 也不代表显著，不能据此宣称"显著更强/相等"；
- CI 重叠 = 本样本分不出高下，不等于两者真实胜率相等；
- 退出码：有 INVERTED 或未完成局时为 1；`--strict` 再把 FLAT 也算失败。

faults 来自本工具的 Bot.ask 包装（judge4 只汇总总数）；超时按 WAIT 计入
引擎结算，所以有 faults 时胜率仍可用但可信度下降。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ffa4  # noqa: E402
import harness  # noqa: E402


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def num(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def ci_overlap(a: dict, b: dict) -> bool:
    return not (a["wilson95"][1] < b["wilson95"][0]
                or b["wilson95"][1] < a["wilson95"][0])


def pairs_of(names: list[str], summ: dict, min_gap: float) -> list[dict]:
    out = []
    for lower, upper in zip(names, names[1:]):
        lo, hi = summ[lower], summ[upper]
        delta = hi["win_rate"] - lo["win_rate"]
        if delta < 0:
            verdict = "inverted"
        elif delta < min_gap:
            verdict = "flat"
        else:
            verdict = "gap_ok"
        out.append({
            "lower": lower, "upper": upper,
            "lower_rate": lo["win_rate"], "upper_rate": hi["win_rate"],
            "delta": round(delta, 4),
            "verdict": verdict,
            "ci_overlap": ci_overlap(lo, hi),
        })
    return out


def markdown(cfg: dict, names: list[str], summ: dict, pairs: list[dict],
             active: tuple[int, ...], seat_totals: dict) -> str:
    verdicts = [p["verdict"] for p in pairs]
    inverted = verdicts.count("inverted")
    flat = verdicts.count("flat")
    order = sorted(names, key=lambda n: (-summ[n]["win_rate"],
                                         summ[n]["avg_place"] if summ[n]["avg_place"] is not None else 99.0))
    lines = [
        "# P01 v2 · FFA anchor 审计",
        "",
        f"- 地图：{', '.join(cfg['maps'])}（{len(cfg['maps'])} 张，{cfg['map_source']}）",
        f"- seeds：{', '.join(str(s) for s in cfg['seeds'])}；{cfg['seats']} 席；"
        f"每 (地图, seed) {cfg['rotations']} 种座位排列 → {cfg['games_per_bot']} 局/人"
        + ("；2 席沿用 judge4 的座位 0/2" if cfg["seats"] == 2 else ""),
        f"- 阈值 min-gap={cfg['min_gap'] * 100:.1f}pp（描述性，不是显著性检验）；"
        f"INVERTED {inverted} 处，FLAT {flat} 处",
        "",
        "## 单档",
        "",
        "| anchor | W/G | 胜率 | Wilson 95% | 平均名次 | HP/局 | 故障 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in names:
        s = summ[name]
        lines.append(
            f"| {name} | {s['wins']}/{s['games']} | {pct(s['win_rate'])} "
            f"| [{pct(s['wilson95'][0])}, {pct(s['wilson95'][1])}] "
            f"| {num(s['avg_place'], 2)} | {num(s['mean_hp'])} | {s['faults']} |"
        )
    lines += [
        "",
        "## 每座位胜率（W/G）",
        "",
        "| anchor | " + " | ".join(f"S{s}" for s in active) + " |",
        "|---" * (len(active) + 1) + "|",
    ]
    for name in names:
        cells = []
        by_seat = {r["seat"]: r for r in summ[name]["seat_win_rate"]}
        for s in active:
            r = by_seat[s]
            cells.append(f"{r['wins']}/{r['games']} ({pct(r['rate'])})")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += [
        "| 座位合计 | " + " | ".join(
            f"{seat_totals[s]['wins']}/{seat_totals[s]['games']} "
            f"({pct(seat_totals[s]['wins'] / seat_totals[s]['games']) if seat_totals[s]['games'] else '-'})"
            for s in active) + " |",
        "",
        "## 单调性（相邻档位，上档 − 下档）",
        "",
        "| 上档 | 下档 | Δ胜率 | 判定 | Wilson 区间重叠 |",
        "|---|---|---|---|---|",
    ]
    for p in pairs:
        sign = "+" if p["delta"] >= 0 else ""
        lines.append(f"| {p['upper']} | {p['lower']} | {sign}{p['delta'] * 100:.1f}pp "
                     f"| {p['verdict'].upper()} | {'是' if p['ci_overlap'] else '否'} |")
    spread = max(s["win_rate"] for s in summ.values()) - min(s["win_rate"] for s in summ.values())
    half = 1.96 * math.sqrt(cfg["baseline"] * (1 - cfg["baseline"])
                            / max(1, cfg["games_per_bot"])) * 100
    noise = (f"> 当前 {cfg['games_per_bot']} 局/人，基线附近的 95% 半宽约 ±{half:.0f} 个百分点；"
             f"min-gap={cfg['min_gap'] * 100:.0f}pp 远小于噪声。"
             if cfg["games_per_bot"] else "> 没有有效对局，判定只反映 0 样本。")
    lines += [
        "",
        "## 区分度",
        "",
        "- 观测排序：" + " > ".join(f"{n} ({pct(summ[n]['win_rate'])})" for n in order),
        f"- 胜率极差：{spread * 100:.1f}pp；基线 {pct(cfg['baseline'])}（{cfg['seats']} 席均分）",
        "",
        noise,
        "> INVERTED 只说明本次样本里上档胜率不高于下档，不说明上档更弱；"
        "FLAT 也不说明档位相等。CI 重叠时不要下强弱结论。",
        "> Wilson 区间按独立局近似；同一地图/seed 的轮换对局相关，实际不确定性只会更大。",
        "> 平均名次是 (最终 HP → 伤害 → 存活数) 的代理，不是官方名次；"
        "每座位样本 = 地图数 × seed 数，座位胜率同样只是描述。",
    ]
    if inverted:
        bad = ", ".join(f"{p['upper']}≤{p['lower']}" for p in pairs if p["verdict"] == "inverted")
        lines.append(f"> 本次存在倒挂：{bad}。建议加地图/seed 或先查 anchor 行为，再判断档位。")
    elif flat:
        flat_pairs = ", ".join(f"{p['upper']}−{p['lower']}" for p in pairs if p["verdict"] == "flat")
        lines.append(f"> 无倒挂，但未拉开 min-gap：{flat_pairs}。这通常只是样本噪声，不等于档位相等。")
    else:
        lines.append("> 相邻档位均拉开 min-gap；仍只是本样本的描述性结果，不是显著性结论。")
    if cfg["failed"]:
        lines.append(f"> 有 {cfg['failed']} 局未完成，已从统计中剔除。")
    if cfg["faults"]:
        lines.append(f"> 总故障 {cfg['faults']} 次：超时按 WAIT 计入引擎结算，"
                     "胜率可用但可信度下降。")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 v2 FFA anchor 档位审计")
    ap.add_argument("--game", default=ffa4.GAME)
    ap.add_argument("--seats", type=int, default=4)
    ap.add_argument("--anchors", default="",
                    help="逗号分隔，顺序即档位阶梯；默认 meta anchors4 的前 seats 个")
    ap.add_argument("--seeds", default="1-2")
    ap.add_argument("--map", action="append", default=[], help="地图路径，可重复")
    ap.add_argument("--maps-dir", default="")
    ap.add_argument("--hidden", action="store_true")
    ap.add_argument("--timeout", type=float, default=0.5)
    ap.add_argument("--min-gap", type=float, default=0.05,
                    help="相邻档位视为拉开的最小胜率差（描述性，默认 0.05）")
    ap.add_argument("--strict", action="store_true", help="FLAT 也算失败（退出码 1）")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    if args.seats < 2 or args.seats > 4:
        ap.error("--seats 只支持 2/3/4")
    if args.timeout <= 0:
        ap.error("--timeout 必须 > 0")
    if not 0 <= args.min_gap <= 1:
        ap.error("--min-gap 必须在 0..1 之间")

    game = harness.game_dir(args.game)
    seeds = ffa4.parse_seeds(args.seeds)
    if not seeds:
        ap.error("--seeds 为空")
    maps = ffa4.pick_maps(game, args.map, args.maps_dir, args.hidden)
    names = ffa4.resolve_anchors(game, args.anchors, args.seats)
    if len(names) < 2:
        ap.error("至少需要两个 anchor 才能做单调性审计")

    runner = ffa4.Runner(game, names, args.seats, args.timeout,
                         jobs=getattr(args, "jobs", 1))
    t0 = time.time()
    rows = list(runner.iter_matches(maps, seeds))

    stats = ffa4.aggregate(rows, names)
    summ = {name: ffa4.summary(name, s) for name, s in zip(names, stats)}
    pairs = pairs_of(names, summ, args.min_gap)

    seat_totals = {s: {"wins": 0, "games": 0} for s in range(4)}
    for s in stats:
        for seat in range(4):
            seat_totals[seat]["wins"] += s["seat_wins"][seat]
            seat_totals[seat]["games"] += s["seat_games"][seat]

    ok_rows = [r for r in rows if r["ok"]]
    cfg = {
        "seats": args.seats,
        "maps": [p.stem for p in maps],
        "map_source": ffa4.map_source(args),
        "seeds": seeds,
        "rotations": len(runner.perms),
        "games_per_bot": len(ok_rows),
        "baseline": 1.0 / args.seats,
        "min_gap": args.min_gap,
        "failed": len(rows) - len(ok_rows),
        "faults": sum(r.get("faults", 0) for r in ok_rows),
    }
    md = markdown(cfg, names, summ, pairs, runner.active, seat_totals)
    print(md, end="")

    inverted = sum(1 for p in pairs if p["verdict"] == "inverted")
    flat = sum(1 for p in pairs if p["verdict"] == "flat")
    failed = cfg["failed"] > 0 or inverted > 0 or (args.strict and flat > 0)
    payload = {
        "tool": "anchor_audit4", "game": game.name,
        "seats": args.seats, "anchors": names,
        "maps": cfg["maps"], "seeds": seeds,
        "rotations_per_map_seed": cfg["rotations"],
        "timeout_s": args.timeout,
        "games_per_anchor": cfg["games_per_bot"],
        "matches_failed": cfg["failed"],
        "faults": cfg["faults"],
        "baseline_win_rate": round(cfg["baseline"], 4),
        "min_gap": args.min_gap,
        "threshold_kind": "descriptive_not_significance_test",
        "stats": {name: summ[name] for name in names},
        "pairs": pairs,
        "seat_totals": {f"S{s}": seat_totals[s] for s in runner.active},
        "counts": {"inverted": inverted, "flat": flat,
                   "gap_ok": sum(1 for p in pairs if p["verdict"] == "gap_ok")},
        "failed": failed,
        "elapsed_s": round(time.time() - t0, 2),
    }
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    if args.md:
        p = Path(args.md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
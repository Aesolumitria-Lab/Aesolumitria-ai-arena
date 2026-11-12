#!/usr/bin/env python3
"""P01 v2 FFA 原始胜率表（2/3/4 席，全座位排列）。

    python tools/rating4.py --seeds 1-2
    python tools/rating4.py --seats 3 --seeds 1 --map public4-01.txt
    python tools/rating4.py --hidden --seeds 1-2 --json build/rating4.json

口径（故意保守）
--------------
- 每个 (地图, seed) 跑 judge4.rotations 的全部座位排列，每人每座一次；
  表里的 win% 是**原始胜率**，4 席基线 25%、3 席 33.3%、2 席 50%；
- 同时给 Wilson 95% 区间：区间重叠只说明本样本分不出高下，
  不能读成"相等"或"打平"；
- avg place 用最终 (HP, 伤害, 存活数) 的代理名次（引擎超时结算的排序键），
  淘汰局里未胜者都是 0 HP，其先后只是该键的近似，不是官方名次；
- **不拟合 BT/Elo**：4 席 FFA 没有天然的两两胜负，把一局拆成 6 对
  "谁赢了谁"会产生强相关伪样本；样本又小时，绝对分会比原始胜率更假。
  要 BT 请用 2 席 tools/rating.py，或先把地图/seed 样本做上去再另议。

限制：样本量由 `--seeds` × 地图数 × 轮换数决定；1 张图 1 个 seed
（4 席时每人 24 局）只能当冒烟，不能当档位结论。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ffa4  # noqa: E402
import harness  # noqa: E402


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def num(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def sort_key(name: str, summ: dict) -> tuple:
    s = summ[name]
    return (-s["win_rate"], s["avg_place"] if s["avg_place"] is not None else 99.0,
            -(s["mean_hp"] if s["mean_hp"] is not None else 0.0), name)


def markdown(cfg: dict, summ: dict, ranked: list[str]) -> str:
    seats = cfg["seats"]
    lines = [
        "# P01 v2 · FFA 原始胜率",
        "",
        f"- 地图：{', '.join(cfg['maps'])}（{len(cfg['maps'])} 张，{cfg['map_source']}）",
        f"- seeds：{', '.join(str(s) for s in cfg['seeds'])}；{seats} 席；"
        f"每 (地图, seed) {cfg['rotations']} 种座位排列 → 共 {cfg['matches']} 局",
        f"- 每 anchor {cfg['games_per_bot']} 局；基线胜率 {pct(cfg['baseline'])}"
        f"（{seats} 席均分）；超时 {cfg['timeout']}s",
        "",
        f"| # | anchor | W/G | 胜率 | Wilson 95% | 平均名次 | 名次分布 1..{seats} | HP/局 | 伤害/局 | 故障 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rank, name in enumerate(ranked, 1):
        s = summ[name]
        dist = "/".join(str(c) for c in s["place_counts"])
        lines.append(
            f"| {rank} | {name} | {s['wins']}/{s['games']} | {pct(s['win_rate'])} "
            f"| [{pct(s['wilson95'][0])}, {pct(s['wilson95'][1])}] "
            f"| {num(s['avg_place'], 2)} | {dist} | {num(s['mean_hp'])} "
            f"| {num(s['mean_dealt'])} | {s['faults']} |"
        )
    half = 1.96 * math.sqrt(cfg["baseline"] * (1 - cfg["baseline"])
                            / max(1, cfg["games_per_bot"])) * 100
    noise = (f"> 当前 {cfg['games_per_bot']} 局/人，基线附近的 95% 半宽约 ±{half:.0f} 个百分点；"
             "要拉开差距只能加地图/seed。"
             if cfg["games_per_bot"] else "> 没有有效对局，无法给出样本噪声参考。")
    lines += [
        "",
        "> 胜率是原始频率，不是绝对实力分；Wilson 区间只描述单人胜率的不确定性，"
        "区间重叠时排序不构成结论。",
        "> 排序以胜率为第一关键字；平均名次可能与胜率顺序不一致"
        "（赢少但更稳），名次和质量列只用于判断，不参与胜率定义。",
        noise,
        "> Wilson 区间按独立局近似；同一地图/seed 的轮换对局相关，实际不确定性只会更大。",
        "> 平均名次是 (最终 HP → 伤害 → 存活数) 的代理，不是官方名次。",
        "> 未拟合 BT/Elo：4 席没有天然两两胜负，拆对会产生相关伪样本；"
        "样本不足时绝对分只会更假。",
    ]
    if cfg["failed"]:
        lines.append(f"> 有 {cfg['failed']} 局未完成，已从统计中剔除。")
    if cfg["faults"]:
        lines.append(f"> 总故障 {cfg['faults']} 次（超时按 WAIT 计入引擎结算）。")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 v2 FFA 原始胜率（2/3/4 席，座位轮换）")
    ap.add_argument("--game", default=ffa4.GAME)
    ap.add_argument("--seats", type=int, default=4)
    ap.add_argument("--anchors", default="",
                    help="逗号分隔；默认取 meta anchors4 的前 seats 个")
    ap.add_argument("--seeds", default="1-2", help="如 1-2 或 1,3,5")
    ap.add_argument("--map", action="append", default=[], help="地图路径，可重复")
    ap.add_argument("--maps-dir", default="", help="地图目录（默认 public/maps4）")
    ap.add_argument("--hidden", action="store_true", help="用 private/maps4_hidden")
    ap.add_argument("--timeout", type=float, default=0.5,
                    help="单步 wall 兜底的【下限】（秒）；判超时看的是 CPU 预算"
                         "（50ms × s），不是它")
    ap.add_argument("--seeds-per-perm", type=int, default=2,
                    help="每个座位排列跑几个 seed（默认 2；同一 seed 下换座位 "
                         "会放大座次偏差，见 problems/p01-ballistic/docs/fairness-audit.md）。每个排列各用一个不同的 "
                         "seed 下标（seed_base + k）")
    ap.add_argument("--perms-per-map", type=int, default=0,
                    help="每张图用几个座位排列（默认 0 = 全排列；12/24 走邻接平衡）")
    ap.add_argument("--jobs", type=int, default=1,
                    help="并发对局数（默认 1；判超时只看 CPU，不受多开影响）")
    ap.add_argument("--boot", type=int, default=300,
                    help="胜率与成对判决的地图块 bootstrap 次数（默认 300；0=只出点估计）")
    ap.add_argument("--legacy", action="store_true",
                    help="输出旧口径表（Wilson 列 + 旧表头）；默认 v2 口径（problems/p01-ballistic/docs/rating-design.md §3–4）")
    ap.add_argument("--json", default="", help="JSON 输出路径")
    ap.add_argument("--md", default="", help="Markdown 输出路径")
    args = ap.parse_args()
    if args.seats < 2 or args.seats > 4:
        ap.error("--seats 只支持 2/3/4")
    if args.timeout <= 0:
        ap.error("--timeout 必须 > 0")

    game = harness.game_dir(args.game)
    seeds = ffa4.parse_seeds(args.seeds)
    if not seeds:
        ap.error("--seeds 为空")
    maps = ffa4.pick_maps(game, args.map, args.maps_dir, args.hidden)
    names = ffa4.resolve_anchors(game, args.anchors, args.seats)

    runner = ffa4.Runner(game, names, args.seats, args.timeout, jobs=args.jobs)
    runner.wall_allow_s = 0.25 * max(0, args.jobs - 1)
    t0 = time.time()
    plan = runner.plan(max(1, args.seeds_per_perm), args.perms_per_map)
    # 第三个字段是绝对 seed 下标（0..len(plan)-1）。取第二个会把一张图的 48 局
    # 压成 2 个 seed —— "每个排列换 seed"（RULES 11.2.2）就名存实亡了。
    tasks = [(mp, seed + seed_abs, perm) for mp in maps for seed in seeds
             for perm, _seed_index, seed_abs in plan]
    if args.jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            rows = list(ex.map(lambda t: runner.play(t[0], t[1], t[2]), tasks))
    else:
        rows = [runner.play(mp, seed, perm) for mp, seed, perm in tasks]

    stats = ffa4.aggregate(rows, names)
    summ = {name: ffa4.summary(name, s) for name, s in zip(names, stats)}
    ranked = sorted(names, key=lambda n: sort_key(n, summ))

    ok_rows = [r for r in rows if r["ok"]]

    # ---- v2 口径（problems/p01-ballistic/docs/rating-design.md §3–4）：主分 + 部分序 ----
    # 主分 = 原始胜率（固定 panel 下即多项 MLE）；成对判决用地图块 bootstrap
    # 配对做差。Wilson 假设独立局、低估不确定性 ⇒ 移入 --legacy。
    by_map = defaultdict(list)
    for r in ok_rows:
        by_map[r["map"]].append(r)
    map_ids = sorted(by_map)
    rng = random.Random(20261111)
    reps = []
    if args.boot and len(map_ids) >= 2:
        for _ in range(args.boot):
            pick = []
            for _ in range(len(map_ids)):
                pick.extend(by_map[map_ids[rng.randrange(len(map_ids))]])
            w = [0.0] * len(names)
            g = [0] * len(names)
            for r in pick:
                for i in range(len(names)):
                    g[i] += 1
                if r.get("winner_bot", -1) >= 0:
                    w[r["winner_bot"]] += 1
            reps.append([w[i] / max(g[i], 1) for i in range(len(names))])
    point = {b: summ[b]["win_rate"] for b in names}

    def pctl(xs, q):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, max(0, int(q * (len(xs) - 1))))]

    def pair_stats(a, b):
        d = point[a] - point[b]
        if reps:
            dl = sorted(rp[names.index(a)] - rp[names.index(b)] for rp in reps)
            lo, hi = pctl(dl, 0.025), pctl(dl, 0.975)
            p_ab = sum(1 for x in dl if x > 0) / len(dl)
            return d, lo, hi, p_ab, (lo > 0 or hi < 0)
        return d, None, None, None, None

    seg_ids = {ranked[0]: 1}
    seg = 1
    for k in range(1, len(ranked)):
        a, b = ranked[k - 1], ranked[k]
        if pair_stats(a, b)[4] is not True:
            seg_ids[b] = seg      # False=未分辨 / None=无置信区间：都不分段
        else:
            seg += 1
            seg_ids[b] = seg

    def v2_md(cfg, summ, ranked):
        lines = [
            "# P01 Rating v2（problems/p01-ballistic/docs/rating-design.md §3–4，rating4 口径）",
            "",
            f"- 地图：{', '.join(cfg['maps'])}（{len(cfg['maps'])} 张，{cfg['map_source']}）",
            f"- {cfg['matches']} 局 / 每 bot {cfg['games_per_bot']} 局；基线 {pct(cfg['baseline'])}",
            "- 主分 = panel 内胜率（固定阵容下即多项 MLE；不拟合、不拆对、不做 BT）",
            f"- CI/判决 = 按地图 cluster bootstrap {args.boot} 次"
            + ("" if reps else "（未跑）"),
            "- Wilson 已废除（假设独立局、低估不确定性），`--legacy` 可看旧表",
            "",
            "| 段 | bot | W/G | win% (95% CI) | 相邻对判决 | 平均名次（快照代理） | HP/局(参考) | 伤害/局 | 故障 |",
            "|---:|---|---:|---:|---|---:|---:|---:|---:|",
        ]
        for k, b in enumerate(ranked):
            s = summ[b]
            wlo, whi = (pctl([rp[names.index(b)] for rp in reps], 0.025),
                        pctl([rp[names.index(b)] for rp in reps], 0.975)) if reps \
                else (s["win_rate"], s["win_rate"])
            verdict = ""
            if k > 0:
                a = ranked[k - 1]
                d, lo, hi, _p, resolved = pair_stats(a, b)
                if resolved is None:
                    verdict = "—"
                elif resolved:
                    verdict = "与 %s 已分辨 (Δ=%.1fpp)" % (a, 100 * abs(d))
                else:
                    delta = abs(d)
                    se = max((hi - lo) / 3.92, 1e-9) if lo is not None else None
                    if se is None:
                        extra = ""
                    elif delta < 0.01:
                        extra = "，同档（Δ<1pp）"
                    else:
                        extra = "，需≈%d局" % int(round(
                            max(1, cfg["games_per_bot"]) * (2.0 / (delta / se)) ** 2))
                    verdict = "与 %s 未分辨 (Δ=%.1fpp%s)" % (a, 100 * delta, extra)
            wci = (f"{100*s['win_rate']:.2f}% [{100*wlo:.2f}, {100*whi:.2f}]"
                   if reps else f"{100*s['win_rate']:.2f}%")
            lines.append(
                f"| {seg_ids[b]} | {b} | {s['wins']}/{s['games']} | {wci} | {verdict} | "
                f"{num(s['avg_place'], 2)} | {num(s['mean_hp'])} | "
                f"{num(s['mean_dealt'])} | {s['faults']} |")
        lines += [
            "",
            "> 「段」相同 = 该相邻对胜率差在本样本下不可分辨（不是「相等」）。",
            "> 平均名次是 (最终 HP → 伤害 → 存活数) 的**快照代理**（本工具无回放，"
            "拿不到真实淘汰序）；完整 v2 口径（命中率/资源效率/破坏/淘汰序）"
            "请用 `problems/p01-ballistic/tools/p01_rating.py --replays <dir>`。",
            "> 过程列只作画像，不参与排名算术（problems/p01-ballistic/docs/swing-design.md §2.4）。",
        ]
        if cfg["failed"]:
            lines.append(f"> 有 {cfg['failed']} 局未完成，已从统计中剔除。")
        if cfg["faults"]:
            lines.append(f"> 总故障 {cfg['faults']} 次（超时按 WAIT 计入引擎结算）。")
        return "\n".join(lines) + "\n"

    cfg = {
        "seats": args.seats,
        "maps": [p.stem for p in maps],
        "map_source": ffa4.map_source(args),
        "seeds": seeds,
        "rotations": len({p for p, _, _ in plan}),
        "perms_per_map": len({p for p, _, _ in plan}),
        "seeds_per_perm": max(1, args.seeds_per_perm),
        "jobs": args.jobs,
        "matches": len(ok_rows),
        "games_per_bot": len(ok_rows),
        "baseline": 1.0 / args.seats,
        "timeout": args.timeout,
        "failed": len(rows) - len(ok_rows),
        "faults": sum(r.get("faults", 0) for r in ok_rows),
        "draws": sum(1 for r in ok_rows if r.get("winner_bot", -1) < 0),
    }
    all_pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            d, lo, hi, p_ab, resolved = pair_stats(a, b)
            all_pairs.append({
                "a": a, "b": b, "delta": round(d, 5),
                "ci": ([round(lo, 5), round(hi, 5)] if lo is not None else None),
                "p_ab": (round(p_ab, 4) if p_ab is not None else None),
                "resolved": resolved,
            })

    md = markdown(cfg, summ, ranked) if args.legacy else v2_md(cfg, summ, ranked)
    print(md, end="")

    payload = {
        "tool": "rating4", "game": game.name, "rating_model": "raw_win_rate",
        "seats": args.seats, "bots": names,
        "maps": cfg["maps"], "seeds": seeds,
        "rotations_per_map_seed": cfg["rotations"],
        "timeout_s": args.timeout,
        "matches": cfg["matches"], "matches_failed": cfg["failed"],
        "matches_per_bot": cfg["games_per_bot"],
        "draws": cfg["draws"], "faults": cfg["faults"],
        "baseline_win_rate": round(cfg["baseline"], 4),
        "place_model": "proxy:hp>dealt>alive",
        "bt": None,
        "bt_note": "4 席 FFA 无天然两两胜负；拆对为相关伪样本，样本不足时不拟合绝对分",
        "stats": {name: summ[name] for name in names},
        "ranking": ranked,
        "rating_version": 2,
        "score": "raw win rate (panel multinomial MLE; no fitting)",
        "segments": [{"segment": seg_ids[b], "bot": b} for b in ranked],
        "pairs": all_pairs,
        "full_v2_tool": "problems/p01-ballistic/tools/p01_rating.py --replays <dir>（含过程画像与真实淘汰序）",
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
    if cfg["failed"]:
        print(f"rating4: {cfg['failed']} 局未完成", file=sys.stderr)
    return 1 if cfg["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())


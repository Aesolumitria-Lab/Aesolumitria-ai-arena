#!/usr/bin/env python3
"""P01 rating v2：主分（panel 内胜率）+ 部分序 + 过程画像。

**这是 docs/rating-design.md §3–4 的可执行实现，也是唯一「完整 v2 口径」的工具**：
输入是 judge4 `--replay-dir` 产出的回放目录（既有胜负、也有逐回合事件）。

为什么这样算（每条都有实测依据，详见 docs/rating-design.md）：
- 固定 panel（每局同阵容）下，4 人 softmax / PL 的 MLE 解就是原始胜率
  （实测与拟合在 1e-6 内全等，docs/rating-design.md §2.5）⇒ 主分不拟合、不拆对、不做 BT；
- 座位调整实测否决（交互变号 + 成对 CI 翻倍，docs/swing-design.md §1.1）⇒ 不做座位修正；
  24 排列轮换已在机会层面精确配平；
- Wilson 区间假设独立局、已知低估不确定性 ⇒ 废除，改按**地图块 bootstrap**；
- 胜率 CI 重叠的对如实报「未分辨 + 还差多少局」，不硬排全序（docs/rating-design.md §4–5）；
- 过程列（dealt/命中率/资源效率/灭绝轮/破坏）只作画像，**不参与排名算术**
  （docs/swing-design.md §2.4 的验证门裁决：composite 不合成）。

统计口径：
- DRAW 局不计入胜率分母（另报数）；FORFEIT 局整局剔除（另报数）；
- 名次 place = 淘汰序（胜者 1，其余按真实灭绝轮降序；超时存活并列）；
- bootstrap 单位 = 地图（不是局：同图 24 排列强相关）。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import p01_swing  # noqa: E402  （组件提取的单一实现，不重复造）

SEATS = 4
# (键, 表头, 格式, 额外说明)
COLS = (
    ("dmg_out", "dealt/局", "{:.1f}"),
    ("hit_rate", "命中率", "{:.1%}"),
    ("res_eff", "资源效率", "{:.1f}"),
    ("ext_time", "灭绝轮", "{:.1f}"),
    ("blocks_out", "破坏/局", "{:.1f}"),
)

# ---- 单数字输出（HLTV 风格：1.00 = 场平均）----
# rating = 胜率 / 席位基线（4 席 = 25%）—— 排序分本身的可读刻度。
# PR     = 表现分：exp(PR_SCALE × Σ w·局内 z)，再按场几何均值归一到 1.00。
#          权重只用验证门里符号跨 5 panel 稳定的两个组件（docs/swing-design.md §2.4）：
#          dmg_out_share 为正、dmg_in 为负；kills/assists/hit_rate 符号跨场
#          翻面，不进 PR（只作画像列）。PR_SCALE 是展示尺度，只影响离散度
#          不影响排序。
PR_W = (("dmg_out_share", 0.8), ("dmg_in", -3.0))
PR_SCALE = 0.35


def place_of(g):
    """灭绝序名次（1 = 最好）：胜者 1，其余按 ext_time 降序，并列同分。"""
    ext = {s: g["comp"][s]["ext_time"] for s in range(SEATS)}
    ext[g["winner_seat"]] = 99.0
    return {s: 1 + sum(1 for t in range(SEATS) if ext[t] > ext[s])
            for s in range(SEATS)}


def percentile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(q * (len(xs) - 1))))]


def fmt(v, spec):
    return "-" if v is None else spec.format(v)


def load(replays: Path, rotating: bool = False):
    """swing.load + 本工具需要的派生量（canonical bot 顺序、逐局逐 bot 明细）。"""
    games, _names, skipped = p01_swing.load(replays)
    if not games:
        return None, None, skipped
    bots = sorted({b for g in games for b in g["seat_bots"]})
    games2 = []
    for g in games:
        sb = g["seat_bots"]
        if not rotating and set(sb) != set(bots):
            raise SystemExit("回放集不是固定 panel（同一目录出现不同阵容）："
                             + g["file"] + "（轮转赛请加 --rotating）")
        pl = place_of(g)
        per = {}
        for s in range(SEATS):
            c = g["comp"][s]
            per[sb[s]] = {
                "win": 1.0 if s == g["winner_seat"] else 0.0,
                "place": float(pl[s]),
                "pr_s": 0.0,
                "dmg_out_share": c.get("dmg_out_share"),
                "dmg_in": c.get("dmg_in"),
                **{k: c.get(k) for k, _, _ in COLS},
            }
        line = list(per)  # 本局的 4 个 bot（轮转赛时 != 全池）
        for key, wgt in PR_W:
            vals = [per[b][key] for b in line]
            mu = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
            for b in line:
                z = (per[b][key] - mu) / sd if sd > 1e-9 else 0.0
                per[b]["pr_s"] += PR_SCALE * wgt * z
        games2.append({"map": g["map"], "per": per})
    return games2, bots, skipped


def one_pass(games, bots):
    """点估计：每 bot 的 n、win%、各过程列均值（None 不计入分母）。"""
    out = {b: {"n": 0, "wins": 0.0, "place_sum": 0.0, "pr_sum": 0.0,
               "col_sum": {k: 0.0 for k, _, _ in COLS},
               "col_n": {k: 0 for k, _, _ in COLS}} for b in bots}
    for g in games:
        for b in g["per"]:      # 轮转赛：每局只有 4 个 bot
            d = g["per"][b]
            o = out[b]
            o["n"] += 1
            o["wins"] += d["win"]
            o["place_sum"] += d["place"]
            o["pr_sum"] += d["pr_s"]
            for k, _, _ in COLS:
                v = d[k]
                if v is not None:
                    o["col_sum"][k] += v
                    o["col_n"][k] += 1
    exps = {b: math.exp(out[b]["pr_sum"] / max(out[b]["n"], 1)) for b in bots}
    gmean = math.exp(sum(math.log(max(v, 1e-12)) for v in exps.values()) / len(exps))
    res = {}
    for b in bots:
        o = out[b]
        n = max(o["n"], 1)
        res[b] = {
            "n": o["n"], "wins": int(round(o["wins"])) if o["wins"].is_integer() else o["wins"],
            "win": o["wins"] / n,
            "rating": (o["wins"] / n) * SEATS,
            "pr": exps[b] / gmean,
            "place": o["place_sum"] / n,
            **{k: (o["col_sum"][k] / o["col_n"][k] if o["col_n"][k] else None)
               for k, _, _ in COLS},
        }
    return res


def bootstrap(games, bots, maps, by_map, B, seed):
    """按地图重采样 B 次，返回每次的 one_pass 结果列表。"""
    rng = random.Random(seed)
    reps = []
    for _ in range(B):
        pick = []
        for _ in range(len(maps)):
            pick.extend(by_map[maps[rng.randrange(len(maps))]])
        reps.append(one_pass(pick, bots))
    return reps


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 rating v2（docs/rating-design.md §3–4）")
    ap.add_argument("--replays", required=True, help="回放目录（judge4 --replay-dir 产物）")
    ap.add_argument("--boot", type=int, default=1000, help="地图块 bootstrap 次数；0=只出点估计")
    ap.add_argument("--rotating", action="store_true",
                    help="轮转阵容（pools round，如 210 组 4 人阵容）："
                         "跳过固定 panel 断言；其余口径不变（主分仍是 panel 内胜率，"
                         "阵容平衡时它对每个人无偏）")
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    replays = Path(args.replays)
    games, bots, skipped = load(replays, args.rotating)
    if not games:
        print("no usable replays under " + str(replays))
        return 1
    maps = sorted({g["map"] for g in games})
    by_map = {m: [g for g in games if g["map"] == m] for m in maps}
    n_bots = len(bots)
    point = one_pass(games, bots)
    order = sorted(bots, key=lambda b: (-point[b]["win"], point[b]["place"], b))

    reps = bootstrap(games, bots, maps, by_map, args.boot, args.seed) if args.boot else []

    def ci(b, key, q=0.05):
        if not reps:
            return None
        vals = [r[b][key] for r in reps if r[b][key] is not None]
        if not vals:
            return None
        return percentile(vals, q / 2), percentile(vals, 1 - q / 2)

    # 成对：Δwin% 的 CI 与 P(A>B)
    pairs = {}
    for i in range(n_bots):
        for j in range(i + 1, n_bots):
            a, b = bots[i], bots[j]
            d = point[a]["win"] - point[b]["win"]
            if reps:
                dl = sorted(r[a]["win"] - r[b]["win"] for r in reps)
                lo, hi = percentile(dl, 0.025), percentile(dl, 0.975)
                p_ab = sum(1 for x in dl if x > 0) / len(dl)
            else:
                lo = hi = p_ab = None
            pairs[(a, b)] = {"delta": d, "lo": lo, "hi": hi, "p_ab": p_ab,
                             "resolved": (None if lo is None else (lo > 0 or hi < 0))}

    # 部分序：相邻对已分辨 ⇒ 分段
    seg_of = {}
    seg = 1
    seg_of[order[0]] = 1
    for k in range(1, n_bots):
        a, b = order[k - 1], order[k]
        pr = pairs[(a, b)] if (a, b) in pairs else pairs[(b, a)]
        if pr["resolved"] is False:
            pass  # 同段
        else:
            seg += 1
        seg_of[order[k]] = seg

    def needed_n(pr):
        """未分辨相邻对还差多少局（2σ 口径）。"""
        if not reps or pr["lo"] is None:
            return None
        delta = abs(pr["delta"])
        width = pr["hi"] - pr["lo"]
        se = max(width / 3.92, 1e-9)
        if delta < 0.01:
            return "同档（Δ<1pp）"
        z = delta / se
        if z <= 0:
            return None
        n_req = int(round(point[order[-1]]["n"] * (2.0 / z) ** 2))
        return "需≈%d局" % n_req

    # ---------- 输出 ----------
    lines = [
        "# P01 Rating v2（docs/rating-design.md §3–4）",
        "",
        f"- replays: `{replays}`",
        f"- {n_bots} bots / {len(maps)} maps / {sum(point[b]['n'] for b in bots)} bot-局"
        f"（每人 {min(point[b]['n'] for b in bots)}~{max(point[b]['n'] for b in bots)} 局；"
        f"排除 forfeit {skipped['forfeit']} / draw {skipped['draw']}"
        f" / 异常 {skipped['malformed']}）",
        f"- 主分 = **panel 内胜率**（固定阵容下即多项 MLE，docs/rating-design.md §2.5：不拟合、不拆对、不做 BT）",
        "- 过程列只作画像，**不参与排名算术**（docs/swing-design.md §2.4 验证门：composite 不合成）",
        f"- CI/判决：按地图 cluster bootstrap {args.boot} 次"
        + ("（未跑，无判决）" if not reps else "（单位=地图，同图 24 排列强相关）"),
        "",
        "| 段 | bot | rating | PR | win% (95% CI) | W/G | 相邻对判决 | dealt/局 | 命中率 | 资源效率 | 灭绝轮 | 破坏/局 |",
        "|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for k, b in enumerate(order):
        p = point[b]
        lo_hi = ci(b, "win")
        if lo_hi:
            wci = f"{100*p['win']:.1f} [{100*lo_hi[0]:.1f}, {100*lo_hi[1]:.1f}]"
        else:
            wci = f"{100*p['win']:.1f}"
        verdict = ""
        if k > 0:
            a = order[k - 1]
            pr = pairs[(a, b)] if (a, b) in pairs else pairs[(b, a)]
            if pr["resolved"] is None:
                verdict = "—"
            elif pr["resolved"]:
                verdict = "与 %s 已分辨 (Δ=%.1fpp)" % (a, 100 * abs(pr["delta"]))
            else:
                extra = needed_n(pr)
                verdict = "与 %s 未分辨 (Δ=%.1fpp%s)" % (
                    a, 100 * abs(pr["delta"]), "，" + extra if extra else "")
        cols = []
        for key, _hdr, spec in COLS:
            v = p[key]
            cl = ci(b, key)
            cols.append(fmt(v, spec) + (f" [{fmt(cl[0], spec)}, {fmt(cl[1], spec)}]"
                                        if cl else ""))
        lines.append("| %d | %s | %.2f | %.2f | %s | %d/%d | %s | %s |" % (
            seg_of[b], b, p["rating"], p["pr"], wci, p["wins"], p["n"], verdict,
            " | ".join(cols)))
    lines += [
        "",
        "> 「段」相同 = 该相邻对的胜率差在本样本下**不可分辨**（不是「相等」）；",
        "> 未分辨对标注了 2σ 口径所需局数；同档（Δ<1pp）给多少局也分不开，别硬排。",
        "> 灭绝轮/命中率/资源效率/破坏是描述量：解释风格用，不进名次。",
        "> **rating = 胜率 ÷ 25%**（1.00 = 场平均；就是排序分本身的可读刻度）。",
        "> **PR = 表现分**（exp(0.35×Σw·局内 z) 按场几何均值归一，1.00 = 场平均）：",
        ">   只用 dmg_out_share(+) 与 dmg_in(−) 两个验证门稳定组件；是画像，不进排名。",
    ]
    if not reps:
        lines.append("> 未跑 bootstrap：只有点估计，无置信区间与分辨判决。")

    payload = {
        "tool": "p01-rating", "rating_version": 2,
        "replays": str(replays),
        "bots": bots, "maps": maps,
        "n_games_total": sum(point[b]["n"] for b in bots) // n_bots,
        "skipped": skipped, "boot": args.boot, "seed": args.seed,
        "score": "win_rate (panel multinomial MLE; no fitting, no pairwise decomposition)",
        "order": order,
        "segments": [{"segment": seg_of[b], "bot": b} for b in order],
        "rows": [{
            "bot": b, "segment": seg_of[b], "games": point[b]["n"],
            "wins": point[b]["wins"], "win": round(point[b]["win"], 5),
            "win_ci": [round(x, 5) for x in ci(b, "win")] if ci(b, "win") else None,
            "rating": round(point[b]["rating"], 3),
            "rating_ci": ([round(x, 3) for x in ci(b, "rating")]
                          if ci(b, "rating") else None),
            "pr": round(point[b]["pr"], 3),
            "pr_ci": ([round(x, 3) for x in ci(b, "pr")] if ci(b, "pr") else None),
            "place": round(point[b]["place"], 3),
            "place_ci": [round(x, 3) for x in ci(b, "place")] if reps else None,
            **{k: (round(point[b][k], 4) if point[b][k] is not None else None)
               for k, _, _ in COLS},
            **{k + "_ci": ([round(x, 4) for x in ci(b, k)] if ci(b, k) else None)
               for k, _, _ in COLS},
        } for b in order],
        "pairs": [{"a": a, "b": b, "delta": round(pairs[(a, b)]["delta"], 5),
                   "ci": ([round(pairs[(a, b)]["lo"], 5), round(pairs[(a, b)]["hi"], 5)]
                          if pairs[(a, b)]["lo"] is not None else None),
                   "p_ab": (round(pairs[(a, b)]["p_ab"], 4)
                            if pairs[(a, b)]["p_ab"] is not None else None),
                   "resolved": pairs[(a, b)]["resolved"]}
                  for a, b in sorted(pairs)],
        "note": "win% is the rating; process columns are descriptive only; "
                "CI by map-cluster bootstrap; segments = statistically unresolved adjacency.",
    }

    md = "\n".join(lines) + "\n"
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

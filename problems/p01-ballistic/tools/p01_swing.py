#!/usr/bin/env python3
"""P01 操作质量组件提取器（operation_swing v1，docs/swing-design.md §2.2 的落地）。

WHY: 排名层（panel 内胜率）只回答「谁强」；这一层回答「打得好不好」。
每个 bot 每局从回放事件流提取一组操作质量组件（输出/承伤/击杀/助攻/
命中率/资源效率/出手量/bad/节奏），全部局内可观测量，v1 只出组件列、
不出 composite（§2.4 的验证门关着，权重不手拍）。

三条实测教训直接约束本文件的写法（都不是风格问题，是正确性问题）：

1. 座位→bot 归属必须用 seat_perm 反查：judge4 把 header['bots'] 写成
   canonical 顺序、seat_perm[k] 是 canonical bot k 的座位，所以「坐在
   seat s 的 bot」是 bots[perm.index(s)]。把 header['bots'] 当按座位排列
   的列表，会在 23/24 的非恒等排列局里整体贴错 bot 和座位（docs/rating-design.md §1
   的 bug）。本文件照抄 p01_eventsignal.seat_bots 的修法，不要改。
2. bad 必须拆「存活期 / 全灭后」两列（docs/swing-design.md §1.3）：这批 bot 存活期
   bad 恒 0，全灭后的 N WAIT（点名死虫）被引擎记 bad 占了 bad 列的
   100%——混在一起的 bad/局只是灭绝时长的代理，不是操作质量。
   bad_alive 是给未来 bot 留的探测器：一旦有 bot 活着乱交指令它会立刻响。
3. 回放不存 turn-0 状态（map 记录只有初始 grid）：首回合的增量相对
   初值算（每虫 100 HP / 2 手雷 / 2 钻头 / 全部 alive，RULES §2），
   否则首回合的掉血、首血和资源消耗会被整体丢掉。dealt 初值取 0 与
   p01_eventsignal 的 (prev_dealt or 0) 口径一致。

地形破坏组件（blocks_*，v1）的口径由下面 4~6 三条数据事实直接决定
（编号接着上面三条实测教训连续排；与任务规格的两处出入如实记录在
这里，不按估计数字猜）：

4. 破坏 = 泥土消失：逐格严格统计 prev='#' 且 cur='.'。RULES §8.5 规定
   爆炸对地形只有 '#'→'.' 一种写法（基岩 X、水 '~' 都不变），实测相邻
   turn 的 grid diff 也确实只有 '#'→'.' 一种迁移（无 '.'→'#'，即地形
   不会凭空出现），所以这个计数没有反向噪声。
5. map 记录的 grid 是原始地图文件（judge4.py:520 直接
   load_grid(map_path)）：出生点以 '0'-'3' 标注、水以 '~' 标注，而引擎
   GRID 查询只回 '#'/X/'.'（judge4.py:266）。所以 t=1 拿 map.grid 当
   基线时，「水 184 格 '~'→'.'」和「出生标记 '0'-'3'→'.'」这两个格式差
   都不是 '#'→'.'，天然不进计数；规格要求的「减去出生点格」仍按机制
   实现为防御性剔除（用 initial 虫坐标），只是实测减去恒为 0。
6. 出生点格实测是 12 个（initial.worms 每虫占一格 [x,y]，每席 3 虫，
   RULES §66「每 active 席 3 个出生点」），任务规格写的「4 个」是按
   4 席估的；实现按机制取 initial 虫坐标全集剔除，不硬编码格数。

v1 已知近似（docs/swing-design.md §2.5，接受、如实报告）：
- 破坏归因用 sole-fire 规则：某回合 shots 非空且只有一席开火 ⇒ 该回合
  全部破坏（含该席自己钻头挖的）归该席；多席同回合开火不拆分（v1 不做
  按弹分摊）、无 shot 的回合（纯挖土/走位掉土）也不归属任何席，两者都
  计入 unattributed，同时报告覆盖率 = 被归因破坏 / 总破坏。
- 击杀/助攻按「死亡回合的 dealt 增量席位集合」归因，多攻击者按增量
  比例分摊；无法归因的伤害（水伤/摔伤等无 dealt 增量的掉血）丢弃，
  但统计归因覆盖率 = 可归因伤害 / 总承伤。
- 弹着同回合结算，所以「造成伤害的回合」就是「命中的出手回合」；
  dealt 增量 >0 但该席当回合无 shot 记录 = 异常路径（钻头贴身之类），
  计数并在非零时打印警告。

与 p01_eventsignal 读数的一个**已知且刻意保留**的口径差：eventsignal 的
`if prev is not None` 把首回合记录的命中/首血整体跳过（那个 guard 本是为
alive 翻转判定写的，dealt/hp 差分被捎带跳过）。强 bot 在 89~90% 的局
第 1 回合就命中，所以它的 hit炉/首血轮读数系统性偏低。本工具按引擎语义
把首回合算进 hit_rate/first_blood/dmg_in（dealt 是从 0 起的累计值、首回合
弹着同回合结算，物理上这是对的），同时在附注里打印「复刻 eventsignal
跳过首回合口径」的交叉验证读数（hit_rounds_no1 / first_blood_no1），
两边可机械对照 corrected_outputs.txt。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
SEATS, WPS, ROUNDS = 4, 3, 40
WORM_HP, INIT_GRENADES, INIT_DRILLS = 100, 2, 2  # RULES §2；回放里无 turn-0，见模块 docstring


def seat_bots(header):
    """Per-seat bot names, correctly attributed.

    judge4.py:532 writes header['bots'] in CANONICAL order (the --bots order,
    constant across a run) and seat_perm[k] = seat of canonical bot k
    (judge4.py:473: winner_bot = perm.index(winner_seat)). So the bot AT
    seat s is bots[perm.index(s)]. Treating header['bots'] as a per-seat
    list silently relabels bots with seats for every non-identity perm
    (23/24 of a full rotation run) -- the pre-2026-11-10 version of this
    tool did exactly that (docs/rating-design.md §1).
    """
    bots = (header or {}).get("bots")
    perm = (header or {}).get("seat_perm")
    if not bots:
        return None
    if not perm or sorted(perm) != list(range(len(bots))):
        return list(bots)  # 老回放没有 seat_perm：保持旧行为
    return [bots[perm.index(s)] for s in range(len(bots))]


def read_replay(p):
    """读全部记录；map 记录单独带出（t=1 破坏基线 = 原始地图文件 grid）。"""
    header = result = map_rec = None
    turns = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            t = rec.get("type")
            if t == "header":
                header = rec
            elif t == "map":
                if map_rec is None:
                    map_rec = rec  # 单地图局只有一条；多条取第一条
            elif t == "result":
                result = rec
            elif t == "turn":
                turns.append(rec)
    return header, turns, result, map_rec


def spawn_cells_of(map_rec):
    """出生点格集合 {(x,y)}：取 map.initial.worms 的逐虫坐标。

    WHY 用虫坐标而不是硬编码：这批地图每虫占一格出生点（每席 3 格、
    共 12 格），原始地图文件里以 '0'-'3' 标注；引擎把出生格渲染成
    '.'（虫不画进地形 grid）。规格里的「4 个」是估计，数据是 12 个，
    按机制取 initial 虫坐标全集，不按估计数字。
    """
    if not map_rec:
        return frozenset()
    ws = (map_rec.get("initial") or {}).get("worms") or []
    return frozenset((w[0], w[1]) for w in ws
                     if isinstance(w, (list, tuple)) and len(w) >= 2)


_NO_EXCLUDE = frozenset()  # t>=2 相邻 diff 无需出生点剔除的公共空集


def destroyed_cells(prev_grid, cur_grid, exclude=_NO_EXCLUDE):
    """逐格统计 prev='#' 且 cur='.' 的格数（精确破坏量，RULES §8.5）。

    grid[y][x]：行=y、列=x，与 judge4 worm 坐标 (x,y) 一致。exclude 里的
    (x,y) 即使是 '#'→'.' 也剔除（t=1 出生点防御性剔除；实测出生格在
    map.grid 里是 '0'-'3'，永远不进 '#→.' 计数，故减去恒为 0）。
    尺寸不齐返回 None（宁可不计也不错计——破坏计数必须精确）。
    """
    if not prev_grid or not cur_grid or len(prev_grid) != len(cur_grid):
        return None
    n = 0
    for y, (rp, rc) in enumerate(zip(prev_grid, cur_grid)):
        if len(rp) != len(rc):
            return None
        if "#" not in rp:
            continue  # 整行无泥土 ⇒ 无 '#'→'.'，快跳天空行
        if exclude:
            n += sum(1 for x, (a, b) in enumerate(zip(rp, rc))
                     if a == "#" and b == "." and (x, y) not in exclude)
        else:
            n += sum(1 for a, b in zip(rp, rc) if a == "#" and b == ".")
    return n


def is_forfeit(result):
    """ffa_rating.py 的口径：reason 为 FORFEIT 或指名了 forfeit 席即弃赛局。"""
    fz = result.get("forfeit", -1)
    return result.get("reason") == "FORFEIT" or (fz is not None and int(fz) >= 0)


def game_components(turns, map_rec=None):
    """逐回合扫一遍事件流：每席组件 + 全局归因统计。

    增量约定：turn 记录是「本回合指令结算后」的状态；prev_* 初值取
    开局初值（dealt=0、每虫 WORM_HP/2/2/alive），故首回合的掉血/首血/
    资源消耗都计入，且与 eventsignal 的 dealt 口径对齐。
    击杀归因（v1）：某虫 alive 1→0 ⇒ 死亡事件；该回合 dealt 增量 >0 的
    席位构成攻击者集合，唯一 ⇒ 1.0 全给它，多个 ⇒ 按增量比例分摊 1.0。
    助攻（v1）：逐回合把每虫的掉血按同样的攻击者集合归因累计；虫死亡
    时，非击杀席在该虫身上的归因伤害计入各自 assist（击杀席的那份只
    进 kills 不重复进 assists）。
    地形破坏（blocks_*，v1）：(t-1, t) 两条记录的 grid 逐格 diff 恰好
    覆盖 t 回合的移动/钻头/爆炸效果，与 t 回合 shots 的开火席同源，所以
    sole-fire 归因可直接挂在 shot_seats 上。t=1 的基线是 map 记录的
    原始地图（出生点按 initial 虫坐标剔除）；turn 缺 grid 时断链重置
    （跨两回合的 diff 归因必错，宁可丢一格数据）。整局无 grid ⇒
    blocks_out/blocks_total 输出 None（0 = 实测为零，None = 不可观测）。
    """
    comp = {s: {
        "dmg_out": 0, "dmg_in": 0.0, "kills": 0.0, "assists": 0.0,
        "fire_turns": 0, "hit_fire_turns": 0, "hit_rounds": 0,
        "hit_rounds_no1": 0, "first_blood_no1": None,  # 复刻 eventsignal 口径（跳过首回合记录）
        "bad_alive": 0, "bad_dead": 0, "res_used": 0,
        "shots": 0, "shots_ROCKET": 0, "shots_GRENADE": 0, "shots_other": 0,
        "ext_time": None, "first_blood": None,
        "blocks_out": 0, "blocks_total": 0,
    } for s in range(SEATS)}
    tot = {"dmg_taken": 0.0, "dmg_attrib": 0.0, "dealt_no_shot": 0,
           "unattrib_kills": 0, "self_kill_events": 0, "self_assist_events": 0,
           "blocks_total": 0, "blocks_attrib": 0,
           "blocks_unattrib_multi": 0, "blocks_unattrib_dig": 0,
           "dig_turns": 0, "sole_fire_turns": 0,
           "blocks_avail_turns": 0, "blocks_missing_turns": 0, "blocks_dim_bad": 0}
    # 地形破坏基线：t=1 相对 map 记录的原始地图（见模块 docstring 第 5 条），
    # t>=2 相邻 turn diff。旧回放 turn 无 grid ⇒ prev_grid 断链成 None，
    # avail_turns 停在 0，blocks 组件整体输出 None。
    map_grid = (map_rec or {}).get("grid")
    spawn_xy = spawn_cells_of(map_rec)
    prev_grid = map_grid
    blocks_per_turn = []
    # attrib[a][wi]：席位 a 在虫 wi 身上的归因伤害累计
    attrib = [[0.0] * (SEATS * WPS) for _ in range(SEATS)]
    prev_hp = [WORM_HP * WPS] * SEATS
    prev_dealt = [0] * SEATS
    prev_worm_hp = [WORM_HP] * (SEATS * WPS)
    prev_alive = [1] * (SEATS * WPS)
    prev_res = [(INIT_GRENADES, INIT_DRILLS)] * (SEATS * WPS)
    first_rec = True

    for rec in turns:
        hp = rec.get("hp") or [0] * SEATS
        dealt = rec.get("dealt") or [0] * SEATS
        bad = rec.get("bad") or [0] * SEATS
        worms = rec.get("worms") or []
        if len(worms) != SEATS * WPS:
            return None, tot  # 结构异常（非 4×3 活虫局），调用方跳过
        t = rec.get("t")
        inc = [dealt[s] - prev_dealt[s] for s in range(SEATS)]
        attackers = [s for s in range(SEATS) if inc[s] > 0]
        inc_sum = sum(inc[s] for s in attackers)

        shot_seats = set()
        for sh in rec.get("shots") or []:
            s = sh.get("seat")
            if s is None or s not in comp:
                continue
            sc = comp[s]
            sc["shots"] += 1
            kind = sh.get("kind")
            if kind == "ROCKET":
                sc["shots_ROCKET"] += 1
            elif kind == "GRENADE":
                sc["shots_GRENADE"] += 1
            else:
                sc["shots_other"] += 1
            shot_seats.add(s)

        # --- 地形破坏：本回合相对上一条记录的 '#'→'.' 计数 + sole-fire 归因 ---
        grid = rec.get("grid")
        destroyed = None
        if grid is None:
            # 缺一条 grid，下一对的 diff 会跨两回合、归因必错 ⇒ 断链重置
            tot["blocks_missing_turns"] += 1
            prev_grid = None
        elif prev_grid is None:
            # 没有基线（t=1 无 map 记录，或上一条缺 grid）：丢 t=1 增量，
            # 但从下一条起恢复相邻 diff
            tot["blocks_missing_turns"] += 1
            prev_grid = grid
        else:
            exclude = spawn_xy if prev_grid is map_grid else _NO_EXCLUDE
            destroyed = destroyed_cells(prev_grid, grid, exclude)
            if destroyed is None:
                tot["blocks_dim_bad"] += 1
            prev_grid = grid
        if destroyed is not None:
            tot["blocks_avail_turns"] += 1
            if destroyed > 0:
                tot["blocks_total"] += destroyed
                if len(shot_seats) == 1:
                    # sole-fire：该回合全部破坏（含开火席自己的钻头挖土）归该席
                    a0 = next(iter(shot_seats))
                    comp[a0]["blocks_out"] += destroyed
                    tot["blocks_attrib"] += destroyed
                    tot["sole_fire_turns"] += 1
                    blocks_per_turn.append([t, destroyed, a0])
                else:
                    # 多席同回合开火（v1 不按弹拆分）或无 shot（纯挖土/走位）：
                    # 宁可进 unattributed 也不做无依据的归属
                    if shot_seats:
                        tot["blocks_unattrib_multi"] += destroyed
                    else:
                        tot["blocks_unattrib_dig"] += destroyed
                        tot["dig_turns"] += 1
                    blocks_per_turn.append([t, destroyed, None])

        for s in range(SEATS):
            drop = prev_hp[s] - hp[s]
            if drop > 0:
                comp[s]["dmg_in"] += drop
            if inc[s] > 0:
                comp[s]["hit_rounds"] += 1
                if comp[s]["first_blood"] is None:
                    comp[s]["first_blood"] = t
                if not first_rec:
                    # eventsignal 复刻口径：它跳过首回合记录（prev is None）
                    comp[s]["hit_rounds_no1"] += 1
                    if comp[s]["first_blood_no1"] is None:
                        comp[s]["first_blood_no1"] = t
                if s not in shot_seats:
                    tot["dealt_no_shot"] += 1
            if s in shot_seats:
                comp[s]["fire_turns"] += 1
                if inc[s] > 0:
                    comp[s]["hit_fire_turns"] += 1
            if bad[s]:
                # §1.3：bad 相对本记录的存活状态拆列。死后 WAIT 伪影全落
                # bad_dead；bad_alive 若非 0 说明有 bot 活着交非法指令。
                alive_now = any(worms[s * WPS + i][5] for i in range(WPS))
                comp[s]["bad_alive" if alive_now else "bad_dead"] += 1
            for i in range(WPS):
                w = worms[s * WPS + i]
                comp[s]["res_used"] += (max(0, prev_res[s * WPS + i][0] - w[3])
                                       + max(0, prev_res[s * WPS + i][1] - w[4]))

        for wi in range(SEATS * WPS):
            w = worms[wi]
            seat_of = wi // WPS
            drop = prev_worm_hp[wi] - w[2]
            if drop > 0:
                tot["dmg_taken"] += drop
                if attackers:
                    # 有攻击者 ⇒ 可归因；多攻击者按该回合 dealt 增量分摊
                    tot["dmg_attrib"] += drop
                    for a in attackers:
                        attrib[a][wi] += drop * inc[a] / inc_sum
                # 无攻击者（水伤/摔伤等）⇒ 丢弃，只进覆盖率分母
            if prev_alive[wi] and not w[5]:
                # 死亡事件：击杀分摊（v1 近似）
                if seat_of in attackers:
                    tot["self_kill_events"] += 1  # 「自己的席位在该席虫的死亡回合也有 dealt 增量」的归因伪影
                if len(attackers) == 1:
                    comp[attackers[0]]["kills"] += 1.0
                elif len(attackers) > 1:
                    for a in attackers:
                        comp[a]["kills"] += inc[a] / inc_sum
                else:
                    tot["unattrib_kills"] += 1
                # assist：非击杀席在该虫身上的归因伤害（击杀席只拿 kill）
                for a2 in range(SEATS):
                    if a2 not in attackers and attrib[a2][wi] > 0:
                        if a2 == seat_of:
                            tot["self_assist_events"] += 1  # 自己虫身上「归因给自己」的伪影
                        comp[a2]["assists"] += attrib[a2][wi]
                for a2 in range(SEATS):
                    attrib[a2][wi] = 0.0
                comp[seat_of]["ext_time"] = t  # turns 递增，最后死的一条虫胜出

        prev_hp = list(hp)
        prev_dealt = list(dealt)
        prev_worm_hp = [w[2] for w in worms]
        prev_alive = [w[5] for w in worms]
        prev_res = [(w[3], w[4]) for w in worms]
        first_rec = False

    for s in range(SEATS):
        c = comp[s]
        c["dmg_out"] = prev_dealt[s]  # 终态 dealt = 该席累计对敌伤害
        if c["ext_time"] is None:
            c["ext_time"] = ROUNDS + 1
        if c["first_blood"] is None:
            c["first_blood"] = ROUNDS + 1
        if c["first_blood_no1"] is None:
            c["first_blood_no1"] = ROUNDS + 1
        c["hit_rate"] = (c["hit_fire_turns"] / c["fire_turns"]) if c["fire_turns"] else None
        c["res_eff"] = c["dmg_out"] / max(c["res_used"], 1)
        c["dmg_out_share"] = (prev_dealt[s] / sum(prev_dealt)) if sum(prev_dealt) > 0 else 0.0

    if tot["blocks_avail_turns"]:
        # 有可观测回合：每席 comp 带上整局总破坏（供按 bot 聚合每局均值）
        for s in range(SEATS):
            comp[s]["blocks_total"] = tot["blocks_total"]
        tot["blocks_game"] = {
            "total": tot["blocks_total"], "attributed": tot["blocks_attrib"],
            "unattrib_multi": tot["blocks_unattrib_multi"],
            "unattrib_dig": tot["blocks_unattrib_dig"],
            "dig_turns": tot["dig_turns"], "sole_fire_turns": tot["sole_fire_turns"],
            "coverage": (tot["blocks_attrib"] / tot["blocks_total"]) if tot["blocks_total"] else None,
            "avail_turns": tot["blocks_avail_turns"],
            "missing_turns": tot["blocks_missing_turns"],
            "dim_mismatch_turns": tot["blocks_dim_bad"],
            "per_turn": blocks_per_turn,  # 仅破坏>0 的回合：[t, 格数, sole-fire 席或 None]
        }
    else:
        # 整局无 grid（旧格式回放）：blocks 组件不可观测，输出 None
        for s in range(SEATS):
            comp[s]["blocks_out"] = None
            comp[s]["blocks_total"] = None
        tot["blocks_game"] = None
    return comp, tot


def load(path: Path):
    """读全部回放，跳过 FORFEIT / draw / 结构异常局并计数（照 ffa_rating 口径）。"""
    games, names = [], None
    skipped = {"forfeit": 0, "draw": 0, "malformed": 0}
    for p in sorted(path.glob("*.jsonl")):
        header, turns, result, map_rec = read_replay(p)
        if not turns or not result or not header:
            skipped["malformed"] += 1
            continue
        bots = seat_bots(header)
        if not bots or len(bots) != SEATS:
            skipped["malformed"] += 1
            continue
        winner = result.get("winner", -1)
        if winner is None or int(winner) < 0:
            skipped["draw"] += 1
            continue
        if is_forfeit(result):
            skipped["forfeit"] += 1
            continue
        comp, tot = game_components(turns, map_rec)
        if comp is None:
            skipped["malformed"] += 1
            continue
        if names is None:
            names = list(bots)
        games.append({"file": p.name, "map": header.get("map"), "seed": header.get("seed"),
                      "winner_seat": int(winner), "seat_bots": bots,
                      "comp": comp, "tot": tot, "blocks": tot.get("blocks_game")})
    return games, names, skipped


def aggregate(games):
    """按 bot 聚合：各组件的每局均值。hit_rate 只对 fire_turns>0 的局求均值
    （fire_turns=0 的局 hit_rate=None），另给 pooled 口径做交叉验证。"""
    per = {}
    for g in games:
        for s in range(SEATS):
            b = g["seat_bots"][s]
            per.setdefault(b, []).append(g["comp"][s])
    agg = {}
    for b, rows in per.items():
        n = len(rows)
        def mean(key, filt=None):
            vs = [r[key] for r in rows if filt is None or filt(r)]
            return sum(vs) / len(vs) if vs else None
        hrs = [r for r in rows if r["hit_rate"] is not None]
        agg[b] = {
            "n_games": n,
            "dmg_out": mean("dmg_out"), "dmg_in": mean("dmg_in"),
            "kills": mean("kills"), "assists": mean("assists"),
            "hit_rate": (sum(r["hit_rate"] for r in hrs) / len(hrs)) if hrs else None,
            "hit_rate_pooled": ((sum(r["hit_fire_turns"] for r in rows)
                                 / sum(r["fire_turns"] for r in rows))
                                if sum(r["fire_turns"] for r in rows) else None),
            "hit_rate_none_games": n - len(hrs),
            "hit_rounds": mean("hit_rounds"),
            "hit_rounds_no1": mean("hit_rounds_no1"),
            "first_blood_no1": mean("first_blood_no1"),
            "hit_fire_turns": mean("hit_fire_turns"),
            "res_eff": mean("res_eff"), "res_used": mean("res_used"),
            "shots": mean("shots"), "shots_ROCKET": mean("shots_ROCKET"),
            "shots_GRENADE": mean("shots_GRENADE"), "shots_other": mean("shots_other"),
            "bad_alive": mean("bad_alive"), "bad_dead": mean("bad_dead"),
            "ext_time": mean("ext_time"), "first_blood": mean("first_blood"),
            "dmg_out_share": mean("dmg_out_share"),
            # 地形破坏：只对有 grid 的局求均值（None 局不计），blocks_n_games
            # 暴露覆盖局数——旧格式混入时各 bot 的覆盖面可能不等，不能只看均值
            "blocks_out": mean("blocks_out", lambda r: r["blocks_out"] is not None),
            "blocks_total": mean("blocks_total", lambda r: r["blocks_total"] is not None),
            "blocks_n_games": sum(1 for r in rows if r["blocks_out"] is not None),
        }
    return agg


def fmt(v, spec):
    return "-" if v is None else spec % v


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 操作质量组件提取器（docs/swing-design.md §2.2）")
    ap.add_argument("--replays", required=True, help="回放目录（每局一个 .jsonl）")
    ap.add_argument("--json", help="可选：把聚合 + 每局明细写入此 JSON")
    args = ap.parse_args()

    games, names, skipped = load(Path(args.replays))
    if not games:
        print("no usable replays")
        return 1
    agg = aggregate(games)
    taken = sum(g["tot"]["dmg_taken"] for g in games)
    attrib = sum(g["tot"]["dmg_attrib"] for g in games)
    no_shot = sum(g["tot"]["dealt_no_shot"] for g in games)
    unattr_kills = sum(g["tot"]["unattrib_kills"] for g in games)
    self_kills = sum(g["tot"]["self_kill_events"] for g in games)
    self_assists = sum(g["tot"]["self_assist_events"] for g in games)
    grid_games = [g for g in games if g["blocks"] is not None]
    nogrid_games = len(games) - len(grid_games)
    b_tot = sum(g["blocks"]["total"] for g in grid_games)
    b_attr = sum(g["blocks"]["attributed"] for g in grid_games)
    b_multi = sum(g["blocks"]["unattrib_multi"] for g in grid_games)
    b_dig = sum(g["blocks"]["unattrib_dig"] for g in grid_games)
    dig_turns = sum(g["blocks"]["dig_turns"] for g in grid_games)

    print("games=%d  bots=%s  (forfeit 跳过 %d，draw 跳过 %d，异常跳过 %d)"
          % (len(games), ", ".join(names), skipped["forfeit"], skipped["draw"], skipped["malformed"]))
    print("")
    print("=== 操作质量组件（每 bot 每局平均，docs/swing-design.md §2.2） ===")
    print("| bot | n | dmg_out | dmg_in | kills | assists | hit_rate | res_eff | shots(R/G/other) | bad_alive | bad_dead | ext_time | first_blood | dmg_out_share | blocks_out | blocks_total |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for b in sorted(agg):
        a = agg[b]
        print("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            b, a["n_games"],
            fmt(a["dmg_out"], "%.1f"), fmt(a["dmg_in"], "%.1f"),
            fmt(a["kills"], "%.2f"), fmt(a["assists"], "%.2f"),
            fmt(a["hit_rate"], "%.3f"), fmt(a["res_eff"], "%.1f"),
            "%s / %s / %s" % (fmt(a["shots_ROCKET"], "%.1f"),
                              fmt(a["shots_GRENADE"], "%.1f"),
                              fmt(a["shots_other"], "%.1f")),
            fmt(a["bad_alive"], "%.2f"), fmt(a["bad_dead"], "%.1f"),
            fmt(a["ext_time"], "%.1f"), fmt(a["first_blood"], "%.1f"),
            fmt(a["dmg_out_share"], "%.3f"),
            fmt(a["blocks_out"], "%.1f"), fmt(a["blocks_total"], "%.1f")))
    print("")
    print("附注：")
    print("- 归因覆盖率（可归因伤害/总承伤）= %.4f  （总承伤 %.0f，丢弃的无攻击者伤害 %.0f，水伤/摔伤等）"
          % (attrib / taken if taken else 0.0, taken, taken - attrib))
    print("- dealt 增量>0 但该席当回合无 shot 记录的回合数 = %d" % no_shot)
    if no_shot:
        print("  [warn] 上述非零：存在未记录的伤害路径（钻头贴身等），hit_rate 口径可能低估")
    print("- 无法归因的击杀（虫死亡回合无任何 dealt 增量）= %d 局次" % unattr_kills)
    print("- v1 归因伪影：死亡回合自己也有 dealt 增量的死亡事件 %d 次；「归因到自己虫身上」的 assist %d 次"
          % (self_kills, self_assists))
    if any(a["hit_rate_none_games"] for a in agg.values()):
        print("- hit_rate 为按局均值、fire_turns=0 的局不计（None）：%s"
              % ", ".join("%s:%d" % (b, agg[b]["hit_rate_none_games"])
                          for b in sorted(agg) if agg[b]["hit_rate_none_games"]))
    print("- pooled hit_rate（sum命中出手/sum出手）：%s"
          % ", ".join("%s=%s" % (b, fmt(agg[b]["hit_rate_pooled"], "%.3f")) for b in sorted(agg)))
    print("- 本表口径（含首回合）：dealt/局、hit_rounds、首血轮、灭绝轮：%s"
          % "; ".join("%s %.1f/%.2f/%.1f/%.1f" % (b, agg[b]["dmg_out"], agg[b]["hit_rounds"],
                                                  agg[b]["first_blood"], agg[b]["ext_time"])
                      for b in sorted(agg)))
    print("- 锚点复刻口径（跳过首回合记录 = p01_eventsignal 的实际行为）：hit炉/首血轮：%s"
          % "; ".join("%s %.2f/%.1f" % (b, agg[b]["hit_rounds_no1"], agg[b]["first_blood_no1"])
                      for b in sorted(agg)))
    if grid_games:
        print("- 地形破坏（v1 sole-fire，严格 '#'→'.' 逐格计数）：sole-fire 覆盖率 = %.4f（归因 %d / 总破坏 %d）"
              % ((b_attr / b_tot) if b_tot else 0.0, b_attr, b_tot))
        print("- 未归因破坏 %d 格 = 多席同回合开火 %d 格 + 无 shot 纯挖土 %d 格；纯挖土回合 %d（每局均值 %.2f）"
              % (b_multi + b_dig, b_multi, b_dig, dig_turns, dig_turns / len(grid_games)))
        if nogrid_games:
            print("  [note] blocks 只统计有 grid 的 %d/%d 局（旧格式回放不计入 blocks，也不影响其他组件）"
                  % (len(grid_games), len(games)))
        miss = sum(g["blocks"]["missing_turns"] for g in grid_games
                   if g["blocks"]["missing_turns"])
        miss_g = sum(1 for g in grid_games if g["blocks"]["missing_turns"])
        if miss:
            print("  [warn] 有 grid 的局里 %d 局存在缺 grid 回合（其相邻 diff 不计），共 %d 回合"
                  % (miss_g, miss))
        dimb = sum(g["blocks"]["dim_mismatch_turns"] for g in grid_games)
        if dimb:
            print("  [warn] grid 尺寸不齐的相邻对 %d 处（破坏计数要求精确，宁缺毋错）" % dimb)
    else:
        print("- 回放无 grid 字段（旧格式）：blocks_out / blocks_total 不可用（None）；"
              "其余组件不受影响，照常输出")
    bad_alive_total = sum(a["bad_alive"] or 0 for a in agg.values())
    if bad_alive_total:
        print("  [warn] bad_alive 非零（合计 %.2f）：与 docs/swing-design.md §1.3 的实测矛盾，先怀疑本工具的存活判定"
              % bad_alive_total)
    else:
        print("- bad_alive 合计 0（与 docs/swing-design.md §1.3 一致：存活期 bad 恒 0）")

    if args.json:
        blocks_cov = None
        if grid_games:
            blocks_cov = {"grid_games": len(grid_games), "nogrid_games": nogrid_games,
                          "blocks_total": b_tot, "blocks_attrib": b_attr,
                          "coverage": (b_attr / b_tot) if b_tot else None,
                          "unattrib_multi": b_multi, "unattrib_dig": b_dig,
                          "dig_turns": dig_turns,
                          "sole_fire_turns": sum(g["blocks"]["sole_fire_turns"]
                                                 for g in grid_games)}
        out = {"replays": str(args.replays), "n_games": len(games), "skipped": skipped,
               "coverage": {"dmg_taken": taken, "dmg_attrib": attrib,
                            "coverage": (attrib / taken) if taken else None,
                            "dealt_no_shot_turns": no_shot, "unattrib_kills": unattr_kills,
                            "self_kill_events": self_kills, "self_assist_events": self_assists,
                            "blocks": blocks_cov},
               "per_bot": agg,
               "per_game": [{"file": g["file"], "map": g["map"], "seed": g["seed"],
                             "winner_seat": g["winner_seat"], "seat_bots": g["seat_bots"],
                             "comp": g["comp"], "blocks": g["blocks"]} for g in games]}
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("\nJSON 明细已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

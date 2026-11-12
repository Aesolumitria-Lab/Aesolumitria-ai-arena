#!/usr/bin/env python3
"""P01 BALLISTIC · 规则测试。

这道题没有别的题那样的"格子状态"可断言：弹道是连续的，同样的角度在不同风下
落点差十几格。所以这里不比较"弹道中间经过哪些格"，只比较**可观测结果**：
谁掉血、哪块土被炸掉、谁落水死了、终局谁赢。

地图在临时目录里现造（96×54 平地），场景用 LOADX + SETWORM 精确摆位。
正式对局走 LOAD，那些命令会被拒绝——所以测试能力不会泄漏给选手。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))  # 本题专用工具（genmap_p01 等）
from harness import Reporter  # noqa: E402

_R = Reporter()
check = _R.check

GAME = "p01-ballistic"
W, H = 96, 54
FLOOR = 30          # y >= FLOOR 是泥土
FALL_SAFE = 3
WORM_HP = 100
CXX = ["g++", "-O2", "-std=c++17"] + (["-static"] if os.name == "nt" else [])


def _exe(stem: str) -> str:
    return stem + (".exe" if os.name == "nt" else "")


# ---------------------------------------------------------------- 地图

def flat_map(spawns=None, floor: int = FLOOR) -> str:
    """平地：上方空气、下方泥土，两列基岩边框。

    64 格宽的射击空间足够让 45° 弹道飞出去，不会像正式地图那样被土柱卡住。
    """
    if spawns is None:
        spawns = [(10, floor - 1), (20, floor - 1), (30, floor - 1),
                  (60, floor - 1), (70, floor - 1), (80, floor - 1)]
    rows = [["." for _ in range(W)] for _ in range(H)]
    for y in range(floor, H):
        for x in range(W):
            rows[y][x] = "#"
    for x in range(W):
        rows[0][x] = "X"
        rows[H - 1][x] = "X"
    for y in range(H):
        rows[y][0] = rows[y][1] = "X"
        rows[y][W - 1] = rows[y][W - 2] = "X"
    for i, (x, y) in enumerate(spawns):
        rows[y][x] = "0" if i < 3 else "1"
    return "\n".join("".join(r) for r in rows) + "\n"


class Engine:
    def __init__(self, exe: Path, tmp: Path):
        self.proc = subprocess.Popen(
            [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, encoding="utf-8")
        self.tmp = tmp
        self.n = 0

    def send(self, line: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def line(self) -> str:
        assert self.proc.stdout
        out = self.proc.stdout.readline()
        if not out:
            raise RuntimeError("engine 关闭了 stdout")
        return out.rstrip("\n")

    def cmd(self, line: str) -> str:
        self.send(line)
        return self.line()

    def load(self, cells: str | None = None, seed: int = 1) -> str:
        self.n += 1
        p = self.tmp / f"case-{self.n:03d}.txt"
        p.write_text(cells if cells else flat_map(), encoding="utf-8")
        r = self.cmd(f"LOADX {p.as_posix()} {seed}")
        if r.startswith("ERR"):
            raise RuntimeError(f"地图被拒：{r[4:]}")
        return r.split()[1]

    def grid(self) -> list[str]:
        self.send("GRID")
        rows = []
        while True:
            ln = self.line()
            if ln == "GEND":
                return rows
            rows.append(ln[2:])

    def step(self, order0: str, order1: str) -> dict:
        self.send(f"STEP {order0} | {order1}")
        return parse_state(self.line())

    def close(self) -> None:
        try:
            self.send("QUIT")
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def parse_state(line: str) -> dict:
    p = line.split()
    if p[0] == "ERR":
        return {"err": " ".join(p[1:])}
    d = {
        "round": int(p[1]), "wind": int(p[2]) - 32, "water": int(p[3]),
        "over": int(p[4]) == 1, "winner": int(p[5]),
        "hp": [int(p[6]), int(p[7])], "dealt": [int(p[8]), int(p[9])],
        "reason": p[10], "bad": [int(p[11]) == 1, int(p[12]) == 1], "hash": p[13],
        "worm": [],
    }
    for i in range(6):
        o = 14 + i * 6
        d["worm"].append({"x": int(p[o]), "y": int(p[o + 1]), "hp": int(p[o + 2]),
                          "grenades": int(p[o + 3]), "drills": int(p[o + 4]),
                          "alive": int(p[o + 5]) == 1})
    return d


# ---------------------------------------------------------------- 场景

def solo(e: Engine, places: dict[int, tuple[int, int]], wind: int = 0,
         water: int = 50, cells: str | None = None) -> None:
    """摆位并把其余虫杀掉，只留需要的场景。

    杀掉别的虫是为了让"谁掉血"没有歧义——爆炸范围内站着三四条虫时，
    断言 HP 会变成在测爆炸半径而不是在测被测规则。
    """
    e.load(cells)
    e.cmd(f"SETWIND {wind}")
    e.cmd(f"SETWATER {water}")
    for wid in range(6):
        if wid in places:
            r = e.cmd(f"SETWORM {wid} {places[wid][0]} {places[wid][1]}")
            assert r == "OK", (wid, places[wid], r)
        else:
            e.cmd(f"KILL {wid}")


def idleboth(_: int) -> str:
    return "WAIT"


def run_rules(exe: Path, tmp: Path) -> None:
    def fresh() -> Engine:
        return Engine(exe, tmp)

    # ---- 基础：摆位与状态
    e = fresh()
    solo(e, {0: (10, 29), 3: (30, 29)})
    e.send("INFO")
    line = e.line()
    check("SETWORM 摆位生效", line.split()[2:5] == ["0", "10", "29"], line)
    e.close()

    # ---- B01 直射命中：同一高度近距离，角度 0 力度 100
    e = fresh()
    solo(e, {0: (10, 29), 3: (16, 29)})
    st = e.step("0 F ROCKET 0 100", "3 WAIT")
    check("B01 火箭直射命中扣血", st["worm"][3]["hp"] < WORM_HP,
          f"hp={st['worm'][3]['hp']}")
    check("B01 命中记入 dealt", st["dealt"][0] > 0, f"dealt={st['dealt']}")
    e.close()

    # ---- B02 爆炸挖掘：命中点附近的土变空气
    e = fresh()
    solo(e, {0: (10, 29), 3: (16, 29)})
    before = e.grid()
    st = e.step("0 F ROCKET 0 100", "3 WAIT")
    after = e.grid()
    dug = sum(1 for y in range(H) for x in range(W)
              if before[y][x] == "#" and after[y][x] == ".")
    check("B02 爆炸挖掉泥土", dug > 0, f"dug={dug}")
    e.close()

    # ---- B03 基岩不可破坏：贴边打，边框必须完好
    e = fresh()
    solo(e, {0: (5, 29), 3: (40, 29)})
    before = e.grid()
    e.step("0 F ROCKET 180 100", "3 WAIT")
    after = e.grid()
    border_ok = all(after[y][0] == "X" and after[y][1] == "X" for y in range(H))
    check("B03 基岩不可破坏", border_ok, "边框被打穿")
    e.close()

    # ---- B04 爆炸半径衰减：同一次爆炸，中心满伤、边缘减伤
    # 火箭半径 2、基础 25：d=0 拿 25，d=2 拿 25*(3-2)/3=8。
    e = fresh()
    solo(e, {0: (10, 29), 3: (16, 29), 4: (18, 29)})
    st = e.step("0 F ROCKET 0 100", "3 WAIT")
    center = WORM_HP - st["worm"][3]["hp"]
    edge = WORM_HP - st["worm"][4]["hp"]
    check("B04 伤害随距离衰减", center == 25 and edge == 8, f"center={center} edge={edge}")
    e.close()

    # ---- B05 自伤：自己站在爆炸半径内也会掉血
    e = fresh()
    solo(e, {0: (10, 29), 3: (12, 29)})
    st = e.step("0 F ROCKET 0 100", "3 WAIT")
    check("B05 近距离自己也吃爆炸", st["worm"][0]["hp"] < WORM_HP,
          f"hp={st['worm'][0]['hp']}")
    e.close()

    # ---- B06 落水即死
    e = fresh()
    solo(e, {0: (10, 29), 3: (40, 29)}, water=30)
    st = e.step("0 WAIT", "3 WAIT")
    check("B06 水线覆盖即死", not st["worm"][0]["alive"] and not st["worm"][3]["alive"],
          f"alive={[w['alive'] for w in st['worm']]}")
    e.close()

    # ---- B07 坠落伤害：凭空放 5 格，落地扣血但不死
    e = fresh()
    solo(e, {0: (10, 25), 3: (40, 29)})
    st = e.step("0 WAIT", "3 WAIT")
    w = st["worm"][0]
    check("B07 坠落超过安全高度扣血", w["y"] == 29 and w["hp"] == WORM_HP - 10,
          f"y={w['y']} hp={w['hp']}")
    e.close()

    # ---- B08 安全高度内不扣血（2 格）
    e = fresh()
    solo(e, {0: (10, 27), 3: (40, 29)})
    st = e.step("0 WAIT", "3 WAIT")
    check("B08 安全坠落不掉血", st["worm"][0]["hp"] == WORM_HP,
          f"hp={st['worm'][0]['hp']}")
    e.close()

    # ---- B09 风影响弹道：同角度同力度，顺风与逆风落点不同
    def impact_x(wind: int) -> int:
        en = Engine(exe, tmp)
        solo(en, {0: (10, 29), 3: (90, 29)}, wind=wind)
        before = en.grid()
        en.step("0 F ROCKET 45 70", "3 WAIT")
        after = en.grid()
        xs = [x for y in range(H) for x in range(W)
              if before[y][x] == "#" and after[y][x] == "."]
        en.close()
        return max(xs) if xs else -1

    right, left = impact_x(10), impact_x(-10)
    check("B09 风改变落点", right != left, f"wind+10 x={right}  wind-10 x={left}")

    # ---- B10 移动：M 一步一格
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)})
    st = e.step("0 M R", "3 WAIT")
    check("B10 右移一格", st["worm"][0]["x"] == 21, f"x={st['worm'][0]['x']}")
    e.close()

    # ---- B10a 裁判给无效 actor 时不可越界，也不能阻断另一方移动
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)})
    st = e.step("-1 X", "3 M R")
    check("B10a 无效 actor 标记 bad 且对方仍移动", st["bad"] == [True, False]
          and st["worm"][3]["x"] == 61 and st["worm"][3]["alive"],
          f"bad={st['bad']} x={st['worm'][3]['x']} alive={st['worm'][3]['alive']}")
    e.close()

    # ---- B11 跳跃：J 跨 2 格
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)})
    st = e.step("0 J R", "3 WAIT")
    check("B11 跳跃跨 2 格", st["worm"][0]["x"] == 22, f"x={st['worm'][0]['x']}")
    e.close()

    # ---- B12 AP 上限：4 次移动超预算
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)})
    st = e.step("0 M R; M R; M R; M R", "3 WAIT")
    check("B12 超过 3 AP 判非法", st["bad"][0], f"bad={st['bad']}")
    check("B12 非法前已执行的移动保留", st["worm"][0]["x"] > 20,
          f"x={st['worm'][0]['x']}")
    e.close()

    # ---- B13 非法开火：角度越界
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)})
    st = e.step("0 F ROCKET 999 50", "3 WAIT")
    check("B13 角度越界被拒", st["bad"][0], f"bad={st['bad']}")
    e.close()

    e = fresh()
    solo(e, {0: (10, 29), 3: (16, 29)})
    st = e.step("0 F ROCKET 0 100; M R", "3 WAIT")
    check("B13 开火后的多余动作忽略但记非法",
          st["bad"][0] and st["worm"][3]["hp"] < WORM_HP,
          f"bad={st['bad']} hp={st['worm'][3]['hp']}")
    e.close()

    # ---- B14 手雷数量：2 发，第 3 发无效
    e = fresh()
    solo(e, {0: (10, 29), 3: (40, 29)})
    s1 = e.step("0 F GRENADE 45 40", "3 WAIT")
    s2 = e.step("0 F GRENADE 45 40", "3 WAIT")
    s3 = e.step("0 F GRENADE 45 40", "3 WAIT")
    check("B14 手雷用尽后开火非法",
          s1["worm"][0]["grenades"] == 1 and s2["worm"][0]["grenades"] == 0 and s3["bad"][0],
          f"g={[s1['worm'][0]['grenades'], s2['worm'][0]['grenades']]} bad={s3['bad']}")
    e.close()

    # ---- B15 钻地弹：水平钻进土里（无重力、无风、固定 0.25 格/tick）
    e = fresh()
    e.n += 1
    wall = tmp / f"case-{e.n:03d}.txt"
    rows = [list(r) for r in flat_map().splitlines()]
    for y in range(10, FLOOR):
        rows[y][40] = "#"
    wall.write_text("\n".join("".join(r) for r in rows) + "\n", encoding="utf-8")
    e.send(f"LOADX {wall.as_posix()} 1")
    e.line()
    e.cmd("SETWIND 0")
    e.cmd("SETWORM 0 30 29")
    for wid in (1, 2, 3, 4, 5):
        e.cmd(f"KILL {wid}")
    before = e.grid()
    e.step("0 F DRILL 0 50", "3 WAIT")
    after = e.grid()
    dug = sum(1 for y in range(H) for x in range(W)
              if before[y][x] == "#" and after[y][x] == ".")
    check("B15 钻地弹钻进墙里", dug >= 2, f"dug={dug}")
    e.close()

    # ---- B16 三个全灭即结束
    e = fresh()
    e.load()
    e.cmd("KILL 3")
    e.cmd("KILL 4")
    st = e.cmd("KILL 5") and None
    e.send("STEP 0 WAIT | 5 WAIT")
    st = parse_state(e.line())
    check("B16 一方全灭立即结束",
          st["over"] and st["winner"] == 0 and st["reason"] == "ELIMINATION",
          f"over={st['over']} winner={st['winner']} reason={st['reason']}")
    e.close()

    # ---- B17 40 轮用尽按 HP
    e = fresh()
    solo(e, {0: (10, 29), 3: (80, 29)})
    st = None
    for _ in range(40):
        st = e.step("0 WAIT", "3 WAIT")
        if st["over"]:
            break
    check("B17 40 轮用尽结束", st["over"] and st["reason"] == "DRAW",
          f"round={st['round']} reason={st['reason']}")
    check("B17 同血量同伤害同存活直接平局",
          st["hp"] == [WORM_HP, WORM_HP] and st["winner"] == -1,
          f"hp={st['hp']} winner={st['winner']}")
    e.close()

    # ---- B18 水位从第 30 轮开始上涨
    e = fresh()
    solo(e, {0: (10, 29), 3: (60, 29)}, water=50)
    w29 = w30 = None
    for r in range(30):
        st = e.step("0 WAIT", "3 WAIT")
        if r + 1 == 29:
            w29 = st["water"]
        if r + 1 == 30:
            w30 = st["water"]
    check("B18 第 30 轮开始涨水", w30 == w29 - 1, f"r29={w29} r30={w30}")
    e.close()

    # ---- B19 确定性：两次独立进程跑同一序列，hash 必须一致
    def replay_hashes() -> list[str]:
        en = Engine(exe, tmp)
        solo(en, {0: (10, 29), 3: (50, 29)})
        out = []
        for _ in range(6):
            out.append(en.step("0 F ROCKET 60 70", "3 WAIT")["hash"])
            out.append(en.step("0 M E; F ROCKET 30 40", "3 M L")["hash"])
        en.close()
        return out

    h1, h2 = replay_hashes(), replay_hashes()
    check("B19 同输入两次 hash 一致", h1 == h2, f"{h1[:2]} vs {h2[:2]}")
    check("B19 hash 随状态变化", len(set(h1)) > 1, f"unique={len(set(h1))}")

    # ---- B20 hash 覆盖风：只改风，hash 必变
    e = fresh()
    solo(e, {0: (10, 29), 3: (50, 29)}, wind=0)
    e.send("STEP 0 WAIT | 3 WAIT")
    h0 = parse_state(e.line())["hash"]
    e.close()
    e = fresh()
    solo(e, {0: (10, 29), 3: (50, 29)}, wind=5)
    e.send("STEP 0 WAIT | 3 WAIT")
    h5 = parse_state(e.line())["hash"]
    e.close()
    check("B20 风进入 state_hash", h0 != h5, f"{h0} vs {h5}")

    # ---- B21 手雷会滚动/延迟：发射当轮不一定立即结算完
    e = fresh()
    solo(e, {0: (10, 29), 3: (40, 29)})
    g0 = e.grid()
    e.step("0 F GRENADE 30 30", "3 WAIT")
    g1 = e.grid()
    check("B21 手雷飞行轨迹与火箭不同（低角度低力度落地近）",
          g0 != g1, "地形完全没变")
    e.close()

    # ---- B22 死掉的虫不能再行动
    e = fresh()
    solo(e, {0: (10, 29), 3: (40, 29)})
    e.cmd("KILL 0")
    st = e.cmd("STEP 0 WAIT | 3 WAIT")
    check("B22 死亡单位下令被忽略", not st.startswith("ERR"), st[:60])
    e.close()

    # ---- B23 同时移动：目标虫成功让出原格后，双方都能移动
    e = fresh()
    solo(e, {0: (20, 29), 3: (21, 29)})
    st = e.step("0 M R", "3 M R")
    check("B23 同时移动允许让位",
          (st["worm"][0]["x"], st["worm"][3]["x"]) == (21, 22)
          and not any(st["bad"]),
          f"x={st['worm'][0]['x']},{st['worm'][3]['x']} bad={st['bad']}")
    e.close()

    # ---- B24 同时移动：双方争同一格 / 互换，双方本次都失败，但【不记 fault】
    #      （v2.2.0：合法声明被抢格子不是违规；旧版 all(bad) 会把密集移动判负）
    e = fresh()
    solo(e, {0: (20, 29), 3: (22, 29)})
    st = e.step("0 M R", "3 M L")
    check("B24 同时争格双方留在原位且不记 fault",
          (st["worm"][0]["x"], st["worm"][3]["x"]) == (20, 22)
          and not any(st["bad"]),
          f"x={st['worm'][0]['x']},{st['worm'][3]['x']} bad={st['bad']}")
    e.close()

    e = fresh()
    solo(e, {0: (20, 29), 3: (21, 29)})
    st = e.step("0 M R", "3 M L")
    check("B24 同时互换位置双方留在原位且不记 fault",
          (st["worm"][0]["x"], st["worm"][3]["x"]) == (20, 21)
          and not any(st["bad"]),
          f"x={st['worm'][0]['x']},{st['worm'][3]['x']} bad={st['bad']}")
    e.close()

    # ---- B24a 真非法仍然是 fault：把邻格填成基岩，移动必须失败并记 fault
    e = fresh()
    rows = [list(r) for r in flat_map().splitlines()]
    for y in range(28, 31):
        rows[y][21] = "X"
    cells = "\n".join("".join(r) for r in rows) + "\n"
    solo(e, {0: (20, 29), 3: (60, 29)}, cells=cells)
    st = e.step("0 M R", "3 WAIT")
    check("B24a 走不通的移动仍记 fault", st["bad"][0] and st["worm"][0]["x"] == 20,
          f"bad={st['bad']} x={st['worm'][0]['x']}")
    e.close()

    # ---- B25 每条 M/J 后立即落地：第一段落入水，第二段不能继续走
    rows = [list(r) for r in flat_map().splitlines()]
    for y in range(FLOOR, 50):
        rows[y][21] = "."
    cells = "\n".join("".join(r) for r in rows) + "\n"
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)}, cells=cells)
    st = e.step("0 M R; M R", "3 WAIT")
    check("B25 连续移动逐段落地并中止死者后续动作",
          not st["worm"][0]["alive"] and st["worm"][0]["x"] == 21,
          f"worm0={st['worm'][0]}")
    e.close()

    # ---- B26/B27 同 tick 爆炸按轮次奇偶换序，覆盖飞行命中与出生即爆
    def exchange(left: int, right: int, prewait: int) -> dict:
        rows = [list(r) for r in flat_map().splitlines()]
        rows[29][22] = "#"
        cells = "\n".join("".join(r) for r in rows) + "\n"
        en = Engine(exe, tmp)
        solo(en, {0: (left, 29), 3: (right, 29)}, cells=cells)
        en.cmd("SETHP 0 8")
        en.cmd("SETHP 3 8")
        for _ in range(prewait):
            en.step("0 WAIT", "3 WAIT")
        st = en.step("0 F ROCKET 0 100", "3 F ROCKET 180 100")
        en.close()
        return st

    odd = exchange(20, 23, 0)
    even = exchange(20, 23, 1)
    check("B26 飞行同 tick 爆炸奇数轮 seat0 先结算", odd["dealt"] == [16, 0],
          f"dealt={odd['dealt']}")
    check("B26 飞行同 tick 爆炸偶数轮 seat1 先结算", even["dealt"] == [0, 8],
          f"round={even['round']} dealt={even['dealt']} hp={even['hp']}")

    def immediate(prewait: int) -> dict:
        rows = [list(r) for r in flat_map().splitlines()]
        rows[29][21] = rows[29][23] = "#"
        cells = "\n".join("".join(r) for r in rows) + "\n"
        en = Engine(exe, tmp)
        solo(en, {0: (20, 29), 3: (22, 29)}, cells=cells)
        en.cmd("SETHP 0 8")
        en.cmd("SETHP 3 8")
        for _ in range(prewait):
            en.step("0 WAIT", "3 WAIT")
        st = en.step("0 F ROCKET 0 100", "3 F ROCKET 0 100")
        en.close()
        return st

    odd = immediate(0)
    even = immediate(1)
    check("B27 出生即爆奇数轮 seat0 先结算", odd["dealt"] == [8, 0],
          f"dealt={odd['dealt']}")
    check("B27 出生即爆偶数轮 seat1 先结算", even["dealt"] == [0, 8],
          f"round={even['round']} dealt={even['dealt']} hp={even['hp']}")

    # ---- B28 双方同时自灭且总伤害相同，不按座位或 seed 分胜负
    rows = [list(r) for r in flat_map().splitlines()]
    rows[29][21] = rows[29][61] = "#"
    cells = "\n".join("".join(r) for r in rows) + "\n"
    e = fresh()
    solo(e, {0: (20, 29), 3: (60, 29)}, cells=cells)
    e.cmd("SETHP 0 8")
    e.cmd("SETHP 3 8")
    st = e.step("0 F ROCKET 0 100", "3 F ROCKET 0 100")
    check("B28 双方同时自灭且同伤害直接平局",
          st["over"] and st["winner"] == -1 and st["reason"] == "DRAW"
          and st["dealt"] == [0, 0],
          f"winner={st['winner']} reason={st['reason']} dealt={st['dealt']}")
    e.close()

    # ---- B28a 双方同时全灭但伤害不同：按总伤害裁决，不是一律 DRAW
    e = fresh()
    solo(e, {0: (30, 29), 3: (32, 29)})
    e.cmd("SETHP 0 5")
    e.cmd("SETHP 3 5")
    st = e.step("0 F ROCKET 0 100", "3 WAIT")
    check("B28a 双方全灭按伤害裁决",
          st["over"] and st["winner"] == 0 and st["reason"] == "DAMAGE"
          and st["dealt"][0] > st["dealt"][1],
          f"winner={st['winner']} reason={st['reason']} dealt={st['dealt']} hp={st['hp']}")
    e.close()


def run_fuzz(exe: Path, tmp: Path, games: int) -> None:
    print("\n[fuzz]")
    if games <= 0:
        return
    rng = random.Random(20261102)
    crash = 0
    hashes: list[str] = []
    rounds_total = 0
    moves = ["M R", "M L", "M R; M R", "J R", "J L", "WAIT",
             "F ROCKET 45 60", "F ROCKET 120 80", "F GRENADE 30 40", "F DRILL 270 50"]
    for _ in range(games):
        e = None
        try:
            e = Engine(exe, tmp)
            e.load(seed=rng.randint(1, 10_000))
            for _ in range(15):
                a = f"0 {rng.choice(moves)}"
                b = f"3 {rng.choice(moves)}"
                st = e.step(a, b)
                rounds_total += 1
                if st.get("over"):
                    break
            hashes.append(st["hash"])
        except Exception as exc:  # noqa: BLE001
            crash += 1
            if crash == 1:
                check("fuzz 首次异常", False, repr(exc))
        finally:
            if e is not None:
                e.close()

    check(f"fuzz {games} 局无崩溃", crash == 0, f"crashes={crash}")
    check("fuzz 有足够样本", len(hashes) == games, f"got={len(hashes)}")
    check("fuzz 结果有变化", len(set(hashes)) > games // 2,
          f"unique={len(set(hashes))}/{games}")
    check("fuzz 压到了长对局", rounds_total > games * 8,
          f"avg rounds={rounds_total / max(1, games):.1f}")


def run_map_checks(exe: Path, tmp: Path, game: Path) -> None:
    import genmap_p01 as gen

    def load(rows: list[list[str]]) -> str:
        path = tmp / "strict-map.txt"
        path.write_text(gen.render(rows), encoding="utf-8")
        r = subprocess.run([str(exe)], input=f"LOAD {path.as_posix()} 0\nQUIT\n",
                           text=True, capture_output=True, timeout=10, check=True)
        return r.stdout.splitlines()[0]

    for path in sorted(game.glob("public/maps/*.txt")) + sorted(game.glob("private/maps_hidden/*.txt")):
        rows = [list(r) for r in path.read_text(encoding="utf-8").splitlines()]
        check(f"strict map {path.parent.name}/{path.name}", load(rows).startswith("OK "))
    base, err = gen.build(1)
    assert base is not None, err
    for family in ("terraces", "ridge", "basin"):
        rows, err = gen.build(7, family)
        check(f"generated {family} strict load", rows is not None and load(rows).startswith("OK "), err)
    spawns = [(x, y) for y, row in enumerate(base) for x, c in enumerate(row) if c in "01"]
    blocked = [r[:] for r in base]
    for x, y in spawns:
        blocked[y][x-1] = blocked[y][x+1] = "X"
    reply = load(blocked)
    check("strict rejects sealed spawn", "spawn has no side exit" in reply, reply)
    check("generator rejects sealed spawn", bool(gen.validate(blocked)))
    asymmetric = [r[:] for r in base]
    asymmetric[5][5] = "#"
    check("generator rejects asymmetric terrain", bool(gen.validate(asymmetric)))
    check("engine rejects asymmetric terrain", "not mirror symmetric" in load(asymmetric))
    pocket = [r[:] for r in base]
    for x, y in spawns:
        for yy in range(y-1, y+2):
            for xx in range(x-2, x+3):
                pocket[yy][xx] = "X"
        pocket[y][x-1] = pocket[y][x+1] = "."
        pocket[y][x] = base[y][x]
    check("strict rejects tiny cavity", "air region smaller than 20" in load(pocket))
    # Twenty-cell cavity is insufficient when both real escape actions fall into water.
    trap = [["X" if x < 2 or x >= W-2 or y < 2 or y >= H-2 else "#"
             for x in range(W)] for y in range(H)]
    for y in range(2, 22):
        for x in range(2, W-2): trap[y][x] = "."
    for x in (12, 24, 36, 59, 71, 83):
        for y in range(36, 50):
            for xx in range(x-2, x+3): trap[y][xx] = "."
        trap[49][x] = "#"
        trap[48][x] = "0" if x < W//2 else "1"
    reply = load(trap)
    check("strict rejects lethal escape", "no surviving move or jump" in reply, reply)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=None)
    ap.add_argument("--fuzz", type=int, default=0)
    ap.add_argument("--json")
    args = ap.parse_args()

    game = Path(args.game_dir) if args.game_dir else Path(__file__).resolve().parent.parent
    game = game.resolve()

    build = game / "build"
    build.mkdir(exist_ok=True)
    src = game / "public" / "engine" / "engine.cpp"
    exe = build / _exe("engine")
    r = subprocess.run([*CXX, "-o", str(exe), str(src)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stderr[:2000])
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="p01-validate-"))
    run_rules(exe, tmp)
    run_map_checks(exe, tmp, game)
    run_fuzz(exe, tmp, args.fuzz)

    print()
    print(_R.summary(GAME))
    _R.emit(args.json, GAME)
    return 0 if _R.ok else 1


if __name__ == "__main__":
    sys.exit(main())

# P01 BALLISTIC · 选手提示词

写一个 C++17 bot，在 96×54 的可破坏地形上，用三条虫在 4 席自由混战中活到最后。

你的对手是其他顶级模型/代理的提交，强度未知。公开基准 **TIDE** 是及格线，
挑战 bot **CHIMERA / PSEUDO / TYPHON** 是上限，不要求击败。

## 交什么

一个 C++17 程序：stdin 读协议，stdout 写动作。可以 `#include "arena4.hpp"`。

```text
g++ -O2 -std=c++17 -o mybot submission.cpp -I public
```

起点是 `public/template4.cpp`。`RULES.md` 第 13 节是**唯一权威协议**，
`public/arena4.hpp` 里有解析器和近似瞄准。

## 一局

- 4 席 FFA，每席 3 虫、每虫 100 HP；每轮每个 active 席同时激活 1 条活虫。
- 每虫每轮 3 AP：`M L/R` 1 AP，`J L/R` 2 AP，`F <武器> <角度> <力度>` 消耗剩余 AP 并结束。
- 40 轮结束，或只剩一个 active 席存活时提前结束；水从第 30 轮起每 2 轮涨 1 行，落水即死。
- 武器：`ROCKET`（无限）/ `GRENADE`（2 发，会弹）/ `DRILL`（2 发，向下挖）。
- 角度 `0–180`（0 向右，90 向上），力度 `0–100`。

## 协议（照 RULES 13.1 / 13.2，别照 2 席老协议）

每轮收到一个块：

```text
TURN <seat> <grid_rows>
STATE <round> <wind+32> <water> <over> <winner> <hp0>..<hp3> <dealt0>..<dealt3> <reason> <bad0>..<bad3> <hash> <12×(x y hp grenades drills alive)>
GRID <row>          ← 共 grid_rows 行（当前 54）
END
```

应答**一行**，第一个字段就是 act id，**没有轮次前缀**：

```text
4 F ROCKET 135 72
6 J L; M L; WAIT
```

- `act` 必须是 `seat*3 .. seat*3+2` 里的存活虫；`F` 必须最后；输出后 flush；调试走 stderr。
- 累计 3 次非法动作 / 超时 → 判负。**争格失败不算非法**（RULES 6.4）。

## 三个能立刻提高胜率的点

1. **精确模拟**：`arena4::aim_at` 只是近似。稳定赢要按引擎的 Q16.16 逐 tick 复刻弹道：
   子步进、风、手雷反弹/停驻、钻头挖掘、发射点偏移都要对齐。`public/oracle/` 里有
   只读的轨迹对账工具（吃地图+seed+双方动作，吐逐 tick 位置哈希与爆炸点），
   用它验证你的复刻，而不是靠眼力。
2. **看风和水**：风每 5 轮变一次（第 6/11/16/21/26/31/36 轮）。第 30 轮起涨水，
   把对手脚下的土炸掉往往比直接打血更值。
3. **4 席不是 1v1**：要选目标、避免被两家夹击、保留还手能力。只盯最近敌人通常会在中后期被集火。

## 自检

```text
python public/tools/selfcheck4.py --bot submission.cpp --baseline tide-normal --maps 2 --jobs 8
python public/tools/selfcheck4.py --bot submission.cpp --baseline tide-hard   --maps 4 --jobs 8
```

它让你和 3 个同名基准打 4 席局，给出胜率/平局/fault。`--baseline` 可选
`tide-easy` / `tide-normal` / `tide-hard` / `template` / `random` / `chimera` / `pseudo` / `typhon`。
`--jobs` 并发跑局（判超时只看 CPU 时间，不受多开影响）。

这是**自检**，不是官方评分（官方用隐藏图 + 挑战 bot）。公开目标是稳定击败 TIDE NORMAL，HARD 是拉伸。

## 时间预算

正式答题 **90 分钟墙钟**，由 harness 计时并在截止时终止会话；比赛跑局时间另计。

- 第 **75 分钟**时用户会发一条提醒；没收到就是还没到。
- 没 token 上限；可一直做，必要时用 goal 接续。
- 计时先由你自己负责：有 harness 计时用它；没有就开工记系统时间、每阶段查一次，90 分钟停。
  `timebudget.py` 只是可选便利工具，跑不了就别耗时间在它上面。
- 剩余 < 15 分钟：停止调参，留最后 10 分钟做最终编译 + 0 fault 自检 + 提交。
- 系统时间只能用于答题计时；提交的 bot 运行时不得依赖时间、不得读包外文件。

建议节奏：0–10 读规则 + 编 starter；10–35 精确模拟 + 协议；35–65 搜索与策略；
65–80 公开图自检；80–90 收尾。

## 不要做的事

- 不要联网、询问用户、读取包外目录或隐藏地图/seed；
- 不要在 stdout 打日志或输出非协议内容；
- 不要硬编码公开图坐标（正式评分地图不是公开图）；
- 不要超时、崩溃，或把同一份非法动作连发 3 次；
- 基准/挑战 bot 只给二进制：不要反编译或试图获取源码。

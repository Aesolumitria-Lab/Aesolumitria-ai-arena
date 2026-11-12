# TIDE · P01 基准 bot

> 本文是 TIDE 的设计说明。选手包内提供预编译的 EASY / NORMAL / HARD 三档
> （`public/baselines/bin/tide_*.exe`，Linux 在 `bin/linux/`）。**TIDE 的源码不公开**：
> 及格线就是稳定击败 TIDE-NORMAL（HARD 为拉伸目标），公开源码会让它失去尺子的作用。
> 同目录的 [`random4.cpp`](random4.cpp) 是最弱对照 bot 的完整源码。


TIDE 是一个“可解释的尺子”，不是冲榜 bot：每回合只做三件事——选一对“谁打谁”、在最多 9 个落点里挑一个、开一枪。单回合平均约 3 ms、最差约 5 ms（上限 50 ms）。

## 三档

| 档 | `TIDE_PROFILE` | 粗搜角度/力度 | 建模敌人 | 走位候选 | 输出抖动 |
|---|---|---|---|---|---|
| EASY | 0 | 12° / 20 | 1 | 1 | ±2° / ±3 力，确定性 |
| NORMAL | 1 | 5° / 8 | 2 | 8 | — |
| HARD | 2 | 4° / 6 | 3 | 10 | — |

## 每回合

1. 枚举己方 3 虫 × 敌方 9 虫，各粗解一发火箭，按实际伤害/击杀/自伤选一对。
2. 从所在格 DFS 出最多 9 个可达落点（含原地），按“站位 − 被还手”粗排。
3. 对前列落点各精解 ROCKET/GRENADE，取最高分；都不如不开火就只走位。

## 打分（单位 HP）

| 项 | 权重 |
|---|---|
| 打掉敌人 HP | ×1（头名 ×1.25） |
| 击杀 / 灭席 | +80 / +15 / +80 |
| 自伤 | ×2；自爆另罚 1000 |
| 本回合被还手 | ×2.0 |
| 站位与涨水 | ×1.0 |
| 弹药 | 手雷 −5 / 钻头 −8 |
| 打空 | −12 |

关键机制：所有席位同时提交动作，移动全部结算后才开火——敌人瞄的是你移动前的那一格。所以走位同时改变发射点和让对手这一发打空。

## 弹道

按引擎 Q16.16 语义复刻（定点、三角表、动态子步、手雷反弹、爆炸顺序、settle）。搜索用落点距离粗筛，选出的解再用精确模拟验伤害；粗搜只影响快不快，不影响准不准。

## 难度位置

`TIDE-EASY < TIDE-NORMAL < TIDE-HARD ≪ CHIMERA < PSEUDO < TYPHON`

TIDE 是公开基准/主要目标；CHIMERA/PSEUDO/TYPHON 是挑战 bot（同样只发二进制），不要求击败。

## 使用

预编译二进制在 `public/baselines/bin/tide_{easy,normal,hard}.exe`。

本地自检（在选手包目录内运行）：

```bash
python public/tools/selfcheck4.py --bot <你的bot.cpp> --baseline tide-normal --maps 2
```

## 许可

Apache-2.0；见仓库根目录的 `LICENSE` / `NOTICE`。

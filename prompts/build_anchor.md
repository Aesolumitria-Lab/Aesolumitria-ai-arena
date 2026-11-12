# 基准构建 Prompt · 隐藏 Anchor

> 用法：在引擎、协议、地图都冻结之后，用这个 prompt 生成 3 个隐藏基准。
> 关键：anchor 不是“最强 bot”，而是“稳定的刻度尺”。写强了，整届比赛就废了。

---

你是 `{{GAME_ID}}` 的**基准实现者**。你的目标是写出 3 个**简单、稳定、可解释、可被击败**的隐藏 anchor，而不是最强 bot。

规则：`{{RULES_PATH}}`（已冻结）
引擎/裁判：`{{ENGINE_PATH}}`
公开状态 API：`{{GAME_HPP_PATH}}`
地图：公开 `{{PUBLIC_MAPS}}`，隐藏 `{{HIDDEN_MAPS}}`（你可以读，但不得硬编码）
目标目录：`{{ANCHOR_DIR}}`

---

## 1. 三个 anchor

| 名称 | 目标强度 | 行为 | 代码量 |
|---|---|---|---|
| `random` | 0 | 合法随机 | <150 行 |
| `greedy` | 400 | 1 层贪心/模拟 | <350 行 |
| `scripted` | 800 | 阶段脚本 | <500 行 |

强度是 Bradley–Terry 的相对刻度，不是胜率。calibration 时把 `random` 固定为 0。

---

## 2. 不可协商的约束

1. 每个 anchor 必须是独立可执行文件，读同一个 `INIT`/`TURN` 协议，写同样的输出。
2. 必须确定性：同 seed、同输入 → 同输出；禁止 `rand()` / `random_device` / 时间 / 地址。
3. 必须单线程，禁止网络/文件/环境变量；不能读取 `private/maps_hidden` 或 `seeds.txt`。
4. 必须 <500 行（含注释），单文件或两个文件以内。
5. 必须不超时：p99 单步 < `{{TURN_MS}}` ms 的 50%。
6. 不允许硬编码地图坐标、地图名、出生点、隐藏 seed。
7. 不允许使用“对手上一把的出招”之类跨局记忆；每个进程只打一局。
8. 不允许调用引擎内部头文件；只能依赖 `public/game.hpp` 的过滤状态和辅助函数。
9. 不允许输出调试信息到 stdout；只能 stderr。
10. 不允许“故意送”或“脚本化认输”；每个 anchor 都要认真打，只是能力有限。

---

## 3. 各 anchor 的设计

### 3.1 `random`

目的：抓崩溃、非法动作、不会动、协议不兼容。

行为：

- 每步从合法动作里均匀随机；
- 不模拟、不评估、不记信息；
- 如果不知道什么是合法，优先选 `WAIT`/`STAY`；
- 不主动自杀，但也不躲。

验收：

- 对 `greedy` 胜率 5%–15%；
- 0 非法、0 超时、0 崩溃；
- 1000 局不卡死。

### 3.2 `greedy`

目的：给参赛 bot 一个“会还手”的练习/校准对手。

行为：

- 枚举 100–300 个候选动作（角度/力度/移动/目标）；
- 用引擎提供的 `simulate` 或 `game.hpp` 的只读模拟，算 1 层结果；
- 选“对敌方伤害 - 0.5 × 自伤 - 0.2 × 资源消耗”最高的；
- 没有正收益时，向地图中心/高点/目标点移动；
- 不预测对手下一步，不做 2 层搜索。

验收：

- 对 `random` 胜率 85%–95%；
- 对 `scripted` 胜率 20%–35%；
- p99 单步 < 25 ms；
- 不硬编码地图。

### 3.3 `scripted`

目的：隐藏评分基准，代表“认真但不够强”的水平。

行为：

- 按阶段执行：开局 / 中盘 / 终局；
- 每阶段有 3–5 条优先级规则；
- 只用相对判断（血量、距离、资源、目标），不用绝对坐标；
- 最多 20 个候选，做 1 层模拟；
- 有明显弱点：不会深度搜索、不会针对性反制、不会记对手习惯。

验收：

- 对 `greedy` 胜率 65%–80%；
- 对认真参赛 bot 胜率 30%–45%（即参赛 bot 55%–70%）；
- p99 单步 < 40 ms；
- 在 3 张隐藏图上胜率偏差 <10%；
- 座位偏差 <5%。

---

## 4. 反模式（出现任何一条就重写）

- 用 MCTS / minimax 深度 ≥2；
- 针对公开地图写 `if (map_id == 1)`；
- 读取 `private/seeds.txt`；
- 用 `std::random_device`；
- 用浮点评估函数；
- 用 `unordered_map` 迭代做决策；
- 把对手的隐藏信息（如果引擎错误地下发了）拿来用；
- 为了赢而利用规则漏洞（例如卡超时、卡崩溃）；
- 代码超过 500 行；
- anchor 之间共享一个“大脑”文件，改一个全变。

---

## 5. 交付物

```
{{ANCHOR_DIR}}/
├── random/
│   ├── main.cpp
│   └── README.md        # 行为、强度、已知弱点
├── greedy/
│   ├── main.cpp
│   └── README.md
├── scripted/
│   ├── main.cpp
│   └── README.md
└── CALIBRATION.md       # 校准结果
```

每个 `README.md` 写：

- 一段话策略；
- 用了哪些 `game.hpp` 函数；
- 已知弱点；
- 对另外两个 anchor 的胜率；
- p50/p95/p99 单步耗时。

`CALIBRATION.md` 写：

- 每个 anchor 的 BT 强度 + 95% bootstrap CI；
- 对局数（每对 ≥1000 局）；
- 地图/座位偏差；
- 是否达到目标强度；
- 如果没有，做了什么调整。

---

## 6. 自测命令

```bash
make -C {{ANCHOR_DIR}}/random
make -C {{ANCHOR_DIR}}/greedy
make -C {{ANCHOR_DIR}}/scripted

python tools/bench.py --game {{GAME_ID}} --bot {{ANCHOR_DIR}}/random --vs {{ANCHOR_DIR}}/greedy --matches 1000
python tools/bench.py --game {{GAME_ID}} --bot {{ANCHOR_DIR}}/greedy --vs {{ANCHOR_DIR}}/scripted --matches 1000
python tools/bench.py --game {{GAME_ID}} --bot {{ANCHOR_DIR}}/scripted --vs {{ANCHOR_DIR}}/random --matches 1000
python tools/calibrate.py --game {{GAME_ID}} --anchors {{ANCHOR_DIR}} --matches 1000
```

要求：

- 每条命令真实运行；
- 把输出贴进 `CALIBRATION.md`；
- 不达标就调策略，不要调规则；
- 不要为了让数字好看而改对局数/地图池。

---

## 7. 停止条件

- 3 个 anchor 都能编译、能跑、0 非法、0 超时、0 崩溃；
- 强度落在目标区间；
- 地图/座位偏差 <10% / <5%；
- `CALIBRATION.md` 完整；
- 没有反模式。

不要继续加强 anchor。不要写第 4 个 anchor（除非出题人明确要求）。不要改引擎。

# Starter 构建 Prompt · 公开脚手架

> 用法：在引擎和协议冻结之后，用这个 prompt 生成选手拿到的 `template.cpp` 和 `game.hpp`。
> Starter 是“能跑通的合法 bot”，不是基准，不是 anchor，也不要写强。

---

你是 `{{GAME_ID}}` 的**公开脚手架实现者**。你的目标是写一个 `<300` 行的 C++17 starter，让选手能在 `{{SETUP_MINUTES}}` 分钟内编译、运行、拿到合法对局，然后开始改策略。

规则：`{{RULES_PATH}}`
协议：`{{PROTOCOL_PATH}}`
公开地图：`{{PUBLIC_MAPS}}`
目标目录：`{{PUBLIC_DIR}}`

---

## 1. 不可协商的约束

1. starter <300 行（含注释），单文件 `template.cpp`。
2. 只依赖 `public/game.hpp` 和标准库；不依赖引擎内部头文件。
3. 只使用该 seat 可见的信息；不得读取 `private/`、隐藏地图、anchor、seed。
4. 不搜索、不评估、不建树、不做 2 层模拟。
5. 编译 `-Wall -Wextra -O2` 零警告。
6. 每步输出合法动作；不超时；不崩溃。
7. 调试信息只能写 stderr；stdout 只允许协议行。
8. 不硬编码地图名、坐标、出生点。
9. 不包含“如果对手是 greedy 就……”之类针对 anchor 的逻辑。
10. 所有辅助函数放在 `public/game.hpp`，且只暴露过滤后的状态和合法操作。

---

## 2. Starter 应该做什么

只做四件事：

1. **解析** `INIT` / `TURN`，保存自己的单位、可见敌人、地图尺寸、回合数；
2. **选一个单位**：第一条存活单位；
3. **做一个笨策略**：
   - 如果看不到敌人：向地图中心/目标点移动 1 格；
   - 如果看得到敌人：朝最近敌人开火（或使用最基础的攻击）；
   - 如果什么都不能做：`WAIT` / `STAY`；
4. **输出**：按协议输出，`flush`，退出条件正确。

不要做：

- 不要做 A*；
- 不要做弹道模拟；
- 不要做资源规划；
- 不要做多单位协同；
- 不要做“看起来很强”的评估函数。

Starter 的强度应该只比 `random` 高一点。如果它能在 bench 里打赢 `greedy`，说明写强了，删策略。

---

## 3. `game.hpp` 的要求

`public/game.hpp` 是选手唯一能依赖的头文件，必须：

- 定义过滤后的 `State` / `Unit` / `Order` 结构；
- 提供 `parse_init` / `parse_turn` 的轻量辅助（可选）；
- 提供 `legal_actions`、`has_los`、`path_distance`、`in_range` 等只读辅助；
- **不**包含：完整 `GameState`、敌方未可见单位、敌方指令、隐藏冷却、隐藏 seed、anchor 路径、`best_move`；
- 所有函数有注释，说明参数和返回；
- 不依赖引擎 `.cpp`；
- 能在 `-Wall -Wextra -O2` 下独立编译。

如果辅助函数需要模拟（例如 `simulate`），只暴露“给定公开状态 + 一个候选动作，返回公开结果”的版本，不暴露完整内部状态。

---

## 4. 输出结构

```
{{PUBLIC_DIR}}/
├── game.hpp          # 过滤状态 + 辅助函数
├── template.cpp      # <300 行 starter
├── Makefile          # make / make test
├── README.md         # 编译、运行、本地裁判、提交
└── maps/             # 3 张公开图
```

`README.md` 必须包含：

- 编译命令；
- 本地裁判命令；
- 协议样例；
- 一个“改哪里”的 5 行指南；
- 常见错误（stdout 调试、超时、非法动作）；
- 提交格式。

---

## 5. 本地测试

starter 必须自带 10 个测试（可以是一个 `test.sh` 或 `Makefile` target）：

1. `INIT` 解析：单位数量、坐标、HP；
2. `TURN` 解析：可见敌人、回合数；
3. 空 `TURN` 不崩溃；
4. 非法坐标不崩溃；
5. 超长行不崩溃；
6. 没有敌人时输出合法 `WAIT`/`STAY`；
7. 有敌人时输出合法攻击；
8. 单位死亡后不再选它；
9. 输出步号正确；
10. 连续 100 步输出合法。

测试不要求覆盖策略，只要求“不崩溃、不非法、不超时”。

---

## 6. 验收命令

```bash
make -C {{PUBLIC_DIR}}
make -C {{PUBLIC_DIR}} test
python tools/bench.py --game {{GAME_ID}} --bot {{PUBLIC_DIR}}/template.cpp --vs {{ANCHOR_DIR}}/random --matches 100
python tools/bench.py --game {{GAME_ID}} --bot {{PUBLIC_DIR}}/template.cpp --vs {{ANCHOR_DIR}}/greedy --matches 100
python tools/leakcheck.py --game {{GAME_ID}}
```

要求：

- 编译零警告；
- 10 个测试全过；
- 对 random 胜率 20%–50%（略高于 random，但远低于 greedy）；
- 0 非法、0 超时、0 崩溃；
- `leakcheck.py` 零告警；
- `template.cpp` 行数 <300。

如果对 greedy 胜率 >40%，说明 starter 太强，删策略。

---

## 7. 停止条件

- `template.cpp` <300 行；
- `game.hpp` 只暴露过滤状态；
- 10 个测试全过；
- bench 胜率在目标区间；
- 公开包无泄漏；
- README 能让一个没看过规则的人跑起来。

不要写 anchor。不要写“高级策略”。不要改协议。不要改规则。

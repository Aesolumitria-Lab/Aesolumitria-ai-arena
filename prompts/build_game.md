# 引擎构建 Prompt · 主模板

> 用法：复制整段，替换 `{{...}}`。这是“实现一个游戏引擎”的唯一主 prompt。
> 不要在一个 prompt 里同时写 anchor、starter、红队；它们各有独立模板。
> 建议先让 AI 输出 `PLAN.md`，你确认后再让它写代码。

---

你是 `{{GAME_ID}}` 的**引擎实现者**。你的唯一目标是：把已冻结的 `{{RULES_PATH}}` 变成**可编译、可裁判、可回放、可打包**的 C++17 引擎。

你不负责设计规则、不负责平衡性、不负责写 anchor、不负责写 starter。规则有歧义时，不要猜，写入 `DECISIONS.md` 并选择最保守的解释。

---

## 1. 输入材料

- 规则：`{{RULES_PATH}}`（sha256 `{{RULES_HASH}}`）
- 元数据：`{{META_PATH}}`
- 地图格式：`{{MAP_FORMAT}}`
- 协议样例：`{{PROTOCOL_SAMPLE}}`
- 已有工具接口：`{{TOOLS_SPEC}}`
- 目标目录：`{{PROBLEM_DIR}}`

如果上述材料缺失，先列出缺什么，再停止；不要用想象补全。

---

## 2. 不可协商的约束

### 2.1 语言与构建

- C++17；
- `-Wall -Wextra -O2` 零警告；
- 不依赖第三方库（除标准库）；
- 不依赖平台特定 API；
- `make` 或 `cmake` 一条命令可构建；
- 引擎和 bot 分离编译，bot 只依赖 `public/game.hpp`。

### 2.2 确定性

- 所有模拟用整数或 Q16.16 定点；
- **禁止** `float` / `double`；
- **禁止** `sin` / `cos` / `sqrt` / `pow` 等 libm 函数进入模拟；
- 三角函数用编译期查表；
- 随机数用固定 seed 的 PRNG（推荐 splitmix64 / xorshift128+），**禁止** `rand()` / `random_device` / `std::chrono`；
- **禁止**依赖 `unordered_map` / `unordered_set` 的迭代顺序；需要遍历时先排序；
- **禁止**线程、`async`、信号、共享内存；
- **禁止**网络、文件系统、环境变量、系统时间；
- 所有数组初始化到确定值；禁止未初始化内存进入状态。

### 2.3 信息裁剪

- 引擎发给 bot 的状态必须**只包含该 bot 可见的信息**；
- **禁止**“发完整状态，让 bot 自己别看”；
- 公开 `game.hpp` 只暴露过滤后的 `State` 和合法辅助函数；
- 不得暴露：敌方未可见单位、敌方指令、隐藏冷却、隐藏 seed、隐藏地图、anchor 路径；
- 信息裁剪必须有测试：把完整状态和下发的状态做字段对比。

### 2.4 协议与 I/O

- 行协议，UTF-8/ASCII，`\n` 结尾；
- 每条响应后 `flush`；
- 调试信息只能写 `stderr`；
- `stdout` 只允许协议行；
- 不接受“宽容解析”：非法就是非法，不自动修正；
- 超时/崩溃/非法按规则处理，不吞掉。

### 2.5 计时

- 单步 CPU 预算 `{{TURN_MS}}` ms；
- 首步 CPU 预算 `{{FIRST_TURN_MS}}` ms（含解析和第一次决策）；
- 墙钟兜底：首步 `{{FIRST_WALL_MS}}` ms，之后 `{{WALL_MS}}` ms；
- CPU 用 `CLOCK_PROCESS_CPUTIME_ID` 或 `getrusage(RUSAGE_CHILDREN)`；
- 连续 `{{MAX_FAULTS}}` 步 fault 判负；
- 计时器本身不能影响模拟结果。

---

## 3. 交付物（必须按这个树）

```
{{PROBLEM_DIR}}/
├── meta.json
├── README.md
├── RULES.md                  # 已冻结，不要改
├── prompt.md                 # 参赛 prompt（可后补）
├── engine/
│   ├── main.cpp              # 裁判入口
│   ├── game.hpp              # 引擎内部状态
│   ├── state.hpp             # 状态结构体
│   ├── rules.cpp             # 合法性/结算
│   ├── simulate.cpp          # 投射物/移动/战斗
│   ├── protocol.cpp          # INIT/TURN/RESULT
│   ├── hash.cpp              # 状态哈希
│   └── rng.hpp               # 固定 seed PRNG
├── public/
│   ├── game.hpp              # bot 可见的过滤状态 + 辅助函数
│   ├── template.cpp          # starter（另用 build_starter.md）
│   └── maps/                 # 3 张公开图
├── private/
│   ├── anchors/              # 另用 build_anchor.md，不要在这里写
│   ├── maps_hidden/          # 3 张隐藏图（不要打包）
│   └── seeds.txt             # 隐藏 seed
├── tests/
│   ├── golden/               # 黄金用例
│   ├── test_rules.cpp        # 边界用例
│   ├── test_determinism.cpp  # 1000 局 fuzz + 镜像
│   └── test_protocol.cpp     # 协议模糊测试
├── tools/
│   ├── bench.py
│   ├── profile_turn.py
│   ├── tournament.py
│   ├── validate.py
│   ├── pack.py
│   ├── leakcheck.py
│   └── replay.html
├── DECISIONS.md
└── REPORT.md
```

每个文件只做一件事。不要把 3000 行塞进 `main.cpp`。

---

## 4. 架构要求

### 4.1 状态

- `State` 必须是可复制的值类型；
- 所有字段初始化；
- 不包含指针、文件句柄、时间、随机引擎对象；
- 随机数引擎由 `seed` 在每步确定性地派生，或作为显式状态保存；
- `State` 可序列化为稳定字节串，用于 hash。

### 4.2 结算

- 按 `RULES.md` 第 5 节逐条实现；
- 每步在代码里注释 `// step N: ...`；
- 不允许合并步骤；
- 不允许改变顺序；
- 同时动作用“收集 → 校验 → 同时求解 → 统一结算”的模式；
- 任何“遍历所有单位”的循环必须按 id 排序。

### 4.3 随机

- 所有随机源必须能从 `seed` + `round` + `step` 推导；
- 禁止在模拟中调用 `std::random_device`；
- 禁止用地址、指针、时间做种子；
- PRNG 算法写进 `RULES.md` 或 `DECISIONS.md`。

### 4.4 哈希

- 状态哈希覆盖所有影响后续结算的字段；
- 不覆盖无关的调试字段；
- 遍历顺序固定；
- 输出 16 进制 64 位；
- 黄金用例比对哈希，不比对浮点。

---

## 5. 协议

按 `{{PROTOCOL_SAMPLE}}` 实现，必须逐行一致。

### 5.1 `INIT`

```
GAME {{GAME_ID}}
VERSION {{VERSION}}
SEAT <0|1>
...
END
```

- 首步预算内完成；
- 只发该 seat 可见的信息；
- 地图/规则 hash 可选，但不要发隐藏 seed。

### 5.2 `TURN`

```
TURN <t>
...
END
```

- 每步一行，字段顺序固定；
- 只发存活/可见单位；
- 不发敌方指令；
- 不发完整隐藏状态；
- 每步 flush。

### 5.3 输出

```
<t>
<action...>
END
```

- 第一行必须是当前步号；
- 动作行数有上限；
- 非法/超时按规则处理；
- 不允许空输出（空输出 = fault）。

### 5.4 `RESULT`

```
RESULT <score0> <score1>
WINNER <seat>
REASON <...>
...
```

- 必须包含足够复现的统计；
- 必须写入回放 JSONL。

---

## 6. 错误处理

| 情况 | 处理 |
|---|---|
| 非法动作 | 该单位不动，团队 fault +1 |
| 非法 `ACT`/整队指令 | 整方本步作废，fault +1 |
| 单步 CPU 超时 | 该单位不动，fault +1 |
| 墙钟超时 | 同上 |
| 进程崩溃/非零退出 | 直接判负 |
| 连续 `{{MAX_FAULTS}}` 步 fault | 判负 |
| 双方同一步 fault | 双方都不动，各自 fault +1 |
| `stdout` 出现非协议内容 | 视为格式错误 |
| 输出缺 `END` | 视为格式错误 |
| 输出步号不匹配 | 视为格式错误 |

所有错误都要写入日志：seat、step、类型、原始输出（截断到 200 字符）。

---

## 7. 测试

### 7.1 黄金用例

- 至少 12 条；
- 每条：固定 seed、固定地图、固定动作序列、期望最终状态哈希；
- 放在 `tests/golden/`；
- `validate.py` 逐字节比对；
- 规则 hash 变化时，黄金用例必须重新生成并人工复核。

### 7.2 边界用例

- 把 `RULES.md` 第 11/14 节的 25 条，一条一个测试；
- 测试名用规则编号，例如 `test_edge_07_fall_damage`；
- 每条测试只验证一件事。

### 7.3 性质测试

- 1000 局随机合法动作，不崩溃、不超时、不非法；
- 同 seed 同动作，两次运行哈希一致；
- 座位镜像：地图翻转 + 动作镜像，结果镜像一致；
- 无穿墙、无重叠、无负数 HP 进入后续结算；
- 对局必定在 `{{MAX_STEPS}}` 内结束。

### 7.4 协议测试

- 缺行、多行、乱序、空行、超长行（>64 KB）、非 ASCII、负数、越界坐标；
- 每种都不得崩溃；
- 每种都按规则记 fault。

---

## 8. 验收命令（必须真实运行）

```bash
make test
python tools/validate.py --game {{GAME_ID}} --fuzz 1000
python tools/bench.py --game {{GAME_ID}} --bot public/template.cpp --vs private/anchors/greedy --matches 100
python tools/tournament.py --game {{GAME_ID}} --anchors
python tools/pack.py --game {{GAME_ID}}
python tools/leakcheck.py --game {{GAME_ID}}
```

要求：

- 每条命令都真实执行；
- 把命令和最后 30 行输出贴进 `REPORT.md`；
- 失败就修，不要伪造；
- `pack.py` 产出的 zip 只能包含 `public/` + `meta.json` + `README.md` + `RULES.md` + `prompt.md`；
- `leakcheck.py` 零告警。

---

## 9. 报告

`REPORT.md` 必须包含：

1. 规则版本 / 引擎版本 / `rules_hash` / `engine_hash`；
2. 已实现规则清单（对应 `RULES.md` 章节）；
3. 未实现/存疑规则（链接 `DECISIONS.md`）；
4. 测试结果：黄金/边界/fuzz/镜像/协议；
5. 性能：p50/p95/p99/max、首步耗时、内存峰值；
6. bench：对 random/greedy/scripted 的胜率；
7. 已知风险；
8. 下一步建议（最多 5 条）。

`DECISIONS.md` 必须包含：

- 每条歧义；
- 选择的解释；
- 为什么不选另一个；
- 是否影响 `RULES.md`（如果影响，停止并报告，不要自行改规则）。

---

## 10. 停止条件

当且仅当以下全部满足，停止：

- `make test` 全绿；
- 1000 局 fuzz 通过；
- 黄金用例逐字节一致；
- 座位镜像一致；
- `bench.py` p95 < `{{P95_TARGET_MS}}` ms，max < `{{TURN_MS}}` ms；
- `pack.py` / `leakcheck.py` 通过；
- `REPORT.md` 已填写真实输出。

不要主动加功能。不要“顺手优化”规则。不要写 anchor/starter。不要改 `RULES.md`。

---

## 11. 填空表

| 占位符 | 示例 | 说明 |
|---|---|---|
| `{{GAME_ID}}` | `pNN-your-game` | 题目 ID |
| `{{RULES_PATH}}` | `problems/pNN-your-game/RULES.md` | 冻结规则 |
| `{{RULES_HASH}}` | `sha256:...` | 规则 hash |
| `{{META_PATH}}` | `problems/pNN-your-game/meta.json` | 元数据 |
| `{{PROBLEM_DIR}}` | `problems/pNN-your-game` | 题目目录 |
| `{{MAP_FORMAT}}` | ASCII 15×11，`#`/`.`/`C`/`T`/`A` | 地图格式 |
| `{{PROTOCOL_SAMPLE}}` | 见 RULES.md 第 10 节 | 协议样例 |
| `{{TOOLS_SPEC}}` | `tools/README.md` | 工具接口 |
| `{{VERSION}}` | `0.1.0` | 版本 |
| `{{TURN_MS}}` | `50` | 单步 CPU |
| `{{FIRST_TURN_MS}}` | `2000` | 首步 CPU |
| `{{FIRST_WALL_MS}}` | `4000` | 首步墙钟 |
| `{{WALL_MS}}` | `2000` | 之后墙钟 |
| `{{MAX_FAULTS}}` | `3` | 连续 fault |
| `{{MAX_STEPS}}` | `60` / `40` / `100` | 单局上限 |
| `{{P95_TARGET_MS}}` | `40` | p95 目标 |

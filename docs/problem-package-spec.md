# 题目包规范

所有题共用同一套目录、协议、回放、版本和安全约定；新题只替换规则和引擎。

## 1. 目录结构

```
problems/<game>/
  meta.json              机器可读元数据
  README.md              题目介绍、怎么玩、变更记录
  RULES.md               选手可见的精确规则（唯一权威）
  CONDITIONS.md          比赛条件、版本、预算、事故记录
  prompt.md              发给参赛模型的提示词
  public/                只有这里能进选手包
    engine/              引擎源码
    judge/               本地裁判（与正式评测同源）
    maps/ (或 maps4/)    公开地图
    tools/               选手可用的公开工具（自检、计时）
    template*.cpp, arena*.hpp   Starter 与 SDK 头
  tests/                 规则测试（validate.py 等）
  tools/                 本题专用脚本
  docs/                  本题专用文档
  tournament/            赛制配置与结果
  private/               仓库外的私有内容，通过 link_private 挂入（见 §7）
packages/<game>/         选手包（pack.py 生成后解压的内容）
tools/                   各题通用的工具
docs/                    通用文档
```

`packages/<game>/` 不手写，是 `tools/pack.py` 从 `public/` 加白名单文件（`meta.json`、`README.md`、`RULES.md`、`prompt.md`）及 `CONTESTANT_RULES.md`（来自 `docs/contestant-rules.md`）生成的产物，带 `manifest.json`（文件列表 + sha256）。

新题从 `problems/_template/` 复制。

## 2. meta.json

主要字段：

| 字段 | 含义 |
|---|---|
| `id` | 全局唯一，小写连字符，与目录名一致 |
| `version` | 语义化版本；规则改动必须 bump |
| `status` | `draft` / `playtest` / `released` / `archived` |
| `seats` / `mode` | 席位数与赛制 |
| `turn_ms` / `first_turn_ms` | 每步 CPU 预算 / 首步宽限 |
| `max_steps`、`rounds_per_match` | 单局步数与每场局数 |
| `partial_observability` | 是否有信息裁剪 |
| `deterministic` | 必须为 `true` |
| `rules_hash` / `engine_hash` / `arena_hash` | 规则、引擎、SDK 头的哈希；由 `tools/refresh_meta.py` 刷新，`acceptance` 校验 |
| `anchors`（及 4 席题的 `anchors4`） | 基准列表 |
| `difficulty` | 人工排序标签，不被任何工具读取，不是验收项 |

`status` 生命周期：`draft`（规则在改，不用于排名）→ `playtest`（规则冻结，跑平衡和校准）→ `released`（规则 hash 冻结）→ `archived`。

`acceptance` 的 `meta` 项会拒绝：`version` 以 `0.` 开头、`status` 为 `draft`、含 `TODO`、hash 与文件不符。模板原样跑 `acceptance` 一定不过，这是设计。

## 3. RULES.md 的结构

至少包含以下小节，顺序固定：概览；地图与初始状态；单位与属性；一步的流程；**结算顺序（编号）**；信息模型；目标与胜负；平局与 tiebreak；判罚；协议；裁定；数值速查表。

三条硬要求：结算顺序必须编号；每个“同时”都要说明冲突怎么处理；每个“随机”都要说明 seed 来源。

## 4. 协议约定

选手 bot 是独立进程，stdin 读、stdout 写、stderr 只写日志。

- 一行一条消息，`\n` 结尾；每步输出后必须 flush；
- 动作行以回合号开头，迟到/重复的回合号丢弃；
- 非法动作按题判罚，不静默修正；
- stdout 只能有协议行；
- 时间预算按 CPU 时间算，墙钟只做保险上限。

公开头文件只能暴露**过滤后的本方状态**和纯 helper（视线、寻路、弹道模拟等），不能包含敌方未可见单位、对手指令、隐藏冷却、隐藏地图或 seed，也不能包含 `best_move`、`solve` 之类的“答案”。

动作应可枚举、可序列化、可校验、可回放；数字用十进制整数，不用浮点。

## 5. 回放格式

回放是 JSONL，必须能只靠 seed + 各方动作重建整局：

```jsonl
{"type":"header","game":"<game>","version":"...","rules_hash":"...","engine_hash":"...","seed":7,"map":"..."}
{"type":"map","id":"...","grid":["...原始地图..."]}
{"type":"game", ...}
{"type":"turn","t":2,"orders":{"0":"...","1":"..."},"state_hash":"..."}
{"type":"result", ...}
```

各题的 `game`/`turn`/`result` 字段不同，以 `tools/README.md` §replay 与各题文档为准。要求：

- `map.grid` 必须是**原始地图**，不是下发给 bot 的裁剪地形；
- 每步记录各方动作与 `state_hash`，结果状态由引擎重算；
- 同 seed + 同动作 + 同引擎，必须得到同一个 `state_hash`；
- 旧回放只配旧引擎；`engine_hash` 不一致时标记“旧引擎”，不要用新引擎硬跑。

## 6. 测试规范

至少四类：

- **Golden**：固定输入 → 固定 `state_hash` 与结果；
- **边界用例**：同时移动/冲突、死亡后行为、射程与视线边缘、超时/非法/崩溃、平局 tiebreak、地图边界；
- **Fuzz**：随机合法动作多局，无崩溃、越界、死循环，同 seed 结果一致；对局应足够长，别让上限没被压到；
- **对称/换座**：对称规则下换座后结果应镜像；同名 bot 完整轮换座位应接近平局。

## 7. 私有内容外置

隐藏地图、anchor / 基准 / 挑战 bot 的源码**不在本仓库**。私有根目录结构为 `<私有根目录>/<game>/private/...`，出题人运行：

```bash
python tools/link_private.py --root <私有根目录>
```

（或设环境变量 `ARENA_PRIVATE`）把它挂到 `problems/<game>/private`，该路径被 `.gitignore` 忽略。没有私有内容时，只依赖 `public/` 的工具照常可用；需要隐藏图或 anchor 源码的工具会报找不到文件。

## 8. 打包与泄漏检查

两类包：

| 包 | 工具 | 内容 | 给谁 |
|---|---|---|---|
| 选手包 | `pack.py` | `public/` + meta/README/RULES/prompt + `CONTESTANT_RULES.md` | 参赛者 |
| 评测包 | `pack_eval.py` | anchor 二进制 + 隐藏地图 + 元数据 | 评测服务端 |

两类包都不含 anchor 源码。`pack.py` 固定时间戳与顺序，两次打包应 sha256 一致；`verify_package.py` 校验 manifest；`leakcheck.py` 是第二道闸，检查私有路径、隐藏地图、源码、绝对路径等。

## 9. 版本与复现

每次规则或引擎改动：

1. bump `version`；
2. `refresh_meta` 更新 hash；
3. 重跑 validate（golden + fuzz）与 acceptance，把读数记入 `CONDITIONS.md`。

版本号建议：`0.x.y` 草稿；`1.0.0` 首个正式赛季；`1.x.0` 加机制不破坏旧回放；`2.0.0` 破坏性改动。

## 10. 安全与沙箱

bot 必须跑在独立子进程：限制 CPU、内存，只读工作目录，禁网络、禁 fork，墙钟设保险上限。判罚：超时/非法/格式错误本步无动作并记 fault；累计 3 次判负；崩溃判负。部分信息的裁剪在引擎侧完成。

环境要求见 [eval-environment.md](eval-environment.md)，参赛行为红线见 [contestant-rules.md](contestant-rules.md)。

## 11. 新题 checklist

- [ ] `meta.json` 完整，`id` 与目录一致；
- [ ] `RULES.md` 12 节，结算顺序编号；
- [ ] `CONDITIONS.md`、`prompt.md` 齐全；
- [ ] `public/` 只含公开文件；
- [ ] 私有内容放在仓库外，通过 `link_private.py` 挂入；
- [ ] golden、fuzz、determinism 测试通过；
- [ ] `pack.py` + `verify_package.py` + `leakcheck.py` 通过；
- [ ] 回放可逐帧重算；
- [ ] 题目 README 有变更记录。

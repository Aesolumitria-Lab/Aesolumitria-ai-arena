# tools/ · 工具索引与接口契约

本目录只放各题通用的工具。P01 专用脚本（`p01_*.py`、`genmap_p01*.py`）在 [`problems/p01-ballistic/tools/`](../problems/p01-ballistic/tools/)。各题的规则测试在 `problems/<game>/tests/`。设计说明见 [`../docs/tooling-and-timing.md`](../docs/tooling-and-timing.md)。

参数以 `python tools/<x>.py --help` 为准；本文只写用途与契约。`--game` 接受题号前缀、目录名或路径。

## 1. 通用约定

- 从 `problems/<game>/meta.json` 读配置。
- 退出码：`0` 成功；`1` 检查失败；`2` 参数或文件错误（各工具细节不同，以 `--help` 与实现为准）。
- 需要隐藏图或基准源码（`problems/<game>/private`）的工具，在没有私有内容时会报找不到文件。私有内容用 `link_private.py` 挂入，见下。
- 官方计时、验收请保持单并发（`--workers 1` / `--jobs 1`）。
- 各题 `tests/validate.py` 的统一输出契约（由 `harness.py` 定义）：

```json
{"tool": "validate", "game": "<game>", "ok": true, "passed": 24, "total": 24,
 "results": [{"name": "...", "ok": true, "detail": ""}]}
```

`validate.py --json` 输出汇总：`{"tool":"validate","ok":...,"games":[{"game":...,"ok":...,"passed":...,"total":...}]}`。

## 2. 工具索引

### 私有内容

| 工具 | 用途 |
|---|---|
| `link_private.py` | 把仓库外的私有目录挂到 `problems/<game>/private`（Windows 目录联接，其他系统符号链接）。`--root <私有根目录>` 或环境变量 `ARENA_PRIVATE`；`--unlink` 只移除链接。已存在的真实 `private` 目录不会被覆盖 |

私有根目录结构：`<私有根目录>/<game>/private/...`。

### 验收与测试

| 工具 | 用途 |
|---|---|
| `acceptance.py` | 工程验收：docs / meta / maps / validate / validate4 / pack / leak / package 八项；`--fuzz N`；`--ranking` 另验相邻基准档位；`--json` |
| `validate.py` | 汇总调用各题 `tests/validate.py`；`--fuzz N`；`--update-golden` 仅单题且该题支持时有效 |
| `abuse.py` | 抗滥用测试：恶意 bot（死循环、巨量输出、无应答、崩溃）不能让裁判挂死或获胜 |
| `crosscheck.py` | 跨平台 `state_hash` 轨迹：各平台 `--json` 输出后用 `--compare A.json B.json` 比对 |
| `refresh_meta.py` | 把 `meta.json` 的 hash 与地图计数对齐到文件；`--dry-run` 只报差异 |
| `check_env.py` | 检查 Python / g++ / C++17 / 模板编译 / 选手包 / 评测包结构（`--game`、`--package`、`--eval --path`） |
| `tests/` | 工具自身的单元测试，见 §4 |

### 打包与泄漏

| 工具 | 用途 |
|---|---|
| `pack.py` | 生成选手包（只取 `public/` 与白名单文件，附 `CONTESTANT_RULES.md` 与 `manifest.json`，固定时间戳，可复现）。`--game`、`--out` |
| `verify_package.py` | 校验选手包 manifest 的每个文件 sha256、无多余文件、hash 与当前 `meta.json` 一致（`--path` 为 zip 或目录） |
| `leakcheck.py` | 扫描私有路径、隐藏地图、源码、绝对路径等；`--path`，`--eval` 按评测包白名单检查 |
| `seal_anchors.py` | 把私有基准源码编译成二进制 + `manifest.json`，不复制源码；`--seats 2/4/both`，`--strip` |
| `pack_eval.py` | 生成评测包（基准二进制 + 隐藏地图 + 元数据），`--seats 2/4`，`--no-hidden` |

### 对局、基准与评分

| 工具 | 用途 |
|---|---|
| `harness.py` | 公共驱动库（路径、`all_games()`、报告器），不是命令行工具 |
| `bench.py` | 任意两个 bot 对打：胜负、margin、步数分布；`--bot`、`--vs`、`--anchors-bin` |
| `profile_turn.py` | 单局逐步 CPU/墙钟剖析，列最慢的步 |
| `calibrate.py` | 基准互打校准（margin 口径）与强度表 |
| `rating.py` | Bradley–Terry 实力值（胜负口径，含每对一局虚拟平局，bootstrap CI） |
| `anchor_audit.py` | 两席基准档位单调性审计，只看胜负 |
| `challenge.py` | 选手本地一键挑战：只用公开对手与公开 seed，不是官方排名 |
| `tournament.py` | 赛程冻结 + 断点续跑 + BT 排名 + 回放归档（两席） |
| `ffa4.py` | 4 席对局运行器（库，供 `rating4` / `anchor_audit4` 复用） |
| `rating4.py` | 4 席原始胜率评分（座位轮换、地图块 bootstrap） |
| `ffa_rating.py` | 从 4 席回放/JSONL 汇总评分 |
| `anchor_audit4.py` | 4 席基准档位审计 |
| `bot_registry.py` | bot 提交条目登记：`validate` / `list` / `resolve` |
| `agent_deadline.py` | 给选手 agent 命令加硬性墙钟截止，到点终止进程树 |

### 回放

| 工具 | 用途 |
|---|---|
| `replay.py` | `verify`：用引擎逐帧重算并比对 `state_hash`；`html`：生成单文件查看器 |
| `replay4.py` | 4 席回放的 `verify` / `html` |

### 其他

通用 `tools/` 里没有地图生成器；P01 的地图生成器见 `problems/p01-ballistic/tools/` 下的 `genmap_p01*.py`。

## 3. 关键契约

### 3.1 `bench.py` JSON

```json
{"tool": "bench", "game": "<game>", "bot": "...", "vs": "...",
 "matches": 8, "wins": [7, 1], "draws": 0, "margin_mean": 0.0,
 "steps_p50": 0, "steps_p95": 0, "elapsed_s": 0.0,
 "rows": [{"seed": 1, "map": "...", "winner": 0, "margin": 0, "steps": 0}]}
```

字段以实现为准；margin 的单位每题不同，不可跨题比较。

### 3.2 `tournament.py` 赛程档案

`--out` 指向的 JSON 是唯一档案：第一次运行写入 `config` 与 `config_hash` 冻结赛程，参数不一致时报错；每完成一局原子追加，中断后重跑跳过已完成的局。round-robin 一次生成整届对阵；swiss 每轮开打前追加，只能冻结参数与已生成的轮次。评分复用 `rating.fit`。档案绑定的产物范围（bot 二进制、地图等）以实现为准。

### 3.3 回放 JSONL

```jsonl
{"type":"header","game":"...","version":"...","rules_hash":"...","engine_hash":"...","seed":7,"map":"..."}
{"type":"map","id":"...","grid":["...原始地图..."]}
{"type":"game", ...}
{"type":"turn","t":2,"orders":{"0":"...","1":"..."},"state_hash":"..."}
{"type":"result", ...}
```

`map.grid` 是原始地图。`verify` 只依赖地图与动作重算，不信任回放里记录的状态；`engine_hash` 与当前 `meta.json` 不同时标记“旧引擎”，帧 hash 仍一致说明兼容。4 席字段见 `replay4.py` 与 P01 文档。

### 3.4 封闭基准与评测包

```bash
python tools/seal_anchors.py --game <game>
```

```bash
python tools/pack_eval.py --game <game>
```

两类包不要混：选手包（`pack.py`）只含 `public/`；评测包（`pack_eval.py`）只含基准二进制与隐藏图。两者都不含基准源码。使用封装基准时给 `bench.py` 等工具传 `--anchors-bin <目录>`，解析顺序为二进制优先、源码兜底。

## 4. 工具自身的测试

```bash
python -m unittest discover -s tools/tests -v
```

## 5. 常用命令

```bash
python tools/acceptance.py --game p01-ballistic
```

```bash
python tools/pack.py --game p01-ballistic
```

```bash
python tools/verify_package.py --path <选手包 zip 或目录>
```

```bash
python tools/leakcheck.py --path <选手包 zip 或目录>
```

```bash
python tools/check_env.py --game p01-ballistic
```

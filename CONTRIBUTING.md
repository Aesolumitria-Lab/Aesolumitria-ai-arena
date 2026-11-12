# CONTRIBUTING

操作手册。规则以 `problems/<game>/RULES.md` 为唯一权威；工具接口见 [tools/README.md](tools/README.md)；目录、协议、版本见 [docs/problem-package-spec.md](docs/problem-package-spec.md)；通用已知问题见 [docs/known-issues.md](docs/known-issues.md)。

这个仓库里最贵的是 **hash**：`rules_hash` / `engine_hash` / `arena_hash` 和每张地图的 sha256 都按字节计算，三类改动的影响面不同（见 §2）。

## 1. 私有内容

隐藏地图与基准/挑战 bot 源码不在仓库中。需要时：

```bash
python tools/link_private.py --root <私有根目录>
```

没有私有内容时，只依赖 `public/` 的工具照常可用，需要私有内容的工具会报找不到文件。**不要把私有内容提交进仓库**；`problems/<game>/private` 已被 `.gitignore` 忽略。

## 2. 提交前（顺序不能换）

```bash
python tools/refresh_meta.py --game p01-ballistic
```

```bash
python tools/pack.py --game p01-ballistic
```

```bash
python tools/acceptance.py --game p01-ballistic
```

需要 fuzz 或档位检查时：

```bash
python tools/validate.py --game p01-ballistic --fuzz 100
```

```bash
python tools/acceptance.py --game all --ranking
```

`refresh_meta` 必须在 `acceptance` 之前：`acceptance` 的 `meta` 项把 `meta.json` 里的 hash 与磁盘文件比对，顺序颠倒会报“hash 过期”，多数不是代码坏了而是忘了刷 meta。`acceptance` 校验 `rules_hash`、`engine_hash`、`arena_hash`（字段可选，写了就必须对）。

工具自身的测试：

```bash
python -m unittest discover -s tools/tests -v
```

## 3. 三种改动，三种影响面

| 改的是 | 变的 hash | 必须重跑 | 不要顺手碰 |
|---|---|---|---|
| 规则 `RULES.md` | `rules_hash` + 包 manifest | bump `version` → `refresh_meta` → `validate` → `acceptance` → 读数写入 `CONDITIONS.md` | 引擎、地图 |
| 引擎 / SDK 头 | `engine_hash`，以及全部 `state_hash` | `refresh_meta` → `crosscheck` → 回放重生成 | 已发布回放与成绩 |
| 地图 | 每张图 sha256 + 开局 `state_hash` | 用该题的生成器重生成 → 该题的地图校验 | 隐藏图与公开图不得内容重合 |

改引擎会使旧回放与成绩失去可比性：旧回放只配旧引擎，对不上就标“无法复现”，不要用新引擎硬跑。

`refresh_meta` 只哈希它列出的文件；引擎依赖的其他文件（如被包含的头文件）变动不会让 `engine_hash` 变化，需人工登记进 `CONDITIONS.md` 并做跨平台对账：

```bash
python tools/crosscheck.py --game p01-ballistic --json <输出1.json>
```

另一平台再跑一份后：

```bash
python tools/crosscheck.py --compare <输出1.json> <输出2.json>
```

各题专用的地图生成与校验脚本在 `problems/<game>/tools/`，说明见该题 `docs/`。

## 4. 换行符：git 钉死 LF

仓库用 `.gitattributes` 把文本钉为 LF。hash 按字节计算，若文件被 checkout 成 CRLF，干净 clone 上 `acceptance` 会因 hash 对不上而失败，而本机可能通过。不要用 `git config core.autocrlf false` 去“覆盖”（只改你一台机器），编辑器也请设为 LF。

## 5. 加一道新题

```bash
cp -r problems/_template problems/pNN-your-game
```

按 [docs/problem-package-spec.md](docs/problem-package-spec.md) 填写，并按 [docs/ai-prompts.md](docs/ai-prompts.md) 的顺序使用 `prompts/`。要点：

- 只有 `public/` 能进选手包；`public/engine/` 目录存在即会被 `tools/harness.py` 的 `all_games()` 收入全库验收；
- 隐藏图与基准源码放在仓库外，通过 `link_private.py` 挂入；
- 模板的 `version` 为 `0.x` 且 `status` 为 `draft`、并含 `TODO` 占位，照模板原样跑 `acceptance` 一定不过，这是设计；
- `pack.py` 当前对所有题排除 `public/template.cpp` 与 `public/arena.hpp`，并把包内 `public/judge/judge.py` 改名为 `judge2.py`（见 `tools/pack.py` 的 `EXCLUDE_EXACT` / `RENAME_IN_Pack`）。新题命名要避开，或先改 `pack.py` 并单独评审；
- 打完包后核对条目：

```bash
python tools/pack.py --game pNN-your-game
```

```bash
python tools/verify_package.py --path <打出的 zip>
```

```bash
python tools/leakcheck.py --path <打出的 zip>
```

```bash
python tools/acceptance.py --game pNN-your-game
```

## 6. 不要做的事

- 不要提交构建产物（`build/`、`dist/`、`__pycache__/`、`*.exe` 等，以 `.gitignore` 为准）。基准/挑战二进制不进版本库，干净 clone 里对应目录为空，依赖它们的冒烟测试会 `SKIP`，这是设计内的跳过。
- 不要把私有内容弄进选手包。`pack.py` 只从 `public/` 取文件，`leakcheck.py` 是第二道闸，两个都要过；`leakcheck` 通过不等于绝对干净，发布前仍需人工复核。
- 不要为了让测试变绿去改断言。断言红着说明数据不对，先修数据。
- 不要手改 `meta.json` 里的 hash（用 `refresh_meta`），也不要手改地图（manifest 会静默过期）。
- 不要跨题比较 `bench` 的 `margin`，单位每题不同。
- 不要在文档、提示词、示例里写本机绝对路径、隐藏图 seed、具体参赛模型名。

## 7. PR 自查清单

- [ ] `python tools/refresh_meta.py --game <x> --dry-run` 无差异
- [ ] `python tools/acceptance.py --game <x>` 全 PASS
- [ ] 改规则：`version` 已 bump，`CONDITIONS.md` 已记读数
- [ ] 改引擎：已说明哪些回放/成绩作废，并已重生成或标注“无法复现”
- [ ] 改地图：该题的地图校验已通过
- [ ] 文本文件为 LF
- [ ] 包里没有私有内容、没有构建产物；动过 `public/` 就重打过包
- [ ] `python -m unittest discover -s tools/tests -v` 通过
- [ ] 文档里没有本机路径、隐藏 seed、参赛模型名

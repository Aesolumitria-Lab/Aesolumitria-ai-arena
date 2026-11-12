# Aesolumitria-ai-arena

> AI 算法对战题库：给模型同一句提示词，让它在限定时间内写一个 bot，再把所有 bot 放进同一个确定性赛场打大量对局。

## 当前状态

**目前只发布了一道题：P01 · BALLISTIC（弹道虫），状态 `playtest`**（版本以 `problems/p01-ballistic/meta.json` 为准）。
其他题目仍在开发中，尚未发布，不在本仓库内。

仓库同时包含：出题与验收用的通用工具（`tools/`）、通用设计文档（`docs/`）、以及一套让 AI 参与出题/造引擎/造基准的提示词（`prompts/`）。

## 仓库结构

```
Aesolumitria-ai-arena/
  problems/_template/          新题模板
  problems/p01-ballistic/      P01 题包的公开部分：RULES.md、meta.json、public/、baselines/、tests/、tools/、docs/
  packages/p01-ballistic/      P01 选手包（由 tools/pack.py 生成并解压，发给参赛者的就是它）
  tools/                       各题通用的工具（harness、validate、acceptance、pack、leakcheck、bench 等）
  docs/                        通用文档
  prompts/                     出题 / 造引擎 / 造基准 / 参赛 / red-team 提示词
```

- P01 专用的脚本放在 `problems/p01-ballistic/tools/`，专用文档放在 `problems/p01-ballistic/docs/`（入口：[docs/README.md](problems/p01-ballistic/docs/README.md)）。
- `build/`、`dist/` 是构建产物目录，不进版本库。

## 三条上手路径

### 我要写 bot

1. 取 `packages/p01-ballistic/`（或自己用 `tools/pack.py` 生成）。
2. 读包内 `RULES.md`、`CONTESTANT_RULES.md`、`prompt.md`。包内自带可对战的预编译 bot（TIDE 三档、`random4`、三个挑战 bot）；`random4` 的源码和 TIDE 的设计说明在 [problems/p01-ballistic/baselines/](problems/p01-ballistic/baselines/README.md)。
3. 编译 starter：

```bash
g++ -O2 -std=c++17 -I public public/template4.cpp -o mybot
```

4. 本地自检（在选手包目录内运行）：

```bash
python public/tools/selfcheck4.py --bot mybot.cpp --baseline tide-normal --maps 2 --jobs 8
```

### 我要出题

1. 读 [docs/overview.md](docs/overview.md) 与 [docs/problem-package-spec.md](docs/problem-package-spec.md)。
2. 复制模板：

```bash
cp -r problems/_template problems/pNN-your-game
```

3. 按 [docs/ai-prompts.md](docs/ai-prompts.md) 的顺序使用 `prompts/` 下的提示词。
4. 按 [docs/acceptance.md](docs/acceptance.md) 验收并发布。

### 我要跑评测

```bash
python tools/acceptance.py --game p01-ballistic
```

```bash
python tools/validate.py --game p01-ballistic --fuzz 100
```

工具总览与参数见 [tools/README.md](tools/README.md)。正式评分口径见 [docs/baseline-and-scoring.md](docs/baseline-and-scoring.md) 与 P01 的 `rating-design.md`。

## 私有内容

隐藏地图、anchor / 基准 / 挑战 bot 的源码**不在本仓库里**，放在仓库外的私有目录，结构为 `<私有根目录>/<game>/private/...`。出题人用下面任一方式把它挂到 `problems/<game>/private`（该路径被 `.gitignore` 忽略）：

```bash
python tools/link_private.py --root <私有根目录>
```

或设置环境变量 `ARENA_PRIVATE` 后直接运行 `python tools/link_private.py`。`--unlink` 只移除链接。

没有私有内容时，只依赖 `public/` 的工具照常可用（公开部分的规则测试、选手包打包、选手自检）；需要隐藏图或 anchor 源码的工具会报找不到文件。

## 文档

从 [docs/README.md](docs/README.md) 开始。

## 许可与致谢

本仓库以 **Apache License 2.0** 发布，版权归 **Aesolumitria-Lab**；见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。

设计灵感来自 [chen-006/meow-ai-arena](https://github.com/chen-006/meow-ai-arena)（MIT）：让模型拿到同一句提示词，在有限时间里写一个 bot，再放进同一个赛场打大量对局。其代码或素材如被单独引入，仍按其原许可处理。

游戏原型的名称与商标归各自权利人；本仓库只描述简化后的 2D 算法对战设计，不含原作素材、代码或美术资源。模型或选手提交的 bot 不属于本仓库，也不自动适用本许可。

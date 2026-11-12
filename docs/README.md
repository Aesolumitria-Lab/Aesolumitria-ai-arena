# 文档索引

通用文档，对所有题目适用。P01 专属文档见 [`problems/p01-ballistic/docs/README.md`](../problems/p01-ballistic/docs/README.md)。其他题目开发中，尚未发布。

## 阅读路径

**第一次看这个项目**：[overview.md](overview.md) → [problem-package-spec.md](problem-package-spec.md) → P01 的 `README.md` 与 `RULES.md`。

**写 bot**：选手包内的 `RULES.md` 与 [contestant-rules.md](contestant-rules.md)；环境要求见 [eval-environment.md](eval-environment.md)。

**出题 / 造引擎**：[overview.md](overview.md) → [problem-package-spec.md](problem-package-spec.md) → [engine-skeleton.md](engine-skeleton.md) → [ai-prompts.md](ai-prompts.md) → [baseline-and-scoring.md](baseline-and-scoring.md) → [acceptance.md](acceptance.md)。

**跑评测 / 维护工具**：[tooling-and-timing.md](tooling-and-timing.md) 与 [`../tools/README.md`](../tools/README.md)。

## 文件一览

| 文件 | 内容 |
|---|---|
| [overview.md](overview.md) | 背景、平台约束、设计原则、题库目标 |
| [problem-package-spec.md](problem-package-spec.md) | 题目包目录、协议、回放、版本规范 |
| [baseline-and-scoring.md](baseline-and-scoring.md) | Starter / Sparring / Anchor 与评分 |
| [tooling-and-timing.md](tooling-and-timing.md) | 工具总览与计时口径 |
| [engine-skeleton.md](engine-skeleton.md) | 引擎的可复用工程结构 |
| [ai-prompts.md](ai-prompts.md) | 提示词设计说明，对应 `prompts/` |
| [acceptance.md](acceptance.md) | 验收清单与发布流程 |
| [known-issues.md](known-issues.md) | 通用已知问题 |
| [contestant-rules.md](contestant-rules.md) | 参赛须知（打包时放入选手包） |
| [eval-environment.md](eval-environment.md) | 选手与评测环境规范 |

## 三个术语

- **Starter**：公开脚手架，只保证合法。
- **Anchor / 基准 / 挑战 bot**：用于评分和校准；源码不公开（P01 公开的是 TIDE 三档二进制，见 P01 文档）。
- **`rules_hash` / `engine_hash`**：规则与引擎的字节级哈希，改动即须重新验收。

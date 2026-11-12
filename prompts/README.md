# prompts/ · 提示词索引

> 六份提示词对应六个角色。不要把它们合成一条“大提示词”，每个角色的输入、约束和验收都不同。

---

## 1. 用哪一份

| 文件 | 角色 | 输入 | 输出 | 什么时候用 |
|---|---|---|---|---|
| `set_problem.md` | 出题人 | 游戏名、原型、赛制、目标步数、信息模型 | 12 节规则草案 + 状态机 + 25 条边界 + 参数表 | 选题之后、写代码之前 |
| `build_game.md` | 引擎开发者 | 冻结的 `RULES.md`、`meta.json`、地图格式、协议样例 | 引擎、裁判、工具、测试、回放 | 规则冻结之后 |
| `build_anchor.md` | 基准开发者 | 引擎 + 规则 + 三档强度目标 | random / greedy / scripted 三个隐藏 anchor | 引擎能跑通之后 |
| `build_starter.md` | 脚手架开发者 | 引擎 + 规则 + 公开 API | `<300` 行、合法、可编译的 Starter | 与 anchor 并行 |
| `contestant_prompt.md` | 参赛模型 | 规则、协议、模板、本地工具 | 一个可提交的 bot | 比赛开始时 |
| `red_team.md` | 红队 | 整个题目包 | P0–P3 问题清单 + 复现命令 | 发布前 |

推荐顺序：

```
set_problem → 人工审规则 → build_game → build_anchor / build_starter → red_team → contestant_prompt
```

## 2. 占位符

`build_game.md`、`build_anchor.md`、`build_starter.md`、`contestant_prompt.md` 里使用 `{{...}}` 占位符，发给 AI 前必须全部替换。常见占位符：

| 占位符 | 含义 | 示例 |
|---|---|---|
| `{{GAME_ID}}` | 题目 id | `pNN-your-game` |
| `{{RULES_PATH}}` | 规则文件路径 | `problems/pNN-your-game/RULES.md` |
| `{{RULES_HASH}}` | 规则 hash | `sha256:...` |
| `{{META_PATH}}` | `meta.json` 路径 | `problems/pNN-your-game/meta.json` |
| `{{PROBLEM_DIR}}` | 题目根目录 | `problems/pNN-your-game` |
| `{{PROTOCOL_SAMPLE}}` | 一段真实协议样例 | `INIT` / `TURN` / `RESULT` |
| `{{MAP_FORMAT}}` | 地图文件格式说明 | ASCII + 图例 |
| `{{TURN_MS}}` | 每步 CPU 预算 | `50` |

替换后跑一遍自检：

```bash
grep -n "{{" prompts/*.md        # 只应剩下“占位符说明”本身
```

## 3. 三条硬规则

1. **先冻结规则，再写代码。** `RULES.md` 没有 12 节、没有 25 条边界、没有参数表，就不要进 `build_game`。
2. **Anchor 必须简单。** 目标是被认真写的 bot 稳定击败；如果 AI 主动写搜索/评估函数，立即打断。
3. **Starter 不是 Anchor。** Starter 公开、`<300` 行、只保证合法；不要把它调强，也不要把 anchor 源码发进选手包。

## 4. 每份提示词的验收

| 文件 | 验收 |
|---|---|
| `set_problem.md` | 12 节齐全；25 条边界有明确答案；参数表可直接填 `meta.json` |
| `build_game.md` | `validate.py` 全绿；1000 局 fuzz 无崩溃；同一 seed 两次 hash 相同 |
| `build_anchor.md` | 三档胜率落在目标区间；p99 < 50% 预算；源码不进 `public/` |
| `build_starter.md` | 编译零警告；对 random 20%–50%；对 greedy 不超过 40% |
| `contestant_prompt.md` | 60 分钟内可完成；提交能编译；无超时/非法动作 |
| `red_team.md` | 输出 P0–P3、复现命令、三行结论；P0/P1 清零才能发布 |

## 5. 相关文档

- 提示词设计原则：[`../docs/ai-prompts.md`](../docs/ai-prompts.md)
- 题目包规范：[`../docs/problem-package-spec.md`](../docs/problem-package-spec.md)
- 基准与评分：[`../docs/baseline-and-scoring.md`](../docs/baseline-and-scoring.md)
- 验收清单：[`../docs/acceptance.md`](../docs/acceptance.md)

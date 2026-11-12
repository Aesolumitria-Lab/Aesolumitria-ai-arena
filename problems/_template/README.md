# 题目模板

> 复制这个目录，改名为 `pXX-your-game`，然后按下面的顺序填写。
> 不要直接在这个目录里实现游戏；它是模板，不是题目。

---

## 1. 复制

```bash
cp -r problems/_template problems/p09-your-game
```

然后修改：

- `meta.json`：`id`、`title`、`family`、`mode`、`max_steps`、`rounds_per_match`、`partial_observability`、`difficulty`、`tags`；
- `README.md`：一句话、核心参数、基准计划、实现风险；
- `RULES.md`：12 节完整规则；
- `public/`：`game.hpp`、`template.cpp`、3 张公开地图；
- `private/anchors/`：random / greedy / scripted；
- `private/maps_hidden/`：3 张隐藏地图；
- `tests/`：黄金、边界、fuzz、协议；
- `tournament/`：赛制配置、结果、回放索引。

## 2. 填写顺序

1. **规则**（`RULES.md`）：先冻结 12 节 + 25 条边界 + 参数表；
2. **引擎**：按 `docs/03-problem-package-spec.md` 和 `prompts/build_game.md`；
3. **基准**：`prompts/build_anchor.md`；
4. **脚手架**：`prompts/build_starter.md`；
5. **测试**：黄金 + 边界 + 1000 局 fuzz + 座位镜像；
6. **回放**：JSONL + `replay.html`；
7. **打包**：`pack.py` 只打 `public/`；
8. **泄漏检查**：`leakcheck.py` 零告警；
9. **红队**：`prompts/red_team.md`；
10. **校准**：anchor BT + 地图/座位偏差。

## 3. 必填字段

`meta.json` 的字段说明见 `docs/03-problem-package-spec.md`。最少要填：

- `id`：小写、连字符、全局唯一；
- `title`：中文名 + 英文名；
- `version`：从 `0.1.0` 开始；
- `family`：原型（cs / worms / splatoon / fortnite / l4d2 / …）；
- `mode`：`1v1` / `1v1-asymmetric` / `2v2` / `ffa`；
- `seats`：2 / 4；
- `turn_ms`：50；
- `first_turn_ms`：2000；
- `max_steps`：单局/单半局上限；
- `rounds_per_match`：一局制填 1；多局制填局数；
- `partial_observability`：true/false；
- `deterministic`：必须 true；
- `difficulty`：1–5；
- `status`：`draft` / `playtest` / `frozen`；
- `rules_hash` / `engine_hash`：冻结后填；
- `anchors`：`["random","greedy","scripted"]`；
- `public_dir` / `private_dir`；
- `hidden_maps` / `public_maps`；
- `tags`：3–6 个。

## 4. Definition of Done

见 `docs/10-roadmap-and-acceptance.md` 第 7 节。8 组条件全过，才算“可以发布”。

## 5. 不要做

- 不要在模板里写具体游戏逻辑；
- 不要把 `private/` 放进 `public/`；
- 不要跳过 25 条边界用例；
- 不要先写 anchor 再写引擎；
- 不要让 starter 强到能打赢 greedy。

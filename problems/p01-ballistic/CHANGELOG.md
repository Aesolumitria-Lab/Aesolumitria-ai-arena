# P01 BALLISTIC 变更记录

当前版本 **2.4.3**（以 [`meta.json`](meta.json) 为准）。规则正文以 [`RULES.md`](RULES.md) 为准，本文按版本归纳变更，内容整理自 RULES.md 末尾的版本表。

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1.0 | 未记录 | 完整规则草案（设计动机见 [`docs/design-notes.md`](docs/design-notes.md)） |
| 1.0.0 | 2026-11-01 | 冻结；协议统一为 INIT/TURN 单行；修正 C1–C6 |
| 1.3.0 | 2026-11-02 | 严格地图出口 / 空腔校验，三类地形，修正整场平局文档；规则主体保持定点同时下令 |
| 1.4.0 | 2026-11-02 | 同时移动改为意图后统一解碰；逐组 M/J 落地；爆炸按轮次奇偶换序；单局全同直接平局；开火后多余动作记非法 |
| 2.2.0 | 2026-11-07 | 公平性修复：争格失败不再记 fault（实测 48 次 FORFEIT 降为 0）；地图生成器换 mirror4（四席等高、等距、严格镜像），旧 pair2x2 单座垄断 106/108 张；4 席赛制改 24 排列 x 2 seed 且每个排列换 seed；协议章节按实现重写；基准 / Starter 章节改成 TIDE 与 template4 的真实样子 |
| 2.3.0 | 2026-11-09 | 计时口径按参考机：`50 ms` 改为 `50 x s`（`s = T_host / T_ref`，标尺是 TIDE-NORMAL 单回合 CPU 中位）。旧口径是绝对 CPU 时间，同一批 144 局在 2 vCPU 云主机上 13 局判负、每档胜率被压低约 5pp；换标定后 144/144 `state_hash` 与参考机全同。墙钟不随 `s` 缩放。见 [`docs/crossplatform-and-host-speed.md`](docs/crossplatform-and-host-speed.md) |
| 2.4.0 | 2026-11-10 | 地图加类型轴：mirror4 上叠 `openness / cover / elevation` 三个正交轴共 27 种组合，公开 / 隐藏各 108 张每种 4 张，类型码写进 `manifest.json` 的 `type_code`，`validate4` 加 7 条硬检查。只动地图，不动引擎、不改任何规则数值。见 [`docs/map-types.md`](docs/map-types.md) |
| 2.4.2 | 2026-11-10 | 图集内容去重 + 文档对齐：`open|bare|*` 三类是 seed 无关的纯函数（108 张公开图只有 97 个不同内容，隐藏图 25/108 与公开图逐字节相同）。加 `surface_texture()` + 内容 sha256 去重 + `--avoid` 后：公开 108 唯一 / 隐藏 108 唯一 / 公开与隐藏零重叠。同一轮把 RULES 与引擎的 9 处硬矛盾按「以引擎为准」改文档。引擎零改动 |
| 2.4.3 | 2026-11-10 | 删 `openness` 轴 + 并发补偿 + 贡献指南：`openness` 实测只有 1.4pp（`cover` 27.0 / `elevation` 25.3），27 种组合降到 9 种，每种 12 张；判罚阈值加并发补偿 `c(jobs) = min(1.75, 1+0.03 x (jobs-1))`，`turn_budget_ms = 50 x s x c(jobs)`；新增贡献指南。引擎仍零改动 |

说明：表中 1.1 / 1.2 / 2.0 / 2.1 等中间版本在 RULES.md 的版本表里没有单独条目，这里不补写。

## 包修订

规则版本不变时，选手包的重新打包记在这里（对应 `meta.json` 的 `package_revision`）。

| 包修订 | 规则版本 | 变更 |
|---|---|---|
| 2026-11-12 | 2.4.3 | 仓库迁移并更名为 Aesolumitria-ai-arena：`RULES.md`、`README.md` 里的文档引用改为新文件名，`NOTICE` 改项目名。规则、引擎、地图、bot 二进制均未变（`engine_hash`、`arena_hash` 不变，`rules_hash` 因文案改动而变）。 |

# P01 文档索引

P01（BALLISTIC · 弹道虫）是 2–4 席回合制、全定点、可破坏地形的弹道对战题。本目录是出题侧的设计与审计资料。

> 现行规则只看 [`../RULES.md`](../RULES.md)；版本与 hash 以 [`../meta.json`](../meta.json) 为准；版本历史见 [`../CHANGELOG.md`](../CHANGELOG.md)。
> 文中 S01–S09、T01–T12 是内部试跑用的模型提交 bot 的匿名代号，提交本身不随仓库发布。隐藏图由不公开的私有 seed 生成。
> 评测过程记录（分析脚本与输出存档、部分回放集）未随仓库发布，文中相关读数以正文为准。

## 阅读路径

第一次接触：

1. [`../README.md`](../README.md)：题目概览与选手包内容
2. [`../RULES.md`](../RULES.md)：权威规则
3. [`../BOT_LADDER.md`](../BOT_LADDER.md)：基准与挑战 bot 梯队；[`../baselines/`](../baselines/README.md) 里有 `random4` 源码和 TIDE 设计说明
4. [`design-notes.md`](design-notes.md)：设计动机
5. [`implementation-report.md`](implementation-report.md)：引擎是怎么实现的

想知道为什么这样设计（公平性与地图）：

6. [`fairness-audit.md`](fairness-audit.md)：座位、赛制、补偿实验、事件流评分审计
7. [`map-types.md`](map-types.md)：地图类型轴与读数
8. [`sky-design.md`](sky-design.md)：「挡天空」实验（结论：不并入）

想知道计时、跨平台与评分：

9. [`crossplatform-and-host-speed.md`](crossplatform-and-host-speed.md)：跨平台确定性与宿主速度
10. [`rating-design.md`](rating-design.md)：评分体系设计
11. [`swing-design.md`](swing-design.md)：操作质量层设计

状态与证据：

12. [`known-issues.md`](known-issues.md)：P01 已知问题
13. [`../CONDITIONS.md`](../CONDITIONS.md)：各版本比赛条件与验收读数
14. [`data/`](data/)：各探针脚本产出的 JSON 读数

通用文档（跨题）：[`../../../docs/README.md`](../../../docs/README.md)。

## 文件一览

| 文件 | 内容 |
|---|---|
| `design-notes.md` | v0.1 规则设计稿中仍有价值的设计动机 |
| `implementation-report.md` | v1.0 实现报告（定点物理、地图生成器、验收） |
| `fairness-audit.md` | v2.2.0 公平性审计及其后续补充实验 |
| `crossplatform-and-host-speed.md` | 跨平台一致性与宿主速度审计 |
| `map-types.md` | 地图类型轴与「分类生存」读数（含落地摘要） |
| `sky-design.md` | 「有设计地挡天空」实验记录 |
| `rating-design.md` | 评分体系设计 |
| `swing-design.md` | 胜率排名层 + 操作质量层设计 |
| `known-issues.md` | P01 已知问题 |
| `data/` | 探针 JSON 数据 |

# 验收清单与发布流程

## 1. 一道题“可运行”的定义

| # | 条件 | 验收方式 |
|---|---|---|
| D1 | 规则冻结：12 节 + 编号结算顺序 + 可判定裁定 | 人工审 + `RULES.md` 结构 |
| D2 | 引擎确定性：无随机、无浮点、无系统时间 | 代码审 + 同输入两次 hash 一致 |
| D3 | 引擎负责单局规则；裁判负责进程、计时、判罚与场级结算 | 不重复实现；场级映射另测 |
| D4 | `state_hash` 覆盖所有影响后续结算的字段 | 回放重算一致 |
| D5 | Starter 能编译、跑完整局、不违规 | 本地裁判跑通 |
| D6 | 多档基准，梯度可测 | `anchor_audit` / `rating` 等 |
| D7 | 边界测试 + fuzz 无崩溃 | `validate.py` |
| D8 | `pack` 两次 hash 一致；`leakcheck` 无失败 | 两次打包比对 |
| D9 | 公平性：确定性同名 bot 在完整换座下应接近平局 | 同名测试 |
| D10 | 文档齐全：RULES / CONDITIONS / README / prompt / meta | `acceptance` 的 docs 项 |

`acceptance.py` 只覆盖其中一部分，它的 PASS 不等于 D1–D10 全部完成。

## 2. 工程验收（一条命令）

```bash
python tools/acceptance.py --game p01-ballistic
```

包含 docs / meta / maps / validate / validate4 / pack / leak / package 八项；加 `--ranking` 另验相邻基准档位。`meta` 项校验 `rules_hash`、`engine_hash`、`arena_hash`（字段可选，写了就必须对）。

提交前顺序不能换：先 `refresh_meta.py` 对齐 hash，再 `pack.py`，再 `acceptance.py`。顺序颠倒时 `meta` 会因 hash 过期而失败，多数不是代码坏了，而是改了文件忘了刷 meta。详见 [`../CONTRIBUTING.md`](../CONTRIBUTING.md)。

## 3. 单题发布前清单

规则：

- [ ] `RULES.md` 12 节完整，参数表完整，所有“同时”有结算顺序，随机可追溯到 seed；
- [ ] `rules_hash` 已写入 `meta.json`；

引擎：

- [ ] C++17 零警告；整数/定点；固定 seed PRNG；单线程；固定遍历顺序；每步 flush；
- [ ] 非法/超时/崩溃处理符合规则，累计 3 次 fault 判负；状态哈希稳定；

协议与信息：

- [ ] 公开头文件只暴露过滤后的状态；无隐藏单位/冷却/seed；
- [ ] 输出格式错误按非法处理；

测试：

- [ ] golden、边界、fuzz、座位镜像、同 seed hash 一致、协议模糊、回放重算；

公开材料：

- [ ] Starter、SDK 头、公开地图、`prompt.md`、`README.md`；
- [ ] 包内无私有路径、基准源码、隐藏 seed、绝对路径；

隐藏材料（仓库外）：

- [ ] 多档基准、隐藏地图、隐藏 seed、校准报告；

工具与回放：

- [ ] `bench` / `validate` / `pack` / `leakcheck` 可用，回放可逐帧重算；

平衡：

- [ ] 基准梯度可区分；地图/座位偏差可接受；同名内战接近平局；不存在“不进攻也能赢”的主流策略。

各项的具体阈值由各题在 `CONDITIONS.md` 中给出；通用文档不规定统一数字。

## 4. 赛前 / 赛中 / 赛后

赛前：规则冻结并写入 hash；基准校准完成；选手包 `pack.py` 生成、`verify_package.py` 与 `leakcheck.py` 通过；在干净机器（无缓存、无私有目录）上编译并跑通 Starter；用参赛提示词让一个全新 AI 试写 bot；回放能在浏览器打开；赛事规则、计分、申诉流程写清楚。

赛中：记录每场的 seed、地图、引擎 hash、双方提交 hash；记录超时/非法/崩溃；异常对局标记并人工复核；不临时改规则，如需 hotfix 则 bump 版本并重跑受影响对局。

赛后：导出回放和结果；归档旧引擎与规则 hash；统计基准漂移和地图/座位偏差；收集选手反馈；决定下一赛季是否换图、换基准。

## 5. 版本与维护

规则变更必须：更新 `RULES.md` → 重算 `rules_hash` → 重跑 golden 与校准 → 在题目 `CHANGELOG.md`（P01 见 [`../problems/p01-ballistic/CHANGELOG.md`](../problems/p01-ballistic/CHANGELOG.md)）写清原因与影响 → 旧回放用旧引擎重放。

三种改动影响面不同：

| 改的是 | 变的 hash | 要重跑 |
|---|---|---|
| 规则 `RULES.md` | `rules_hash` | bump version → refresh_meta → validate → acceptance |
| 引擎 / SDK 头 | `engine_hash`（及全部 `state_hash`） | refresh_meta → crosscheck → 回放重生成；旧回放失效 |
| 地图 | 每张图 sha256 | 重生成 → 题目的地图校验 |

基准维护：每个赛季重新校准；选手整体变强时不直接加强旧基准，必要时新增更高一档；基准源码永不进公开包，二进制要版本化。

## 6. 风险登记（通用）

| 风险 | 早期信号 | 对策 |
|---|---|---|
| 基准过强/过弱 | 选手几乎全败/全胜 | 重新校准，不要硬编码地图 |
| 地图过拟合 | 公开图表现远好于隐藏图 | 公开/隐藏分离且零重叠 |
| 非确定性 | 同 seed hash 不一致 | 定点、固定遍历、禁时间/线程 |
| 信息泄漏 | 公开包出现私有路径 | `leakcheck` + 红队 + 人工复核 |
| 拖延/蹲坑 | 对局打满、观赛无聊 | 硬时钟 |
| 超时 | p95 接近预算 | 降候选数、增量模拟 |
| 规则歧义 | 选手与裁判理解不同 | 裁定条目 + 冻结 |
| 回放对不上 | 排名争议 | JSONL + `state_hash` + 旧引擎归档 |
| 换行符导致 hash 漂移 | 干净 clone 上 acceptance 失败 | `.gitattributes` 钉 LF |

## 7. 一句话验收

规则能手算，引擎能复现，基准能被打败，公开包不泄漏，回放能看懂，超时不爆炸。

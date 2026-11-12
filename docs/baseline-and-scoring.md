# 基准与评分

## 1. 三层结构：Starter / Sparring / Anchor

把“基准程序”直接发给选手有风险：选手会抄、会针对。更稳的做法是分三层。

| 层 | 公开？ | 作用 | 强度预期 |
|---|---|---|---|
| Starter | 公开，必须给 | 证明协议跑得通、起一个能编译的脚手架 | 略高于随机，明显低于入门基准 |
| Sparring | 可选公开 | 给选手一个“有挑战但打得过”的本地对手 | 强于 Starter，弱于隐藏基准；须声明“不是评分基准” |
| Anchor（基准 / 挑战 bot） | 源码隐藏，只给二进制或服务 | 评分、校准、检测地图/座位偏差 | 多档，形成单调梯度 |

Starter 不该包含强策略、anchor 战术或隐藏地图信息，建议 <300 行。**给选手看的是 Starter，不是 Anchor。**

P01 的实际做法：公开 TIDE 三档（easy / normal / hard）作为及格线基准，只发二进制；CHIMERA / PSEUDO / TYPHON 作为挑战 bot，只发二进制，不要求击败；`random4` 是下限。它们的定位与梯队见 [`problems/p01-ballistic/README.md`](../problems/p01-ballistic/README.md)。隐藏评分用的地图与基准源码不在本仓库。

## 2. 基准的设计原则

- 简单、确定、可解释；不读隐藏状态，不读对手指令，不写文件、不联网；
- 不针对某张公开地图写死坐标；
- 不超时：超时时先查引擎/helper，不要放宽判罚；
- 要有梯度：相邻档位应当可区分，判断档位看**胜负**，不要看各题自己的分差（`tools/anchor_audit.py` 即按胜负判单调）；
- 目标是“认真写的选手能稳定击败入门档，但未必稳赢高档”。如果所有选手都打不过某档，它可能太强；如果随机 bot 也能赢某档，它可能有 bug 或游戏随机性过大。

隐藏基准的内部算法不在公开文档中描述。

## 3. 封闭分发

- `tools/seal_anchors.py`：把私有目录里的基准源码编译成二进制 + `manifest.json`（含 hash、平台、编译器），不复制源码；
- `tools/pack_eval.py`：生成评测包（基准二进制 + 隐藏地图 + 元数据）；
- `bench.py`、`calibrate.py`、`rating.py`、`profile_turn.py`、`challenge.py`、`tournament.py` 支持 `--anchors-bin`，解析顺序为二进制优先、源码兜底；
- 评测环境只挂载解压后的评测包，不挂载私有源码。

## 4. 校准

在选手进场前先让基准互打，检查：胜率是否呈梯度、地图/座位偏差、平均步数、超时/非法是否为 0、同 seed 是否可复现。再用 Bradley–Terry 拟合强度（`rating.py`、`calibrate.py`），并用 bootstrap 给置信区间。

隐藏地图要求：与公开图同一生成器、不同 seed；不出现在 `public/`、选手包、README、示例中；与公开图内容零重叠；隐藏图的 seed 不公开。

## 5. 评分

### 5.1 两席（1v1）

Bradley–Terry（MM 迭代）+ 每对一局虚拟平局，固定一个基准为 0，刻度 `400 × log10(strength / anchor)`；bootstrap 给 95% CI（`tools/rating.py`，`tournament.py` 复用同一实现）。平局按 0.5。不要用简单胜率排名；不同题的强度不可直接比较（各题 margin 单位不同）。

### 5.2 多席（FFA）

不要把 1v1 Elo 直接套到多人局。做法是：座位完整轮换，保证每个 bot 坐过每个座位；报告原始胜率与区间，必要时用名次信息；用多张隐藏图降低方差。P01 的 4 席评分口径与设计取舍见 [`problems/p01-ballistic/docs/rating-design.md`](../problems/p01-ballistic/docs/rating-design.md)；4 席工具为 `rating4.py` / `ffa_rating.py`（目前是原始胜率口径，正式 BT 与 4 席 tournament 未接入）。

### 5.3 平局与 tiebreak

每题必须有确定的 tiebreak，按顺序比较，写进 `RULES.md`；要可复现，不鼓励刷数据，不奖励拖时间。

## 6. 局数与置信度

单局方差大（地图、座位、对手风格）。报告强度时带 CI；CI 重叠时不宣称“一定更强”。样本多分给强度相近的对手；对基准的样本不必平均分。具体局数视方差而定，没有放之四海皆准的数字。

## 7. 反过拟合

隐藏地图 + 隐藏 seed；多档基准而非单一基准；换边/换座并统计各座位胜率；规则版本冻结，赛季中不改基准强度，必须修 bug 时记入 `CONDITIONS.md` 并说明是否重赛。

## 8. 失败处理

- 单步超时/非法：本步无动作，记 fault；累计 3 次判负；崩溃判负；
- 引擎 bug 影响结果：停止正式赛，保留 seed + 动作 + `state_hash`，修复后 bump `engine_hash`，在 `CONDITIONS.md` 记录事故与重赛范围；
- 基准异常：先查引擎/helper，必要时修基准并重新校准，说明旧成绩是否重算。

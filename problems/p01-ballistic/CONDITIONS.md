# P01 v2.4.3 · 本轮比赛条件与证据

更新：2026-11-10。本文件下方的 v1.4.0 / v2.0.0 / v2.1.0 / v2.2.0 记录是历史证据；
当前版本 **2.4.3** 的规则、引擎、地图与协议指纹一律以 `meta.json` 为准
（`rules_hash` / `engine_hash` / `arena_hash` 三个都在那里，且 `acceptance` 三项全查）。

## v2.4.3 变更（2026-11-10）

**删 openness 轴 + 并发补偿 + 贡献指南。引擎仍零改动。**

- **删 `openness` 轴**：27 种组合 → **9 种**，公开/隐藏各 108 张 → **每种 12 张**。
  理由：它在专精探针上只有 1.4pp（`cover` 27.0 / `elevation` 25.3），挡得住地面行进、挡不住高抛。
  地图整批重生成，公开 108 唯一 / 隐藏 108 唯一 / 并集 216 唯一 / **公开∩隐藏 = 0**。
- **并发补偿进判罚公式**：`turn_budget_ms(jobs) = 50 × s × c(jobs)`，
  `c = min(1.75, 1 + 0.03×(jobs-1))`。c(20)=1.57、c(24)=1.69、c(≥48)=1.75。
  透传 `judge4` / `selfcheck4` / `ffa4` / `rating4` / `anchor_audit4`，`validate4` 4 条断言。
- **`arena_hash` 进了验收**：`acceptance` 的 `meta` 项现在查三个 hash；顺手发现 P08 的 arena_hash
  本来就是过期的、P06/P07 根本没写 —— 三题已刷新。
- 新增 `CONTRIBUTING.md`；移除通用文档里一段跑不起来的 GitHub Actions 示例（仓库不带 workflow）。

**本轮冻结口径全套读数（2026-11-10）**：

| 项 | 读数 |
|---|---|
| `validate`（golden + `--fuzz 200`） | **59/59 pass** |
| `validate4` | **671/671 pass** |
| `acceptance --game p01-ballistic` | **8/8 PASS** |
| `acceptance --game all` | **4/4 PASS** |
| `pack` 可复现 | 两次打包 sha256 一致（见 acceptance 的 pack 项） |
| `p01_mapcheck`（216 张 4 席图） | Windows vs Linux **216/216 开局 hash + grid sha256 全同** |
| `crosscheck --game p01-ballistic`（**两平台**） | Windows 与 Linux 主机 各跑 54 用例 / 108 hash，`--compare` **PASS 54/54、108 个 hash 完全一致**。四份原始读数存在 `tests/evidence/v2.4.3/`（`crosscheck-win.json` / `crosscheck-linux.json` / `mapcheck-win.json` / `mapcheck-linux.json`） |

## v2.4.2 变更（2026-11-10）

**只重生成图集 + 修文档/工具，不动引擎、不改任何协议与判罚语义。**

- **地图内容去重**：生成器里 `openness=open` 与 `cover=bare` 两档不消耗随机数，
  于是 `open|bare|*` 三类是 seed 无关的纯函数 —— 实测每种 4 张 sha256 完全相同，
  108 张公开图只有 97 个不同内容，**隐藏图 25/108 与公开图逐字节相同**。
  加 `surface_texture()`（只改形不改类）+ 内容 sha256 去重 + `--avoid` 跨 split 去重后：
  公开 108 唯一 / 隐藏 108 唯一 / 并集 216 唯一 / **公开∩隐藏 = 0**。
- **规则文档与实现对齐**（全部改文档，引擎零改动）：初速度公式、定点类型、STATE 字段数、
  投射物优先序、`blocked` 计数、`LAST` 行、墙钟兜底、赛制口径、标题版本 —— 明细见
  `docs/known-issues.md` 的「2026-11-10 P01 v2.4.2 评审修复」一节。
- **赛制 seed 接线**：`judge4` / `ffa4` / `rating4` 曾把 `schedule()` 的绝对 seed 下标丢成 `_`，
  一张图 48 局只用了 2 个 seed；现已修正，`validate4` 断言 48 局 48 个 seed。
- **选手包**：删除 2026-11-07 那份 2.1.0 旧契约（无 oracle / 无 bin/linux / judge 未改名 / 带 2 席旧图），
  按当前树重打；`arena_hash` 不再为 null。

**本轮验收读数**：`acceptance --game p01-ballistic` **8/8 PASS**
（`validate 55/55`、`validate4 667/667`、pack/leak/package 全 PASS）；
`acceptance --game all` **4/4 PASS**。跨平台：`problems/p01-ballistic/tools/p01_mapcheck.py` 在 Windows 与
Linux 主机 上各跑 216 张 4 席图，开局 `state_hash` 与 grid sha256 **216/216 全同**（`docs/crossplatform-and-host-speed.md` §0.5）。
及格线面板 `problems/p01-ballistic/tools/p01_passline.py`（3×TIDE-NORMAL）读数见 `docs/known-issues.md`。

**仍未解决**（不得当成已修）：见 `docs/known-issues.md` 第 1 节，其中与本轮相关的有 ——
`difficulty` 标定方法学、整局跨平台对账仍引用旧图集读数等（并发判罚乘子与 `openness` 轴已在 v2.4.3 处理）。

完整公平性读数见 `docs/fairness-audit.md`（`problems/p01-ballistic/tools/p01_fairness_audit.py` / `problems/p01-ballistic/tools/p01_seeddev.py` 复现）。

## v2.2.0 变更（2026-11-07）

| # | 变更 | 原因（实测） |
|---|---|---|
| 1 | 争格/互换失败**不再记 fault**（RULES 6.4、第 32 条） | `public4-12`/`public4-24` 用 TIDE NORMAL 跑 24 排列 → 24/24 全 FORFEIT（144 fault），而两边动作全合法。新语义下 48 → 0 |
| 2 | 地图生成器换 **mirror4**（`problems/p01-ballistic/tools/genmap_p01_fair.py`） | 旧 pair2x2 的 108 张图 0 张垂直镜像；内席头顶永压顶棚（19%/25% vs 2%/4%），内席仰角只剩 2–5 格；4 个相同 bot 下 106/108 张图单座垄断 |
| 3 | 4 席赛制 = **24 排列 × 2 seed，每排列换 seed**（RULES 11.2.2） | 单 seed = 同一套风程序，24 个旋转是同一局换座位名字；"多于一个座位赢过"的图 0/12 → 9–10/12 |
| 4 | 协议章节按实现重写（RULES 13） | 旧 RULES 的应答格式带轮次前缀，照它写的 bot 3 回合判负；只去掉前缀 → 38 轮 0 fault |
| 5 | `judge4` / `selfcheck4` / `rating4` 加 `--jobs`；判超时只看 CPU | 48 并发出现误杀（bot CPU 仅 15ms，wall 上限先掐）；576 局 163.7s → 13.2s |
| 6 | 新增 `public/oracle/`（只读轨迹对账） | 选手没有验证复刻的手段，只能靠近似瞄准 |

## v2.2.0 工程读数

> ⚠️ 本节是 **v2.2.0 当时的读数**，作为历史证据保留，不随版本滚动更新。
> 需要当前版本的验收读数时，以 `meta.json`、`docs/known-issues.md` 与最新一次 `acceptance` 日志为准。

- `tests/validate.py` **55/55**（新增 B24 争格不记 fault、B24a 真非法仍记 fault）—— 这一条到 v2.4.x 仍成立
- `tests/validate4.py` **655/655**（新增：全部地图严格镜像、赛制 24×2、邻居分布 spread=0）—— **这是 v2.2.0 当时的读数**；
  后续用例只增不减：2026-11-10 08:14 落盘的 （v2.4 验收日志，未随仓库发布）里读到 **662/662 PASS**。
  这里**不回头改旧数** —— 每条读数都要带自己的版本。
  **本轮 v2.4.3 的现读数以文件顶部那张表为准**：
  `validate 55/55`（不带 fuzz）/ **`59/59`（`--fuzz 200`）** / **`validate4 671/671`** /
  四题 `acceptance --game all` **4/4 PASS**。别拿不同版本的读数互相顶。
- `acceptance --game p01-ballistic`：docs/meta/maps/validate/validate4/pack/leak/package **全 PASS**
- `acceptance --game all`：**4/4 题 PASS**
- `replay4 verify`：4 份 4 席回放，逐帧 hash / state / result **0 差异**
- 216 张图（108 公开 + 108 隐藏）全部通过 strict `LOAD ... 15`

## v2.2.0 基准读数（12 张公开图 × 24 排列 × 2 seed = 576 局）

| bot | 胜率 |
|---|---|
| TIDE-HARD | 44.79% |
| TIDE-NORMAL | 42.71% |
| TIDE-EASY | 12.33% |
| random4 | 0.17% |

阶梯单调成立。4 个**完全相同**的 bot：bot 级份额 25.0 / 25.0 / 25.0 / 25.0（旋转精确的必然结果），
座次份额 29.2 / 25.0 / 8.3 / 37.5（座次本身不等价，这是引擎标签顺序，不是地图）。

**未验**：跨平台确定性（K01）本轮未重跑 WSL 比对；引擎改动只碰 `G.bad[]`（不进 hash），
新老引擎同脚本 hash 序列 24/24 相同，但按规矩仍标"未验"。

---

# P01 v2.1.0 · 历史比赛条件与证据

更新：2026-11-06。v2.1.0 增加 TIDE 三档公开基准与 CHIMERA/PSEUDO/TYPHON 隐藏挑战，旧 `anchors4` 退役为 legacy。

## v2.1.0 bot 梯队（2026-11-06）

- **公开基准（目标）**：TIDE EASY/NORMAL/HARD（`public/baselines/tide/tide.cpp`，`-DTIDE_PROFILE=0/1/2`）。目标：稳定击败 TIDE，至少 NORMAL；HARD 是拉伸目标。
- **隐藏挑战（不要求击败）**：CHIMERA/PSEUDO/TYPHON（`private/challenges4/`）。它们是抗压/上限/惊吓，不是及格线；正式评分用隐藏图和这组挑战 bot。
- **legacy**：旧 `private/anchors4/` 退役；旧 anchor 梯度只作 v2.0 历史证据，不再作为当前评分门槛。
- 难度梯度：`TIDE-EASY < TIDE-NORMAL < TIDE-HARD ≪ CHIMERA < PSEUDO < TYPHON`；HARD 到 CHIMERA 是断档。
- 新包验证（2026-11-06）：`tests/validate4.py` 651/651 PASS（含 4 座真实对局，`ok`、0 fault）。

## v2.0.0 实现范围

- 引擎最大 4 席，每席 3 虫；`active_mask` 选择参战席，inactive 席跳过且不计分。
- 2 席兼容模式保留 v1.4.0 的地图格式、状态行和 hash；4 席模式使用席号 `0`–`3` 和 12 虫状态行。
- 2 席地图继续镜像；4 席地图不要求镜像，由比赛层座位轮换平衡。
- 2 bots 两局换座；4 bots 4! = 24 局全座位排列。引擎只跑一局，不负责赛制。
- 2 席旧图与旧回放仍是 v1.4.0 兼容证据；4 席新图/新证据另行记录，不混算。
- v2 工程验收：2 席 validate 58/58，4 席 validate4 651/651；4 席公开图 108 张（seed 1000–1107），隐藏图 108 张（由不公开的私有 seed 生成），pair2x2。
- 4 席公开图用 108 图 × 1 seed × 24 全排列（2592 局/人）批量复测；地图族为 pair2x2。单图 seed 只用于生成地形，不是多 seed 对局。
- 早期 80 场循环轮换冒烟：0 fault；每 bot 各 20 胜；按座位统计 seat3=28、seat1=24、seat0=16、seat2=12，说明轮换能平衡 bot，但不能替代地图偏差审计。v2 正式取样已改为 4! 全排列。
- 4 席 anchor 梯度（2592 局/人，108 图 pair2x2，anchor 已去掉非法移动分支）：strong4 66.7% > scripted4 25.9% > greedy4 7.2% > random4 0.2%；相邻 +40.9pp / +18.7pp / +7.0pp，四档均拉开 min-gap。（v2.1.0 起旧 anchors4 退役为 legacy；仅历史证据）
- 4 席评测包可生成：`pack_eval.py --game p01-ballistic --seats 4`；4 席正式 BT 排名尚未接入，`rating4.py` 输出原始胜率/Wilson 区间。
- 4 席真实回放：judge4 全排列 24 场写入 JSONL，`replay4.py verify` 逐帧 hash/状态/结果 0 差异；`replay4.py html` 生成单文件查看器（12 虫、GRID、座位 HP、回合播放）。
- 正式 bot 身份：`entries/<id>/meta.json + bot.cpp`，ID 为 `model@harness#run`；通用工具 `tools/bot_registry.py validate` 校验重复/hash/路径；judge4 `--entries` 写入 `bot_ids/bot_labels/bot_meta`。
- 7 维席位分数选图实验（24 候选→12，1440 局/组）在 5 seed 上略缩窄座位偏差，但 10 seed 合并后不成立；未采用，保留当前 pair2x2。
- 地图生成器：`python problems/p01-ballistic/tools/genmap_p01_mp.py --out <dir> --seed <s> --count <n> --prefix <p>`；公开/隐藏候选 seed 段分开（隐藏段不公开），manifest 记录实际 seed。
- 4 席座位合计（2592 局，108 图 pair2x2）：S0 26.7%、S1 20.8%、S2 24.1%、S3 28.4%。S3 仍有约 +3.4pp 残余。

---

# P01 v1.4.0 · 历史比赛条件与证据

## 实现范围

严格加载新增侧出口、空气连通区域≥20、空气占比25%–60%、实际移动/跳跃后可存活检查；生成器完整验证镜像并将候选送入引擎LOAD。
三类地形：terraces/ridge/basin，公开图 seed 1/2/3，隐藏图由不公开的私有 seed 生成。
scripted修复移动后坐标参与自伤估算，原地候选保留greedy精度；不改变引擎战斗结算。
judge的构建缓存纳入头文件时间，避免SDK更新后沿用旧bot。

v1.4.0只修规则执行一致性，不改武器数值、弹道表或地图：同时移动先声明意图再统一解碰；每组M/J后立即落地；爆炸按轮次奇偶换序；单局全同直接平局；开火后多余动作记fault。裁判对单局平局双方各记0.5分，seed不再参与任何胜负裁定。

## 可复现结果

报告 JSON 为构建/运行产物，未随仓库发布。

| 命令或实验 | v1.4.0结果 | 报告 |
|---|---|---|
| Windows `validate.py --fuzz 100` | 56/56通过 | p01-v140-validation.json |
| WSL `validate.py --fuzz 100` | 56/56通过 | p01-v140-linux-validation.json |
| 公开图 template vs random | 8胜4负，PASS | p01-v140-anchor.json |
| 公开图 greedy vs template | 12胜0负，PASS | p01-v140-anchor.json |
| 公开图 scripted vs greedy | 10胜2负，PASS | p01-v140-anchor.json |
| 隐藏图 scripted vs greedy 12场 | 8胜4负 | p01-v140-hidden.json |
| greedy同名6场 | 6平，分差0 | p01-v140-self.json |
| 真实回放重算 | 80帧，hash/结果差异均为0 | p01-v140-replay-check.json |
| Windows/WSL逐状态比对 | 54用例、108hash完全一致 | p01-v140-win.json / p01-v140-linux.json |
| `g++ -Wall -Wextra -O2` | 零警告 | 主审命令输出 |

新增回归覆盖同时让位、争格、互换、连续移动逐段落地、飞行与出生即爆的奇偶优先序、双方同灭平局、40轮全同平局和开火后多余动作fault。测试在旧引擎上先复现6项失败，再修实现；不是只按新实现反写期望。

六张当前地图均通过严格LOAD；封闭出生/小空腔/必死出口仍为构造拒绝测试。档位样本为公开图12场/档、隐藏图单独12场，不混算公开与隐藏证据，也不声称策略最优。

工程总验收六项全部PASS（`p01-v140-acceptance.json`）：56/56、重复打包一致、泄漏0 fail/0 warn、manifest通过。13文件选手包解压至无缓存目录后，template自战两局完整结束，points=[1.0,1.0]、winner=-1、零fault（`p01-v140-clean-smoke.json`）。旧版本跨平台与回放结果不继承为v1.4.0证据。

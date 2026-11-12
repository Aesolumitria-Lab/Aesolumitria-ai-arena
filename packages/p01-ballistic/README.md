# P01 · BALLISTIC · 弹道对战

状态：**v2.4.3 / playtest**。96×54 可破坏地形，4 席 FFA、每席 3 条虫；2 席为历史兼容模式。
`RULES.md` 标题里的 v2.2.0 是规则冻结线；v2.4.x 是地图类型/打包版本，v2.4.2 重生成图集（去重 + 与隐藏图零重叠），协议不变；v2.4.3 加入并发补偿 c(jobs)，判罚阈值见 RULES §15.2。
4 席正式评测按 **24 个座位排列 × 2 个 seed** 跑（见 `RULES.md` 11.2.2）；正式评分用 **108 张隐藏图（不随包分发）+ 挑战 bot**。规则细节见 `RULES.md`。

## bot 梯队

难度从左到右，`≪` 表示断档：

```text
random4 < TIDE-EASY < TIDE-NORMAL < TIDE-HARD ≪ CHIMERA < PSEUDO < TYPHON
```

- **公开基准（及格线）**：TIDE 三档。Windows 在 `public/baselines/bin/tide_{easy,normal,hard}.exe`；
  Linux 在 `public/baselines/bin/linux/tide_{easy,normal,hard}`（无扩展名）。
  目标：稳定击败 **TIDE-NORMAL**；HARD 是拉伸目标。
- **挑战（不要求击败）**：CHIMERA / PSEUDO / TYPHON，二进制在 `public/challenges/bin/`；
  Linux 版在同目录的 `linux/` 下。它们是抗压/上限/惊吓，不是及格线，源码不提供。
- `random4` 是下限（也会崩溃/乱开火，用来查协议鲁棒性）；平台路径同上。
- 旧 anchors4 一组（greedy4 / scripted4 / strong4）已退役为 legacy，不再是门槛。

> **对手强度**：正式评测的对手是其他顶级模型/代理的提交，强度未知。请认真对待，
> 不要只满足于编译通过或打过 template。

## 选手包内容（4 席）

| 文件 | 用途 |
|---|---|
| `RULES.md` | **唯一权威规则**（协议在第 13 节） |
| `public/arena4.hpp` | 4 席协议解析、地形查询、近似瞄准 |
| `public/template4.cpp` | starter（唯一的 starter 源码） |
| `public/engine/engine.cpp` | 引擎源码（**能当库读，但请先看 `public/oracle/README.md`**） |
| `public/engine/trig.hpp` | 引擎三角函数表（`engine.cpp` / `oracle` 的编译依赖） |
| `public/oracle/` | 只读轨迹对账工具：验你自己的复刻，不替你写 bot |
| `public/judge/judge4.py` | 本地 4 席裁判 / 回放写入（`--jobs` 并发）；默认 bots 为 TIDE 四档（Linux 自动选 `bin/linux/`），也可显式 `--bots`；选手自检优先用 `public/tools/selfcheck4.py` |
| `public/judge/judge2.py` | `judge4` 的 2 席 legacy 依赖（编译与 `ProcessCPU` 封装）；选手不要照它写 bot |
| `public/tools/selfcheck4.py` | 本地自检；默认 TIDE NORMAL |
| `public/tools/timebudget.py` | 可选计时工具 |
| `public/baselines/bin/*.exe` · `bin/linux/*` | TIDE 三档 + random4（仅二进制；Windows/Linux） |
| `public/challenges/bin/*.exe` · `bin/linux/*` | CHIMERA / PSEUDO / TYPHON（仅二进制，不要求击败） |
| `public/maps4/` | 108 张公开 4 席图（严格镜像对称） |

隐藏图不随选手包分发。`public/maps/`（2 席旧图）与 `arena.hpp` 是历史产物，**不进包**。

## 使用

```text
g++ -O2 -std=c++17 -I public public/template4.cpp -o mybot

python public/tools/selfcheck4.py --bot mybot.cpp --baseline tide-normal --maps 2 --jobs 8
```

Linux 上 `selfcheck4.py` 会自动使用 `bin/linux/` 下的预编译基准/挑战 bot；
engine 和你自己的 bot 由本机 `g++` 现场编译。Windows 与 Linux 的基准二进制
来自同一份源码，规则/种子/地图一致。

## 答题时间

- 正式答题 **90 分钟墙钟**；比赛跑局时间另计。
- **没有 token 预算上限**；可以一直做，必要时用 goal 接续。
- 第 **75 分钟**时用户会发一条提醒消息；如果没收到，说明还没到 75 分钟。
- 计时首先由选手自己负责：有 harness 计时就用；没有就开工时记录系统时间，每阶段查一次，90 分钟停。
  `timebudget.py` 只是可选；提交的 bot 不得读取时间。

## 规则与地图

- 4 席 FFA，每席 3 条虫、每条 100 HP；每轮每个 active 席同时激活 1 条活虫。
- 每条虫每轮最多 3 AP；40 轮结束，或只剩一个 active 席存活时提前结束。
- 水从第 30 轮开始涨（每 2 轮 1 行）；落水即死。
- 武器：ROCKET（无限）/ GRENADE（2 发，会弹）/ DRILL（2 发，向下挖）。
- 地图：**mirror4 构造族 + 2 个类型轴（9 种组合）**。四席等高等距、边距相等，
  严格镜像对称（0↔3 / 1↔2）。类型轴：
  `cover`（bare / flank / flank+canopy）、`elevation`（mesa / level / basin）。
  公开 108 张 = 每种 **12 张**，隐藏 108 张同类型不同 seed，**公开与隐藏零重叠**。
  类型码在 `public/maps4/manifest.json` 的 `type_code`（如 `flank|mesa`；老的三段式全码在 `type_code_full`）。
  三族「terraces / ridge / basin」是 v1.x 的旧说法，v2.4.0 起不再适用。
  原第三个轴 `openness` 在 v2.4.3 删除：实测只有 1.4pp 效应（挡不住主流的高抛打法）。
- 公平性与类型读数见 `docs/fairness-audit.md` 与 `docs/map-types.md`。

`docs/*`、`tools/*`、`tests/*` 为出题侧内部资料，不随选手包分发；文内引用仅作出题侧追溯（文档索引见仓库内 `docs/README.md`）。

## 许可

本包随仓库按 **Apache-2.0** 发布，版权 2026 **Aesolumitria-Lab**；见包内 `LICENSE` 与 `NOTICE`。
TIDE 基准与 CHIMERA/PSEUDO/TYPHON 挑战 bot 的归属/改动见 `NOTICE`。

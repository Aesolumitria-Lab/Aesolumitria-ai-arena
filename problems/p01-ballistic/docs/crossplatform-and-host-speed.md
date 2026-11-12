# P01 · 跨平台一致性与「宿主速度」审计（2026-11-09）

> 用一台真实的 2 vCPU Ubuntu 24.04 云主机当第二个平台，
> 和本机 Windows（32 核，MinGW g++ 16.1）对跑。所有读数可复现，命令在 §5。

## 0. 两条结论，一条好消息一条坏消息

| # | 结论 | 证据 |
|---|---|---|
| 1 | **跨平台确定性（K01）成立** | 引擎逐 hash 153 用例 / 1377 hash 全同；bot 决策 40 回合全同；整局 144/144 全同（去掉 CPU 超时后） |
| 2 | **50 ms 每回合 CPU 预算与宿主速度绑定** | 同一份 TIDE-HARD，Windows 上 30ms/回合、云主机上 **60ms/回合** ⇒ 144 局里 0 fault 变 **47 fault / 13 FORFEIT**，阶梯被压低 |

⇒ 第 1 条说明引擎和基准写得对；第 2 条说明**规则还缺一条**：
"50 ms" 到底是"这台机器上的 50ms"还是"某个基准机器上的 50ms"，规则里没说是哪个。
实际后果是：**排名会随评测机速度漂移**，而不是随选手强弱漂移。

---

## 0.5 v2.4.2 地图集重对账（2026-11-10，新增）

> **为什么要单列**：v2.4.2 重生了 216 张 4 席地图（原来的 `open|bare` 三类是同一张抄 4 份、
> 隐藏图还有 25/108 与公开图逐字节相同）。地图内容**会进 `state_hash`**
> （`recompute_hash` 把整张 grid 混进去），所以「地图换了」和「hash 变了」是一回事 ——
> 换了图就必须重跑一次跨平台对账，否则 `crossplatform-and-host-speed.md` 的结论只算对旧图集有效。
>
> 另外：原有 `tools/crosscheck.py` 的 p01 用例只覆盖 `public/maps/`（2 席遗留图），
> **完全没碰 `maps4`** —— 这是一个一直存在的覆盖空洞，本轮补上。

新增 `problems/p01-ballistic/tools/p01_mapcheck.py`：对 216 张 4 席图逐张 `LOAD <file> 1 15` + `STATE` + `GRID`，
取开局 `state_hash` 与 grid 的 sha256。它验证的是 fopen/读行/字符解析/水线与网格哈希这条链路，
不是整局对局（整局见 §3 的 144 局对账）。

| 平台 | 编译器 | 结果 |
|---|---|---|
| Windows（本机） | MinGW-W64 g++ **16.1** | 216 张图 → 216 个不同开局 hash |
| Linux（Ubuntu 24.04） | g++ **13.3** | 216 张图 → 216 个不同开局 hash |
| **比对** | — | **PASS 216/216：开局 hash 与 grid sha256 逐条全同** |

```bash
python  problems/p01-ballistic/tools/p01_mapcheck.py --json <输出目录>/_map_win.json      # Windows
python3 problems/p01-ballistic/tools/p01_mapcheck.py --json <输出目录>/_map_linux.json    # Linux（同一份源码现场编译）
python  problems/p01-ballistic/tools/p01_mapcheck.py --compare <输出目录>/_map_win.json <输出目录>/_map_linux.json
```

⇒ 地图集不是跨平台不确定性的来源。

---

## 1. 引擎：逐 hash 对账

`problems/p01-ballistic/tools/p01_trace.py` 只喂固定动作字符串，绕开 bot，直接比引擎的 `state_hash` 轨迹。

| 用例集 | 用例 | hash | 结果 |
|---|---|---|---|
| 3 张 2 席图 × 3 seed × 8 段动作 × 8 次推进 | 72 | 648 | **全同** |
| 再加 3 张 4 席图（同结构，四段动作） | 153 | 1377 | **全同** |

两次都是 `PASS ... hashes byte-identical`。这说明：
- `trig.hpp` 的编译期表 + Q16.16 定点运算确实把 libm 差异从根上移除了；
- `-static` 与 `-O2` 在两个编译器（g++ 16.1 / g++ 13.3）上给出同样的整数结果。

## 2. bot：决策对账

引擎相同 ≠ 整局相同：bot 也是决策源。`problems/p01-ballistic/tools/p01_askcmp.py` 让引擎当"发起方"，
把每个回合的 `STATE` + `GRID` 按协议包成 4 个 `TURN` 块，依次问 4 个 bot，
把**答复字符串**原样记录，再用这 4 句去 `STEP`。

| 排列 | 回合 | 结果 |
|---|---|---|
| `perm 0,1,2,3`（tide_hard / normal / easy / random4） | 21 | 21/21 回合的 state、4 句答复、结算后 hash **全同** |
| `perm 0,3,1,2`（同上） | 40 | 40/40 全同 |

⇒ TIDE 与 random4 的搜索是纯算术（`clock()` 只在 `TIDE_TRACE` 打开时用于打印耗时），
所以决策与平台无关。

## 3. 整局：`state_hash` 全同 —— 但只在"拿掉超时"之后

`problems/p01-ballistic/tools/p01_crossplatform.py` 跑 3 图 × 24 排列 × 2 seed = 144 局，4 个基准 bot。

| 设置 | Windows | 云主机（2 vCPU） | 逐局 hash |
|---|---|---|---|
| **50 ms CPU 预算（规则现值）** | 0 fault / 0 FORFEIT | **47–51 fault / 13–14 FORFEIT** | **不一致**（17 局） |
| CPU 预算放宽到 300 ms | 0 / 0 | 6 fault / 2 FORFEIT | 不一致（2 局） |
| **完全关掉超时（1e9 ms）** | 0 / 0 | **0 / 0** | **144/144 全同**（sha256 两边都是 `b95c3a34…`） |

⇒ 三层归因很清楚：

1. 引擎与 bot 本身**跨平台一致**（第三行钉死了这一点）；
2. 中间那些不一致，全部来自**裁判因 CPU 超时把答复替换成 `WAIT`** —— 一旦某个回合
   发生这件事，之后整局就分叉了；
3. 所以"不一致"不是平台 bug，是**预算判罚在慢宿主上被真实触发**。

## 4. 真正的缺陷：50 ms 是"宿主相对量"，不是"绝对量"

TIDE 和 CHIMERA 的搜索是**固定算术量**（候选数与搜索预算固定），
没有 `while (time left)` 这种时间自适应。因此：

- 每回合 CPU 消耗 ≈ 常数 × 宿主单核速度；
- 同一份二进制，快机器上 5.86 ms，慢机器上 60 ms。

实测（40 个相同回合，取每回合 CPU）：

| bot | Windows 32 核 | 云主机 2 vCPU | 比值 |
|---|---|---|---|
| TIDE-EASY | 0.00 ms（采样粒度以下） | ≈0 | — |
| TIDE-NORMAL | 3.12 ms | ≈20 ms | ~6× |
| TIDE-HARD | 5.86 ms | **60 ms（p99）** | **~10×** |
| CHIMERA | 6.64 ms | 30 ms | ~4.5× |
| PSEUDO / TYPHON | 19.9 / 22.7 ms | 60 ms（TYPHON p99） | ~2.6× |

（同一份纯整数 CPU 微基准：Windows 0.498 s、云主机 0.740 s —— 单核差 1.5×。
 bot 的差距更大，因为云主机只有 2 核且内存 3.8 GiB，缓存与并行度都吃紧。）

**后果（这是要写进规则的东西）：**

1. **基准自己会越界**。TIDE-HARD 在慢宿主上会稳定吃掉 60ms > 50ms，
   于是"及格线"自己开始 forfeit。
2. **排名随宿主漂移**。同一批 144 局：

   | 宿主 | hard | normal | easy | random |
   |---|---|---|---|---|
   | Windows 32 核 | 46.5% | 40.3% | 13.2% | 0.0% |
   | 云主机 2 vCPU | 41.7% | 37.5% | 11.8% | 0.0% |

   阶梯顺序没翻，但**每一档都被压低 ~5pp**，且 13 局直接判负。
   选手若拿"在快机器上测出来的胜率"去对标，会得到系统性乐观的结论。
3. **选手的策略选择被扭曲**。在快宿主上"多搜一点"是免费的；在慢宿主上同样代码被罚。
   ⇒ 这会**奖励"猜评测机速度"**，而不是奖励"算得准"。

### 建议（2026-11-10 已定：A 方案，参考机 i9-14900HX）

| 方案 | 做法 | 代价 |
|---|---|---|
| A（已采用） | 预算改成**相对量**：每回合预算 = `K × 参考基准机器上 TIDE-NORMAL 的单回合中位数`，评测时先跑一遍标定 | 需要评测框架多一次标定跑（~几十秒） |
| B | 保持 50 ms，但**只在指定的评测机型上排名**，并把机型写进 `meta.json` | 选手必须知道机型；跨机型结果不可比 |
| C | 把预算放宽到"慢宿主也够"（例如 200 ms），接受"快宿主上不再有区分度" | 丢掉了"算得快"这一维 |

2026-11-10 已确认：**参考机就是当前这台 i9-14900HX（8P+16E / 32 线程，Windows）**。
A 方案已在 `RULES.md` §15.2 落地（`turn_budget_ms = 50 × s`，`s = T_host / T_ref`），
`meta.json → turn_budget.landmark` 已写实 CPU 型号；跨机型评测必须先跑
`problems/p01-ballistic/tools/p01_calibrate.py` 并把 `s` 传给 `judge4 --turn-scale`（或 `P01_TURN_SCALE`）。
**没有标定就开跑，等于宣称本机是参考机**，排名只能和同机型比。

## 5. 复现

```bash
# 引擎层（两边各跑一次）
python3 problems/p01-ballistic/tools/p01_trace.py run --engine ./engine --maps-dir ./maps --out side.json
python  problems/p01-ballistic/tools/p01_trace.py compare win.json linux.json

# bot 层
python3 problems/p01-ballistic/tools/p01_askcmp.py --engine ./engine --bins a,b,c,d --map m.txt --seed 1 \
    --perm 0,3,1,2 --rounds 40 --out side.json

# 整局层
python3 problems/p01-ballistic/tools/p01_crossplatform.py run --game . --bins "hard=..,normal=..,easy=..,random=.." \
    --maps ./public/maps4 --maps-count 3 --jobs 1 --out side.json
python  problems/p01-ballistic/tools/p01_crossplatform.py compare win.json linux.json

# 预算余量
python3 problems/p01-ballistic/tools/p01_budget.py --game . --bots "TIDE-HARD=..,TIDE-NORMAL=..,CHIMERA=.." \
    --maps ./public/maps4 --maps-count 3 --perms 8
```

## 6. 两个小坑（2026-11-10 已修）

1. **Linux 预编译基准**：包内新增 `public/baselines/bin/linux/` 与
   `public/challenges/bin/linux/`（TIDE 三档 / random4 / CHIMERA / PSEUDO / TYPHON），
   由评测侧同一份源码在 Ubuntu g++ 15.2 下静态构建。Linux 上 `selfcheck4.py`
   会自动选 `bin/linux/`；engine 和参赛 bot 仍现场 `g++` 编译。
2. **`selfcheck4` 平台后缀**：不再硬编码 `.exe`；Windows 用 `bin/*.exe`，
   Linux 用 `bin/linux/*`（无扩展名）。`judge.py`/`judge4.py` 原有的
   `_exe()`/`CXX` 分叉保留。

## 7. 2026-11-10 并发扫描：吞吐 vs 单步尾延迟

> 脚本在审计目录 `concurrency_sweep.py`，原始读数 `concurrency_sweep.json` /
> `concurrency_typhon.json`。方法：`public4-01 × 24 排列 × 6 seed = 144 局/档`
> （TYPHON 为 24 局/档），4 个相同 bot，`wall_allow_s = 0.25 × (jobs-1)`。

### 7.1 TIDE-HARD（轻基准）

| jobs | 局/秒 | CPU 均值/步 | CPU p99 | CPU max | wall p99 | fault |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2.17 | 5.03 ms | 15.62 | 15.62 | 10.9 ms | 0 |
| 8 | 15.94 | 5.34 ms | 15.62 | 15.62 | 12.1 ms | 0 |
| 14 | 22.19 | 6.58 ms | 15.62 | 31.25 | 15.5 ms | 0 |
| 20 | 26.00 | 7.41 ms | 15.62 | 31.25 | 17.7 ms | 0 |
| 24 | 28.65 | 8.27 ms | 15.62 | 31.25 | 18.9 ms | 0 |
| 32 | 30.74 | 9.29 ms | 15.62 | 31.25 | 21.5 ms | 0 |

### 7.2 TYPHON（最重 challenge）

| jobs | 局/秒 | CPU 均值/步 | CPU p99 | CPU max | wall p99 | fault |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.70 | 19.21 ms | 31.25 | 31.25 | 28.2 ms | 0 |
| 8 | 5.16 | 20.46 ms | 31.25 | 46.88 | 33.6 ms | 0 |
| 12 | 6.70 | 23.69 ms | 46.88 | 46.88 | 42.8 ms | 0 |
| 16 | 6.70 | 24.54 ms | 46.88 | 46.88 | 45.7 ms | 0 |
| 20 | 6.60 | 27.53 ms | 46.88 | 46.88 | 47.2 ms | 0 |
| 24 | 9.94 | 31.46 ms | 46.88 | **62.50** | 47.6 ms | 0 |
| 28 | 9.93 | 31.59 ms | 46.88 | 46.88 | 47.4 ms | 0 |
| 32 | 9.91 | 31.80 ms | 46.88 | 46.88 | 47.5 ms | 0 |

### 7.3 结论

1. **吞吐**：TIDE-HARD 到 24 jobs 已接近饱和（28.65 局/秒；32 为 30.74，只多 7%）。
   TYPHON 在 24 jobs 到 9.94 局/秒，28/32 不再增加。
2. **CPU 时间会随并发升高**：TIDE-HARD 5.03→9.29 ms（1.85×），
   TYPHON 19.21→31.80 ms（1.66×）。墙钟 p99 同步从 10.9/28.2 ms 涨到 21.5/47.5 ms。
3. **重 bot 的安全点**：TYPHON 在 20 jobs 的 CPU p99/max 是 46.88 ms，0 fault；
   24 jobs 吞吐高 50%，但出现一次 62.50 ms 尖峰（仍 0 fault）。
4. **口径**：排名/混合重 bot 用 `--jobs 20`；纯 TIDE 快速跑用 24
   （32 只在不在乎尾延迟时）。24 以上对 TYPHON 没有吞吐收益。
5. **测量粒度**：Windows 进程 CPU 采样是 15.625 ms，p99/max 会被量化成
   0 / 15.62 / 31.25 / 46.88 / 62.50。判断预算余量要看**批量均值或每局总 CPU**，
   不要拿单次采样的 max 当真实耗时；本表的 `fault=0` 是实际判罚结果。
   注意裁判的实际判罚线是 `50 ms + 一个采样粒度`（约 **65.6 ms**）：
   24 jobs 的 62.5 ms 尖峰因此没有变成 fault，但余量已经很薄；排名仍建议留在 20 jobs。

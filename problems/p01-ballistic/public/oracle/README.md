# P01 · 只读轨迹 oracle

`oracle.cpp` 把引擎自己的代码 `#include` 进来，跑**单发弹道**或**一段固定动作**，
把中间量原样打出来。它是给你**对账**用的，不是给你抄的。

## 为什么给你这个

题目要考的是"你能不能把引擎的 Q16.16 物理逐 tick 复刻准"。自己瞎猜弹道，
或者只拿 `arena4::aim_at` 的近似去试，会卡在同一个地方反复失败 —— 而且你连
"我错在第几个 tick"都说不出来。

`oracle` 给你两个东西：

1. **单发轨迹**：`shot` 模式逐 tick 打印`x / y / vx / vy / life / dug / stuck`，
   全是 Q16.16 的**原始整数**（同时给出定点小数便于读）。
   ⇒ 你自己的模拟器如果在第 7 tick 开始偏，差异会直接跳出来。
2. **整段对账**：`trace` 模式吃一个"每轮四席动作"的脚本，逐轮打印
   `STEP <round> <state_hash>`。你把同一个脚本喂给自己的模拟器，
   **hash 必须每一个都完全一致**。不一致就是你的复刻有偏差。

## 编译

```text
g++ -O2 -std=c++17 -I public -o oracle public/oracle/oracle.cpp
# Windows 加 -static
```

## 用法

### 单发弹道

```text
oracle shot <map> <seed> <worm_id> <angle> <power> [ROCKET|GRENADE|DRILL]

> oracle shot public/maps4/public4-01.txt 3 0 45 80
# map=public/maps4/public4-01.txt seed=3 worm=0 at=(12,25) weapon=ROCKET angle=45 power=80 wind=-7 water=50
TICK 0 x=842370 y=1647998 (12.8535,25.1465) vx=37072 vy=-37072 life=300
TICK 1 x=879386 y=1611581 (13.4184,24.5908) vx=37016 vy=-36417 life=299 dug=0 stuck=0
TICK 2 x=916346 y=1575819 (13.9823,24.0451) vx=36960 vy=-35762 life=298 dug=0 stuck=0
...
HASH 90907d0e5085eb1f
```

`worm_id` 用的是引擎的 12 虫编号（0–2 seat0，3–5 seat1，依此类推）。
`TICK 0` 是**发射后、推进前**的状态（`spawn` 位置已经算好）；`TICK n` 是推进 n 次之后。

### 整段对账

脚本文件每行 = 一轮的四席动作，用 `|` 分隔（2 席地图用两段）：

```text
0 M R; M R; M R; F ROCKET 45 80 | 3 F GRENADE 19 86 | 6 M L; M L; F ROCKET 120 70 | 9 M R; M R; F ROCKET 100 60
0 M R; M R; F ROCKET 40 80 | 4 M L; F ROCKET 150 70 | 6 WAIT | 9 WAIT
```

```text
oracle trace <map> <seed> <script.txt> [mask]

> oracle trace public/maps4/public4-01.txt 3 script.txt
# map=public/maps4/public4-01.txt seed=3 mask=15 seats=4
STEP 0 90907d0e5085eb1f
WORM 0 0 12 25 100 2 2 1          ← round id x y hp grenades drills alive
WORM 0 1 16 25 100 2 2 1
...
STEP 1 d13ecb35f3ff9921
...
RESULT 1 winner=-1 reason=RUNNING
```

（上面两条脚本在 v2.4.3 的 `public4-01` 上只跑了两轮，没有触发爆炸，因此示例输出没有
`BOOM` 行；实际发生爆炸时会多出 `BOOM <round> <ordinal> <x> <y> <kind> <owner_worm>`。）

### 风序列

```text
oracle wind <map> <seed> [rounds]

> oracle wind public/maps4/public4-01.txt 3 8
ROUND 1 wind=-7 CHANGED
ROUND 2 wind=-3
...
```

## 它**不**给你什么

- **不是 bot 骨架**。它不含任何策略、搜索、目标选择。那些是你的事。
- **不读 stdin 协议**。对局里你得自己解析 `TURN/STATE/GRID`。
- **不摆位**。`shot`/`trace` 用 debug 模式的 `LOADX` 语义，正式对局走 `LOAD`，
  那些命令会被引擎拒绝 —— 这个能力不会泄漏到比赛里。
- **没说"这样打就赢"**。风、水位、对手动作都不在里面。

## 它和"把引擎当库"有什么区别

引擎源码本来就在包里（`public/engine/engine.cpp`），你完全可以
`#define P01_ENGINE_NO_MAIN` + `#include` 进来在**自己的进程里**跑它。
`oracle` 不是另一条路，它是**已经把这条路跑通、并且把输出格式定死**的那个东西：

- 直接 include 引擎，你会面对 `load_map()` 只吃**文件路径**——
  想用裁判下发的 `GRID`，得自己往 `G.grid` / `G.worm` 里灌字符，没有公开入口。
- `oracle` 把这个坑绕开了：它用文件地图 + debug 摆位，属于"离线对账"场景，
  和对局内的"用下发地形"是两回事。

⇒ **结论**：对账用 `oracle`（或者自己 include 引擎），**比赛里不要 include 引擎**：
对局是流式 stdin、地形每轮变化、50 ms CPU 预算，塞一个完整引擎进去只会让你超时。

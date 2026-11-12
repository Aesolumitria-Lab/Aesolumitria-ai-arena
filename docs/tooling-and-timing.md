# 工具与计时

本文讲工具分工与计时口径。命令行参数与接口契约见 [`../tools/README.md`](../tools/README.md)。

## 1. 工具分工

| 工具 | 作用 |
|---|---|
| `bench.py` | 编译 + 批量对战 + 计时：步数分布、胜负、margin |
| `profile_turn.py` | 单局逐步 CPU/墙钟剖析，定位最慢的步 |
| `validate.py` | 调用各题 `tests/validate.py`：golden、边界、fuzz、确定性 |
| `acceptance.py` | 一条命令的工程验收（docs / meta / maps / validate / validate4 / pack / leak / package，`--ranking` 另验档位） |
| `pack.py` / `verify_package.py` / `leakcheck.py` | 选手包打包、manifest 自校验、泄漏检查 |
| `seal_anchors.py` / `pack_eval.py` | 基准封装成二进制 / 评测包 |
| `replay.py`（2 席）/ `replay4.py`（4 席） | 回放逐帧重算与单文件 HTML 查看器 |
| `tournament.py` | 赛程冻结、断点续跑、BT 排名（两席） |
| `challenge.py` | 选手本地一键挑战（只用公开对手/公开 seed，不是官方排名） |
| `calibrate.py` / `rating.py` | 基准校准、BT 实力值 |
| `rating4.py` / `ffa4.py` / `ffa_rating.py` / `anchor_audit4.py` | 4 席对局运行与评分/档位审计 |
| `anchor_audit.py` | 两席基准档位单调性审计（只看胜负） |
| `crosscheck.py` | 跨平台 `state_hash` 轨迹比对 |
| `abuse.py` | 抗滥用测试（死循环、巨量输出、无应答、崩溃等） |
| `check_env.py` | 环境基线检查 |
| `refresh_meta.py` | 把 `meta.json` 的 hash 对齐到文件 |
| `link_private.py` | 挂载仓库外的私有内容 |
| `agent_deadline.py` | 给选手 agent 加硬性墙钟截止 |
| `bot_registry.py` | bot 提交条目登记与校验 |

通用约定：从 `meta.json` 读配置；失败返回非零退出码；需要隐藏图或基准源码的工具在没有私有内容时会报找不到文件。

## 2. 计时口径

### 2.1 CPU 与墙钟

- **CPU 时间**是判罚依据：裁判读子进程 CPU 时间（Windows `GetProcessTimes`，Linux `/proc/<pid>/stat`），按进程所有线程合计；采样不可用时退回墙钟；
- **墙钟**只做保险上限；
- **编译时间**单独统计，不进每步预算；
- **首步**：`INIT` 发出后直接进入第一次接收，首步给 2s 墙钟宽限（不是 CPU 上限），单步 CPU 预算仍按每步预算算。

分开统计的原因：机器负载影响墙钟但不应影响判罚；多线程 bot 的 CPU 总和可大于墙钟；阻塞 I/O 不算 CPU，但墙钟需要兜底。

P01 的预算含宿主标定系数与并发因子，细节见其 `RULES.md` §15.2 与 [`problems/p01-ballistic/docs/crossplatform-and-host-speed.md`](../problems/p01-ballistic/docs/crossplatform-and-host-speed.md)。

### 2.2 余量与测量

- 建议 p95 不超过预算的约 80%，不要卡着预算写 bot；
- 测量用 CPU 时间，不用 `system_clock`；
- 报告里写明是否丢弃了热身步；异常值保存回放；
- 官方计时/验收保持单并发（`--workers 1`）。

### 2.3 CI 与固定机器

共享 CI runner 速度不稳，读数不可用于排名，只适合冒烟。本仓库不带 workflow 文件。验收是本地一条命令：

```bash
python tools/acceptance.py --game p01-ballistic
```

想接 CI 时，最小形态是上面这条，再加一步只报差异不写文件的守卫：

```bash
python tools/refresh_meta.py --dry-run
```

正式排名应在固定机器上跑，并记录 CPU 型号、编译器版本、优化选项。

仓库用 `.gitattributes` 把文本钉为 LF：hash 按字节算，行尾变化会让 `rules_hash` / `engine_hash` 和地图 sha256 全部对不上。详见 [`../CONTRIBUTING.md`](../CONTRIBUTING.md)。

## 3. 验收报告模板

每次大改或发版建议留一份读数：版本与 hash、编译器与 CPU、测试通过数、计时（init/turn p95、max、超时/非法数）、基准梯度、地图/座位偏差、结论与待修问题。数据写进该题 `CONDITIONS.md`。

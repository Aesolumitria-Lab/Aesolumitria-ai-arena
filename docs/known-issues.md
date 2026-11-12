# 已知问题（通用）

只记录跨题、工具、流程层面的问题。P01 专属问题见 [`problems/p01-ballistic/docs/known-issues.md`](../problems/p01-ballistic/docs/known-issues.md)。其他题目开发中，尚未发布。

分级：**A** 阻塞发布或排名；**B** 影响排名或评分口径；**C** 记录。

| # | 级别 | 范围 | 问题 | 说明与处置 |
|---|---|---|---|---|
| G01 | A | 流程 | 行尾变化导致 hash 漂移 | `rules_hash` / `engine_hash` / `arena_hash` 与地图 sha256 按字节计算。文本被 checkout 成 CRLF 会使干净 clone 上 `acceptance` 失败。仓库用 `.gitattributes` 钉 LF；不要靠本机 `core.autocrlf` 覆盖。见 `CONTRIBUTING.md` |
| G02 | A | 流程 | 改引擎会使全部旧回放与成绩失去可比性 | `state_hash` 由引擎产生。旧回放只配旧引擎，`replay.py verify` 对 `engine_hash` 不一致只提示“旧引擎”，不要用新引擎硬跑 |
| G03 | B | 工具 | `refresh_meta.py` 只哈希 `FIELDS` 里列出的文件 | 未列入的引擎依赖文件（如被 `engine.cpp` 包含的头文件）改动不会让 `engine_hash` 变化，但可能改变行为。此类改动需人工登记进 `CONDITIONS.md` 并做一次跨平台对账（`crosscheck.py`） |
| G04 | B | 工具 | `leakcheck.py` 通过不等于包干净 | 它是模式扫描，曾因只扫已知后缀而漏掉无后缀文件，现已扩展，但仍可能漏。发布前应配合 red-team 与人工复核 |
| G05 | B | 工具 | 不同题的 `margin` 单位不同，不可跨题比较 | 不要把不同题的基准放进同一个 BT 模型 |
| G06 | B | 工具 | 档位判断不能看 margin | 一场多局换座会把分差正负抵消。`anchor_audit.py` 只按胜负判单调 |
| G07 | B | 工具 | 多席（4 席）评分只有原始胜率口径 | 正式 BT、4 席 tournament、`acceptance --ranking` 的 4 席版本未接入。见 `baseline-and-scoring.md` §5.2 |
| G08 | B | 工具 | 对局小样本时偏差与 CI 只作占位 | 已分胜负不足 10 局时座位偏差仅供参考；全胜对的 bootstrap CI 退化为 `[x, x]` |
| G09 | B | 工具 | `tournament.py` 的 swiss 不能预冻结整届对阵 | 对手由上一轮结果决定，只能冻结“参数 + 已生成的轮次”；也不连服务器、不发排行榜 |
| G10 | B | 环境 | 不可信 bot 的强制沙箱未在本仓库内实现 | `check_env.py` 只检查编译器、模板与包结构。断网、禁 fork、文件白名单、资源上限属于运行评测的 agent harness 或容器的职责，外部提交前必须单独验证（见 `eval-environment.md`） |
| G11 | C | 工具 | 构建目录并发 | 多个任务同时重编同一题的 `build/` 会互相干扰；Windows 上运行中的 `engine.exe` 会被锁定，`validate` 可能以 `Permission denied` 失败，清理残留进程即可 |
| G12 | C | 元数据 | `difficulty` 字段不被任何工具读取 | 它是人工排序标签，没有标定方法，不得当作验收依据 |
| G13 | C | 流程 | 干净 clone 中基准/挑战二进制目录为空 | 这些二进制不进版本库（随 `dist` 包走），依赖它们的冒烟测试会打印 `SKIP`，是设计内的跳过 |
| G14 | C | 流程 | 无 CI workflow | 共享 runner 的计时读数不可用于排名；验收是本地一条命令，见 `tooling-and-timing.md` |
| G15 | C | 流程 | 仓库无根级 CHANGELOG | 变更记录在各题的 `CHANGELOG.md`、`CONDITIONS.md` 与 README 中 |

“未验证”说明：旧版文档中的各类历史成绩读数、通过数与日期未随本文保留，因为无法在当前代码中复核。需要证据时请重新运行对应工具。

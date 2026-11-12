# P01 bot 梯队（TIDE / 挑战 / legacy）· 内容适用 v2.4.x

更新：2026-11-06（梯队结构自 v2.1.0 起未变，v2.4.x 仍适用）。
版本号以 `meta.json` 为准（当前 v2.4.3），梯队口径以 `meta.json` 的 `bot_ladder` 为准：
`baseline = tide_easy / tide_normal / tide_hard`、`challenge = chimera / pseudo / typhon`、
`target = tide_normal`、`challenge_required = false`。
目标：选手先稳定击败公开基准 **TIDE**（至少 NORMAL，HARD 为拉伸）；隐藏挑战 **CHIMERA/PSEUDO/TYPHON** 不要求击败，它们是上限/抗压/惊吓。

## 公开基准（随选手包分发）

| 档 | bot | 源码 | 预编译 | 说明 |
|---|---|---|---|---|
| EASY | `tide_easy` | `private/baselines_src/tide.cpp` | `public/baselines/bin/tide_easy.exe`（Linux：`bin/linux/tide_easy`） | `-DTIDE_PROFILE=0`；有确定性抖动 |
| NORMAL | `tide_normal` | 同上 | `public/baselines/bin/tide_normal.exe`（Linux：`bin/linux/tide_normal`） | `-DTIDE_PROFILE=1`；默认/主目标 |
| HARD | `tide_hard` | 同上 | `public/baselines/bin/tide_hard.exe`（Linux：`bin/linux/tide_hard`） | `-DTIDE_PROFILE=2`；拉伸目标 |

TIDE 是“可解释的尺子”：每回合选一对虫/目标、9 个落点、一枪；平均 3ms、最差 5ms。它不是 TYPHON 级搜索。

## 挑战 bot（二进制随选手包，源码不随；不要求击败）

| 档 | bot | 源码 | 预编译 |
|---|---|---|---|
| 挑战 1 | `CHIMERA` | `private/challenges4/chimera/chimera.cpp`（评测侧） | `public/challenges/bin/chimera.exe` |
| 挑战 2 | `PSEUDO` (D10) | `private/challenges4/pseudo/source/`（评测侧） | `public/challenges/bin/pseudo.exe` |
| 挑战 3 | `TYPHON` (D11) | `private/challenges4/typhon/source/`（评测侧） | `public/challenges/bin/typhon.exe` |

难度梯度：`TIDE-EASY < TIDE-NORMAL < TIDE-HARD ≪ CHIMERA < PSEUDO < TYPHON`；HARD 到 CHIMERA 是断档。挑战 bot 不要求击败；及格线/目标是 TIDE。

## 编译

```sh
g++ -O2 -std=c++17 -static -DTIDE_PROFILE=0 -I public private/baselines_src/tide.cpp -o build/tide_easy.exe
g++ -O2 -std=c++17 -static -DTIDE_PROFILE=1 -I public private/baselines_src/tide.cpp -o build/tide_normal.exe
g++ -O2 -std=c++17 -static -DTIDE_PROFILE=2 -I public private/baselines_src/tide.cpp -o build/tide_hard.exe
g++ -O2 -std=c++17 -static -DCHI_C1=1 -DCHI_FIX_PHYS=1 -I public private/challenges4/chimera/chimera.cpp -o build/chimera.exe
```

PSEUDO/TYPHON 由出题侧各自的构建脚本产生；预编译二进制随选手包分发，源码不公开。

## 哈希

| 文件 | sha256 |
|---|---|
| `private/baselines_src/tide.cpp` | `8b96cb832841874e7121bc37a5e81f5eb38fdaf95bd315a7b9f6d2ccbda755b8` |
| `build/tide_easy.exe` | `70037f58a6fe3b8348cba52432c62b31951162a02a99e34e646ca5e3511b0b9d` |
| `build/tide_normal.exe` | `d59245d405ccd620181977dab59025e9bd6f036d5a100e8c9b29b86d2fff2076` |
| `build/tide_hard.exe` | `6a2030a150e669a356072408ffdf48b6b238e5af899b80198ef0dd047c5fcf81` |
| `private/challenges4/chimera/chimera.cpp` | `f6f50aeb2e62c7a15f10c6aba95a4cda34159267db56b78cb8c974a084b3010e` |
| `build/chimera.exe` | `f9165873a290cc98e44e4b8fe9630bd64ad969982aa0a00e3f3d8e779e91b476` |
| `private/challenges4/pseudo/source/typhon/submission.cpp` | `6107f91d23608b25ecc42296747791ee655cf69c2787732feacc81c02850107f` |
| `build/pseudo.exe` | `7844a706e5590d7ea01537e0590d384e05089601dcb0fda6fb050ed2a226cd1f` |
| `private/challenges4/typhon/source/typhon/submission.cpp` | `8ec01f4dfa0ae763f64e013500f15b05074c6b131ecb261bd6d2a622de6c69b5` |
| `build/typhon.exe` | `34e8ba0842d3474925d3b5a1a41865731593eba39a10f31e6bad3afc98b3b149` |

## legacy

旧 anchors4（random4/greedy4/scripted4/strong4）保留为历史校准，不再作为 v2.1.0 起的门槛；旧 anchor 梯度（strong4 66.7% 等）只作 v2.0 历史证据，其源码在出题侧私有目录，不随仓库发布。

注意 `random4` 有双重身份：它作为“最弱对照”仍随选手包发布（`public/baselines/bin/random4.exe`，`selfcheck4.py` 会用到），
但作为评分梯队它是 legacy，不算及格线的一部分。随包发布的这个 `random4` 的源码是公开的：
[`baselines/random4.cpp`](baselines/random4.cpp)（它与旧 anchors4 里同名的那个不是同一份实现）。
TIDE 的设计说明见 [`baselines/TIDE.md`](baselines/TIDE.md)；TIDE 与挑战 bot 的源码不公开。

## 许可

本题 Apache-2.0，版权 2026 Aesolumitria-Lab；见包内 `LICENSE`/`NOTICE`。TIDE 与挑战 bot 的归属说明见 `NOTICE`。

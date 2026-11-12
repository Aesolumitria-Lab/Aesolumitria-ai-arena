# 评测环境规范

> 两个阶段，两套环境。**不要把“能编译的选手环境”直接拿去跑不可信的 bot。**
> 检查脚本：`python tools/check_env.py --game p01-ballistic --package dist/contestant/p01-ballistic.zip`

---

## 1. 两个阶段

| 阶段 | 谁在用 | 目的 | 是否可信 |
|---|---|---|---|
| **A · 选手开发/构建** | 参赛 AI / 选手 | 读规则、写 bot、编译、跑本地 judge | 不可信，需要限制读和网络 |
| **B · 评测运行** | 评测服务端 | 跑 bot 对 anchor/其他 bot | 不可信，需要更窄的沙箱 |

A 阶段需要编译器和 Python；B 阶段只需要“引擎 + 裁判 + bot 二进制 + 评测包”，
**不提供 shell、编译器、Python 或网络**。

---

## 2. 必须有的组件

### 2.1 A 阶段（选手开发/构建）

| 组件 | 要求 | 说明 |
|---|---|---|
| C++ 编译器 | `g++`（C++17），建议 MinGW-w64 / GCC 11+ | 编译 `template.cpp` / bot |
| Python | 3.10+（建议 3.13） | 跑 `public/judge/judge.py` 和本地测试 |
| shell | 一个固定 shell（Linux `/bin/sh`，Windows PowerShell 7 或 `cmd`） | 只用于执行编译/运行命令 |
| 选手包 | `pack.py` 产出的 zip 解压到工作区 | 唯一可读的题目资料 |
| 临时目录 | `/tmp` 或工作区内 `build/` | 编译产物 |

**不需要**：node、npm、git、包管理器、数据库、浏览器、任何在线服务。

### 2.2 B 阶段（评测运行）

| 组件 | 要求 |
|---|---|
| 题库代码 | 引擎 + `judge.py` + `tools/`（服务端持有） |
| 选手 bot | 已编译的二进制（按赛事规定） |
| 评测包 | `pack_eval.py` 产出：anchor 二进制 + 隐藏图 |
| 运行时 | 仅 OS 基础库 + 动态链接器 |

**不需要**：shell、编译器、Python、node、网络。

---

## 3. 文件系统白名单

### 3.1 A 阶段

| 路径 | 权限 | 说明 |
|---|---|---|
| `/workspace`（选手包 + 产物） | 读写 | 唯一工作区 |
| `/usr/include`、`/usr/lib`、`/lib` | 只读 | 编译器/标准库/运行时 |
| `/tmp` | 读写，限大小 | 编译临时文件 |
| `/proc`、`/sys`、`/home`、题库 `private/` | **不挂载** | 防止偷看隐藏图/anchor/其他用户 |
| 网络 | **无** | 容器 `network=none` 或同等隔离 |

### 3.2 B 阶段

| 路径 | 权限 | 说明 |
|---|---|---|
| `/eval`（评测包） | 只读 | anchor 二进制 + 隐藏图 |
| `/run`（bot 二进制） | 只读 | 选手提交物 |
| `/tmp` | 读写，限大小 | 仅运行时临时文件 |
| `/workspace`、`/usr/include`、`/proc`、`/sys`、`/home` | **不挂载** | bot 只通过 stdin/stdout 通信 |

---

## 4. 强制隔离（不是只写规则）

| 项 | A 阶段 | B 阶段 |
|---|---|---|
| 网络 | 无 | 无 |
| 进程数 | 允许编译子进程 | **禁止 fork/子进程** |
| 线程 | 允许（编译需要） | 按赛事规定；CPU 预算按进程所有线程合计 |
| CPU | 不限制（构建） | 单步 50 ms CPU，首步 2 s wall |
| 内存 | 建议 2–4 GB | 建议 256–512 MB |
| 文件写入 | `/workspace` + `/tmp` | `/tmp` 限大小 |
| 交互 | 无“询问用户”通道 | stdin 只给协议 |

规则层（`CONTESTANT_RULES.md`）负责告知，沙箱层负责兜底。两者缺一不可。

---

## 5. 为什么不需要 node

本题库是 C++17 + Python 裁判/工具：

- bot 是 C++，编译靠 `g++`；
- 本地裁判 `judge.py` 是 Python 标准库；
- 没有前端、没有 JS 运行时、没有 npm 依赖。

装 node 只会扩大攻击面。平台层如果要做 Web 榜单/前端，那是另一套服务，不要塞进选手环境。

---

## 6. 禁止询问用户怎么落地

- A 阶段不提供“向用户提问”的交互通道；AI 只能读选手包、写代码、跑命令；
- `CONTESTANT_RULES.md` 与 `prompts/contestant_prompt.md` 明确禁止询问；
- B 阶段 stdin 只给游戏协议，没有自由文本输入；
- 违规判定：该场判负；严重违规取消资格。

---

## 7. 检查清单

```bash
python tools/check_env.py
python tools/check_env.py --game p01-ballistic
python tools/check_env.py --package dist/contestant/p01-ballistic.zip
python tools/check_env.py --eval --path dist/eval
```

`check_env.py` 会检查：

1. Python ≥ 3.10；
2. `g++` 可执行；
3. C++17 能编译并运行；
4. 指定题目的 `template.cpp` 能编译；
5. 选手包通过 `verify_package`，且不含 `private/`/`.exe`；
6. 评测包目录结构正确（`anchors/manifest.json` + 二进制）。

版本基线：Python 3.10–3.13，g++ 11+（C++17），Windows / Linux。
实际冻结环境以 `CONDITIONS.md` 的“构建环境”为准。

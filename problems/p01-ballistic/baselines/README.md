# P01 基准 bot：公开的源码与说明

| 文件 | 内容 |
|---|---|
| [`random4.cpp`](random4.cpp) | 最弱对照 bot `random4` 的完整源码：不移动，每回合随机挑一条存活的己方虫，打一发随机的合法火箭。可当作第二个协议示例（第一个是 `public/template4.cpp`）。 |
| [`TIDE.md`](TIDE.md) | 公开基准 TIDE（EASY / NORMAL / HARD）的设计说明：三档差异、每回合流程、打分项。 |

不在这里的：TIDE 和挑战 bot（CHIMERA / PSEUDO / TYPHON）的源码不公开，只以预编译二进制
随选手包分发（`public/baselines/bin/`、`public/challenges/bin/`）。梯队总览见
[`../BOT_LADDER.md`](../BOT_LADDER.md)。

## 自己编译 random4

在题目目录（或解压后的选手包目录，把本文件拷过去）下：

```bash
g++ -O2 -std=c++17 -static -I public baselines/random4.cpp -o random4
```

自己编译出的二进制与包内的 `random4.exe` 逐字节不一定相同（取决于编译器），但 bot 是确定性的，
相同对局的结果一致。

本目录不进选手包：`tools/pack.py` 只打包 `public/`。

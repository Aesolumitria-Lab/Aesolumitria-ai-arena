// P01 BALLISTIC · 只读轨迹 oracle
//
// 用途：让选手**验证自己复刻的弹道**是否与引擎一致，而不是靠眼力/截图比对。
// 它不吃 stdin 协议，只吃"地图 + seed + 一组固定参数"，把引擎的中间量吐出来。
//
// 它不是一个 bot，也不参与对局。它只读；不改任何文件；不联网。
//
// ------------------------------------------------------------------ 编译
//   g++ -O2 -std=c++17 -I public -o oracle public/oracle/oracle.cpp
//   （Windows 再加 -static）
//
// ------------------------------------------------------------------ 用法
//   oracle shot  <map> <seed> <worm_id> <angle> <power> [weapon]
//       从 worm_id 的当前位置按 angle/power 打一发，逐 tick 打印投射物位置
//       （Q16.16 原始整数 × 4 位整数格），末行给出爆炸格与每 tick 的目标距离。
//       weapon = ROCKET(默认) | GRENADE | DRILL
//
//   oracle trace <map> <seed> <script.txt> [mask]
//       script.txt 每行是"一轮的四席动作"，用 '|' 分隔，例如：
//           0 M R; M R; F ROCKET 90 60 | 3 WAIT | 6 WAIT | 9 WAIT
//       （2 席地图用两段）。逐轮打印：
//           STEP <round> <state_hash> <hp0> <dealt0> ...
//           WORM <round> <id> <x> <y> <hp> <grenades> <drills> <alive>
//           BOOM <round> <ordinal> <x> <y> <kind> <owner_worm>
//       用来对账：你自己的模拟器跑到同一轮，state_hash 必须一致。
//
// ------------------------------------------------------------------ 说明
// · 一切都走引擎自己的代码（#include engine.cpp），所以它给出的就是"正确答案"。
// · 它用 LOADX（debug 模式）来摆位，正式对局走 LOAD，那些命令会被拒绝 —— 也就是说
//   oracle 提供的能力不会泄漏到比赛里。
// · 想看风：`oracle trace` 每轮的 STEP 行第 4 列是 hash，风在 STATE 里；
//   直接跑 `oracle wind <map> <seed> <rounds>` 更省事。

#include <cstdio>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

// ---------------------------------------------------------------- 轨迹钩子
// 引擎里的 explode() 是 static，同一翻译单元里可以换成带计数的版本：
// 先把引擎的 explode 改名成 engine_explode_inner，再把一次 #include 里的
// explode 宏换掉，然后自己定义 explode 包装（先记录、再调用）。
//
// ★ 只 include 一次：engine.cpp 里有 `static const int W` 这类定义，
//   两次 include 会重定义。
namespace trace_hooks {
int boom_round = 0;
int boom_ord = 0;
struct Boom { int round, ord, x, y, kind, owner; };
std::vector<Boom> booms;
}  // namespace trace_hooks

#define explode engine_explode_inner
#define P01_ENGINE_NO_MAIN
#include "engine/engine.cpp"
#undef explode
#undef P01_ENGINE_NO_MAIN

static void explode(Game &G, int cx, int cy, int kind, int owner_wid) {
    trace_hooks::booms.push_back({trace_hooks::boom_round, ++trace_hooks::boom_ord,
                                  cx, cy, kind, owner_wid});
    engine_explode_inner(G, cx, cy, kind, owner_wid);
}

// ---------------------------------------------------------------- 小工具

static const char *weapon_name_of(int kind) {
    if (kind == K_ROCKET) return "ROCKET";
    if (kind == K_GRENADE) return "GRENADE";
    if (kind == K_DRILL) return "DRILL";
    return "?";
}

static std::string read_text(const char *path) {
    std::ifstream f(path, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static int parse_weapon(const std::string &s) {
    if (s == "GRENADE") return K_GRENADE;
    if (s == "DRILL") return K_DRILL;
    return K_ROCKET;
}

// ---------------------------------------------------------------- shot 模式
//
// 直接把一发投射物塞进 G.proj[slot] 然后 hand-roll 推进，逐 tick 打印。
// 为什么不用 do_round：那需要四席指令，而 shot 只想要一条弹道。
static int mode_shot(int argc, char **argv) {
    if (argc < 7) {
        std::printf("usage: oracle shot <map> <seed> <worm_id> <angle> <power> "
                    "[ROCKET|GRENADE|DRILL]\n");
        return 2;
    }
    const std::string map = argv[2];
    const int seed = std::atoi(argv[3]);
    const int wid = std::atoi(argv[4]);
    const int angle = std::atoi(argv[5]);
    const int power = std::atoi(argv[6]);
    const int kind = (argc > 7) ? parse_weapon(argv[7]) : K_ROCKET;

    if (wid < 0 || wid >= NW) { std::printf("ERR bad worm id\n"); return 2; }
    if (angle < 0 || angle > 180 || power < 0 || power > 100) {
        std::printf("ERR angle/power out of range\n");
        return 2;
    }

    Game G;
    G.seed = seed;
    G.active_mask = 0b1111;
    G.debug_mode = 1;           // LOADX 语义：允许摆位
    const std::string err = load_map(G, map, false);
    if (!err.empty()) { std::printf("ERR %s\n", err.c_str()); return 1; }
    if (!G.worm[wid].alive) { std::printf("ERR worm not alive\n"); return 1; }

    std::printf("# map=%s seed=%d worm=%d at=(%d,%d) weapon=%s angle=%d power=%d "
                "wind=%d water=%d\n", map.c_str(), seed, wid, G.worm[wid].x,
                G.worm[wid].y, weapon_name_of(kind), angle, power, G.wind, G.water);

    // 只生成这一发，然后手动推进（每 tick 一行）。
    spawn_projectile(G, wid, kind, angle, power);
    Proj &p = G.proj[wid / 3];
    if (!p.active) {
        std::printf("TICK 0 IMMEDIATE_BOOM at=(%d,%d) (spawn point buried)\n",
                    G.worm[wid].x, G.worm[wid].y);
        return 0;
    }
    std::printf("TICK 0 x=%lld y=%lld (%.4f,%.4f) vx=%lld vy=%lld life=%d\n",
                (long long)p.x, (long long)p.y,
                (double)p.x / Q, (double)p.y / Q,
                (long long)p.vx, (long long)p.vy, p.life);
    int tick = 0;
    while (p.active && tick < 600) {
        tick++;
        proj_tick(G, wid / 3);
        if (!p.active) {
            std::printf("TICK %d ENDED\n", tick);
            break;
        }
        std::printf("TICK %d x=%lld y=%lld (%.4f,%.4f) vx=%lld vy=%lld life=%d dug=%d "
                    "stuck=%d\n", tick, (long long)p.x, (long long)p.y,
                    (double)p.x / Q, (double)p.y / Q, (long long)p.vx,
                    (long long)p.vy, p.life, p.dug, p.stuck ? 1 : 0);
    }
    std::printf("HASH %016llx\n", (unsigned long long)G.hash);
    return 0;
}

// ---------------------------------------------------------------- trace 模式
static int mode_trace(int argc, char **argv) {
    if (argc < 5) {
        std::printf("usage: oracle trace <map> <seed> <script.txt> [mask]\n");
        return 2;
    }
    const std::string map = argv[2];
    const int seed = std::atoi(argv[3]);
    const std::string script = read_text(argv[4]);
    int mask = (argc > 5) ? std::atoi(argv[5]) : 0b1111;
    if (mask <= 0 || mask >= (1 << SEATS)) mask = 0b1111;

    Game G;
    G.seed = seed;
    G.active_mask = mask;
    G.debug_mode = 1;
    const std::string err = load_map(G, map, false);
    if (!err.empty()) { std::printf("ERR %s\n", err.c_str()); return 1; }

    const int na = active_seat_count(G);
    std::printf("# map=%s seed=%d mask=%d seats=%d\n", map.c_str(), seed, mask, na);
    std::printf("STEP 0 %016llx\n", (unsigned long long)G.hash);
    for (int s = 0; s < SEATS; s++) {
        if (!seat_active(G, s)) continue;
        for (int i = s * WORMS_PER_SEAT; i < (s + 1) * WORMS_PER_SEAT; i++)
            std::printf("WORM 0 %d %d %d %d %d %d %d\n", i, G.worm[i].x, G.worm[i].y,
                        G.worm[i].hp, G.worm[i].grenades, G.worm[i].drills,
                        G.worm[i].alive ? 1 : 0);
    }

    // 一行一轮；空行跳过。
    std::vector<std::string> lines;
    {
        std::istringstream ls(script);
        std::string line;
        while (std::getline(ls, line)) {
            while (!line.empty() && (line.back() == '\r' || line.back() == '\n'))
                line.pop_back();
            if (!line.empty() && line[0] != '#') lines.push_back(line);
        }
    }

    int round = 0;
    for (const std::string &line : lines) {
        if (G.over) break;
        round++;
        trace_hooks::boom_round = round;
        trace_hooks::boom_ord = 0;
        Order orders[SEATS];
        {
            size_t pos = 0;
            int idx = 0;
            while (pos <= line.size() && idx < SEATS) {
                const size_t bar = line.find('|', pos);
                const std::string seg = line.substr(
                    pos, bar == std::string::npos ? std::string::npos : bar - pos);
                orders[idx] = parse_order_line(seg);
                orders[idx].ok = !seg.empty();
                idx++;
                if (bar == std::string::npos) break;
                pos = bar + 1;
            }
        }
        do_round(G, orders);
        std::printf("STEP %d %016llx\n", round, (unsigned long long)G.hash);
        for (int s = 0; s < SEATS; s++) {
            if (!seat_active(G, s)) continue;
            for (int i = s * WORMS_PER_SEAT; i < (s + 1) * WORMS_PER_SEAT; i++)
                std::printf("WORM %d %d %d %d %d %d %d %d\n", round, i, G.worm[i].x,
                            G.worm[i].y, G.worm[i].hp, G.worm[i].grenades,
                            G.worm[i].drills, G.worm[i].alive ? 1 : 0);
        }
        for (const auto &b : trace_hooks::booms) {
            if (b.round != round) continue;
            std::printf("BOOM %d %d %d %d %s %d\n", b.round, b.ord, b.x, b.y,
                        weapon_name_of(b.kind), b.owner);
        }
        trace_hooks::booms.clear();
        std::printf("RESULT %d winner=%d reason=%s\n", round, G.winner,
                    G.reason.c_str());
        if (G.over) break;
    }
    return 0;
}

// ---------------------------------------------------------------- wind 模式
static int mode_wind(int argc, char **argv) {
    if (argc < 4) {
        std::printf("usage: oracle wind <map> <seed> [rounds]\n");
        return 2;
    }
    const int seed = std::atoi(argv[3]);
    const int rounds = (argc > 4) ? std::atoi(argv[4]) : 40;
    std::printf("# seed=%d: wind = (seed %% 21) - 10 on round 1, then splitmix64 at "
                "6/11/16/21/26/31/36\n");
    for (int r = 1; r <= rounds; r++) {
        const bool changed = is_wind_round(r) || r == 1;
        std::printf("ROUND %d wind=%d%s\n", r, wind_for_round(seed, r),
                    changed ? " CHANGED" : "");
    }
    return 0;
}

// ---------------------------------------------------------------- main
#ifndef P01_ENGINE_NO_MAIN
int main(int argc, char **argv) {
    if (argc < 2) {
        std::printf(
            "oracle — read-only trajectory reference for P01 BALLISTIC\n"
            "\n"
            "  oracle shot  <map> <seed> <worm_id> <angle> <power> [weapon]\n"
            "  oracle trace <map> <seed> <script.txt> [mask]\n"
            "  oracle wind  <map> <seed> [rounds]\n"
            "\n"
            "It prints the engine's own numbers (Q16.16 raw integers + fixed-point\n"
            "decimals). Match your replica against them; do not guess.\n");
        return 2;
    }
    const std::string mode = argv[1];
    if (mode == "shot") return mode_shot(argc, argv);
    if (mode == "trace") return mode_trace(argc, argv);
    if (mode == "wind") return mode_wind(argc, argv);
    std::printf("ERR unknown mode '%s'\n", mode.c_str());
    return 2;
}
#endif

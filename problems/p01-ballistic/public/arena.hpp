// P01 BALLISTIC · 选手公开头文件
//
// 提供三样东西：
//   1. INIT / TURN 解析
//   2. 地形查询
//   3. **弹道预演** simulate()——这是规则第 16.2 节明确允许的 helper。
//      它和引擎用同一套定点物理（Q16.16、同一张三角表），但不做爆炸和地形破坏。
//
// 不提供：瞄准答案、基准策略、隐藏地图。

#pragma once

#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "engine/trig.hpp"

namespace arena {

constexpr int W = 96;
constexpr int H = 54;
constexpr int ROUNDS = 40;
constexpr int WORM_HP = 100;
constexpr int AP_MAX = 3;
constexpr int Q = 65536;
constexpr int GRAVITY = 655;
constexpr int WIND_UNIT = 8;

enum Weapon { W_ROCKET = 1, W_GRENADE = 2, W_DRILL = 3 };

// Q16.16 -> 整数格坐标。显式 floor，不用 `>> 16`。
// 有符号右移在 C++17 是实现定义，不能拿"主流编译器恰好一致"当保证。
inline int64_t floor16(int64_t v) {
    return v >= 0 ? (int64_t)((uint64_t)v >> 16) : -(((-v) + 65535) >> 16);
}

struct Worm {
    int id = 0;
    int x = 0, y = 0;
    int hp = WORM_HP;
    int grenades = 2;
    int drills = 2;
    bool mine = false;
    bool alive = true;
};

struct State {
    int round = 0;
    int wind = 0;
    int water = 50;
    int alive_mine = 0;
    int alive_enemy = 0;
    Worm worm[6];
    std::vector<std::string> grid;
};

struct Init {
    bool ok = false;
    int seat = 0;
    int rounds = ROUNDS;
    int water = 50;
    int wind = 0;
    std::string map_id;
    Worm worm[6];
    std::vector<std::string> grid;
};

// ---------------------------------------------------------------- 地形

inline bool in_map(int x, int y) { return x >= 0 && x < W && y >= 0 && y < H; }

inline bool is_solid(const std::vector<std::string> &g, int x, int y) {
    if (!in_map(x, y)) return true;
    const char c = g[y][x];
    return c == '#' || c == 'X';
}

inline bool is_bedrock(const std::vector<std::string> &g, int x, int y) {
    if (!in_map(x, y)) return true;
    return g[y][x] == 'X';
}

inline bool is_water(int water, int y) { return y >= water; }

inline bool is_air(const std::vector<std::string> &g, int water, int x, int y) {
    return in_map(x, y) && !is_solid(g, x, y) && !is_water(water, y);
}

inline int worm_at(const State &s, int x, int y) {
    for (int i = 0; i < 6; i++) {
        if (!s.worm[i].alive) continue;
        if (s.worm[i].x == x && s.worm[i].y == y) return i;
    }
    return -1;
}

inline int manhattan(int ax, int ay, int bx, int by) {
    const int dx = ax > bx ? ax - bx : bx - ax;
    const int dy = ay > by ? ay - by : by - ay;
    return dx + dy;
}

// ---------------------------------------------------------------- 弹道预演

struct Shot {
    bool valid = false;
    int hit_x = 0, hit_y = 0;     // 命中/引爆的格
    int ticks = 0;
    bool hit_worm = false;
    bool into_water = false;
    bool out_of_map = false;
};

// 与引擎同源的定点推进：只算轨迹和首次碰撞，不结算伤害、不破坏地形。
inline Shot simulate(const std::vector<std::string> &grid, int water, int wind,
                     int wx, int wy, int angle, int power, int kind,
                     int self_id = -1, const State *st = nullptr) {
    Shot out;
    if (angle < 0 || angle > 180 || power < 0 || power > 100) return out;
    out.valid = true;

    int64_t vy_q = 0, vx_q = 0;
    if (kind == W_DRILL) {
        const int64_t speed = Q / 4;
        vx_q = floor16(speed * p01::cos_q16(angle));
        vy_q = -floor16(speed * p01::sin_q16(angle));
    } else {
        const int64_t speed = (int64_t)power * Q / 100;
        vx_q = floor16(speed * p01::cos_q16(angle));
        vy_q = -floor16(speed * p01::sin_q16(angle));
    }

    int64_t px = ((int64_t)wx * Q) + Q / 2 + floor16((int64_t)p01::cos_q16(angle) * (Q / 2));
    int64_t py = ((int64_t)wy * Q) + Q / 2 - floor16((int64_t)p01::sin_q16(angle) * (Q / 2));

    int life = (kind == W_ROCKET) ? 300 : (kind == W_GRENADE ? 180 : 48);
    int dug = 0;

    const int gx0 = (int)floor16(px), gy0 = (int)floor16(py);
    if (is_solid(grid, gx0, gy0)) {
        out.hit_x = wx;
        out.hit_y = wy;
        return out;
    }

    for (int tick = 0; tick < life; tick++) {
        if (kind == W_DRILL) {
            const int64_t x0 = px, y0 = py;
            px += vx_q;
            py += vy_q;
            const int64_t dxs = px - x0, dys = py - y0;
            int64_t amax = (dxs < 0 ? -dxs : dxs);
            const int64_t ady = (dys < 0 ? -dys : dys);
            if (ady > amax) amax = ady;
            const int sub = 1 + (int)(amax / (Q / 4));
            for (int k = 1; k <= sub; k++) {
                const int cx = (int)floor16(x0 + dxs * k / sub);
                const int cy = (int)floor16(y0 + dys * k / sub);
                if (!in_map(cx, cy)) {
                    out.out_of_map = true;
                    out.hit_x = cx;
                    out.hit_y = cy;
                    return out;
                }
                if (is_water(water, cy)) {
                    out.into_water = true;
                    out.hit_x = cx;
                    out.hit_y = cy;
                    return out;
                }
                if (st != nullptr) {
                    const int wid = worm_at(*st, cx, cy);
                    if (wid >= 0 && !(wid == self_id && tick < 6)) {
                        out.hit_worm = true;
                        out.hit_x = cx;
                        out.hit_y = cy;
                        out.ticks = tick;
                        return out;
                    }
                }
                if (is_solid(grid, cx, cy)) {
                    if (grid[cy][cx] == '#' && dug < 3) {
                        dug++;
                        continue;
                    }
                    out.hit_x = cx;
                    out.hit_y = cy;
                    out.ticks = tick;
                    return out;
                }
            }
            continue;
        }

        vx_q += (int64_t)wind * WIND_UNIT;
        vy_q += GRAVITY;

        const int64_t x0 = px, y0 = py;
        px += vx_q;
        py += vy_q;
        const int64_t dxs = px - x0, dys = py - y0;
        int64_t amax = (dxs < 0 ? -dxs : dxs);
        const int64_t ady = (dys < 0 ? -dys : dys);
        if (ady > amax) amax = ady;
        const int sub = 1 + (int)(amax / (Q / 4));
        for (int k = 1; k <= sub; k++) {
            const int cx = (int)floor16(x0 + dxs * k / sub);
            const int cy = (int)floor16(y0 + dys * k / sub);
            if (!in_map(cx, cy)) {
                out.out_of_map = true;
                out.hit_x = cx;
                out.hit_y = cy;
                return out;
            }
            if (is_water(water, cy)) {
                out.into_water = true;
                out.hit_x = cx;
                out.hit_y = cy;
                return out;
            }
            if (st != nullptr) {
                const int wid = worm_at(*st, cx, cy);
                if (wid >= 0 && !(wid == self_id && tick < 6)) {
                    out.hit_worm = true;
                    out.hit_x = cx;
                    out.hit_y = cy;
                    out.ticks = tick;
                    return out;
                }
            }
            if (is_solid(grid, cx, cy)) {
                out.hit_x = cx;
                out.hit_y = cy;
                out.ticks = tick;
                return out;
            }
        }
    }

    out.hit_x = (int)floor16(px);
    out.hit_y = (int)floor16(py);
    return out;
}

// 爆炸半径与中心伤害，与引擎一致
inline int blast_radius(int kind) { return kind == W_ROCKET ? 2 : (kind == W_GRENADE ? 3 : 1); }
inline int blast_base(int kind) { return kind == W_ROCKET ? 25 : (kind == W_GRENADE ? 35 : 20); }

// 估算一发对某个格子目标的伤害（不模拟地形变化）
inline int estimate_damage(int kind, int cx, int cy, int tx, int ty) {
    const int r = blast_radius(kind);
    const int dx = cx > tx ? cx - tx : tx - cx;
    const int dy = cy > ty ? cy - ty : ty - cy;
    const int d = dx > dy ? dx : dy;
    if (d > r) return 0;
    return blast_base(kind) * (r + 1 - d) / (r + 1);
}

// ---------------------------------------------------------------- 解析

inline void chomp(std::string &s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n')) s.pop_back();
}

inline std::string grid_payload(const std::string &line) {
    std::string rest = line.substr(5);
    if (!rest.empty() && rest[0] == ' ') rest = rest.substr(1);
    if (rest.size() > (size_t)W) rest = rest.substr(0, W);
    return rest;
}

inline bool parse_worm_line(const std::string &line, Worm &w);

inline void reparse_worms(const std::vector<std::string> &worm_lines, Worm *out) {
    for (const auto &l : worm_lines) {
        Worm w;
        if (parse_worm_line(l, w) && w.id >= 0 && w.id < 6) out[w.id] = w;
    }
}

inline bool parse_worm_line(const std::string &line, Worm &w) {
    std::istringstream ss(line);
    std::string key;
    ss >> key;
    if (key != "worm") return false;
    std::string side;
    ss >> w.id >> w.x >> w.y >> w.hp >> w.grenades >> w.drills >> side;
    w.mine = (side == "mine");
    w.alive = w.hp > 0;
    return true;
}

inline Init read_init(std::istream &in) {
    Init I;
    std::string line;
    bool started = false;
    while (std::getline(in, line)) {
        chomp(line);
        if (line == "INIT") { started = true; break; }
    }
    if (!started) return I;
    std::vector<std::string> worm_lines;
    while (std::getline(in, line)) {
        chomp(line);
        if (line.empty()) continue;
        if (line == "END") {
            reparse_worms(worm_lines, I.worm);
            I.ok = I.grid.size() == (size_t)H;
            return I;
        }
        std::istringstream ss(line);
        std::string key;
        ss >> key;
        if (key == "seat") ss >> I.seat;
        else if (key == "rounds") ss >> I.rounds;
        else if (key == "water") ss >> I.water;
        else if (key == "wind") ss >> I.wind;
        else if (key == "map") ss >> I.map_id;
        else if (key == "worm") worm_lines.push_back(line);
        else if (key == "grid") I.grid.push_back(grid_payload(line));
    }
    return I;
}

inline bool read_turn(std::istream &in, State &S) {
    std::string line;
    bool started = false;
    while (std::getline(in, line)) {
        chomp(line);
        if (line.empty()) continue;
        if (line.rfind("TURN", 0) == 0) {
            std::istringstream ss(line);
            std::string tag;
            ss >> tag >> S.round;
            started = true;
            break;
        }
    }
    if (!started) return false;

    S.grid.clear();
    for (int i = 0; i < 6; i++) S.worm[i].alive = false;
    std::vector<std::string> worm_lines;
    while (std::getline(in, line)) {
        chomp(line);
        if (line.empty()) continue;
        if (line == "END") {
            reparse_worms(worm_lines, S.worm);
            return S.grid.size() == (size_t)H;
        }
        std::istringstream ss(line);
        std::string key;
        ss >> key;
        if (key == "wind") ss >> S.wind;
        else if (key == "water") ss >> S.water;
        else if (key == "alive") ss >> S.alive_mine >> S.alive_enemy;
        else if (key == "worm") worm_lines.push_back(line);
        else if (key == "grid") S.grid.push_back(grid_payload(line));
    }
    return false;
}

}  // namespace arena

// P01 BALLISTIC · 4 席选手公开头文件
//
// 裁判每轮向 bot 发送一个 TURN 块：
//   TURN <seat> <grid_rows>
//   STATE <round> <wind> <water> <over> <winner> <hp0>..<hp3> <dealt0>..<dealt3>
//         <reason> <bad0>..<bad3> <hash> <12 × (x y hp grenades drills alive)>
//   GRID <row>                       × grid_rows 行（当前为 54）
//   END
//
// TURN 头的第三个字段是随后 GRID 的行数：54 表示本块带完整地形，0 表示裁判
// 不下发地形（旧版判法）。地形块固定是 54 行 × 96 列，字符含义：
//   '#' 泥土（可被爆炸/钻头破坏）  'X' 基岩（不可破坏）  '.' 空气
// 水位由 STATE 的 water 给出：y >= water 的格在水下，不能站立。
// 地形每轮重发，已破坏的墙会反映在下一轮的 GRID 里。
//
// 解析后的地形在 State::grid；用 has_grid() 判断是否有地形，用 solid_at /
// air_at / worm_at 查询单格。aim_at 的可选 grid 参数会按发到的地形截断弹道
// （只做单发轨迹，不做多步搜索）。
//
// 应答格式是 `<act> <cmds>`，act 为 seat*3..seat*3+2 中的存活虫；
// 例如 `4 F ROCKET 45 70`。动作之间用 `;` 分隔，F 必须是最后一个动作。

#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace arena4 {

constexpr int W = 96;
constexpr int H = 54;
constexpr int SEATS = 4;
constexpr int WORMS_PER_SEAT = 3;
constexpr int WORM_COUNT = SEATS * WORMS_PER_SEAT;
constexpr int ROUNDS = 40;
constexpr int WORM_HP = 100;
constexpr int AP_MAX = 3;
constexpr int MOVE_AP = 1;
constexpr int JUMP_AP = 2;
constexpr int WIND_BIAS = 32;   // STATE 里的风是 实际风 + 32
constexpr int Q = 65536;        // 引擎定点 1.0
constexpr int GRAVITY = 655;    // 0.01 格/tick^2
constexpr int WIND_UNIT = 8;    // 每点风每 tick 的水平加速度
constexpr int ROCKET_LIFE = 300;

enum Weapon { W_ROCKET = 1, W_GRENADE = 2, W_DRILL = 3 };

struct Worm {
    int id = 0;
    int seat = 0;      // id / 3
    int x = 0;
    int y = 0;
    int hp = 0;
    int grenades = 0;
    int drills = 0;
    bool alive = false;
};

inline int manhattan(int ax, int ay, int bx, int by) {
    const int dx = ax > bx ? ax - bx : bx - ax;
    const int dy = ay > by ? ay - by : by - ay;
    return dx + dy;
}

// ---------------------------------------------------------------- 地形

inline bool in_map(int x, int y) { return x >= 0 && x < W && y >= 0 && y < H; }

inline bool is_solid(const std::vector<std::string> &g, int x, int y) {
    if (!in_map(x, y)) return true;
    if (y >= (int)g.size() || x >= (int)g[y].size()) return true;
    const char c = g[y][x];
    return c == '#' || c == 'X';
}

inline bool is_bedrock(const std::vector<std::string> &g, int x, int y) {
    if (!in_map(x, y)) return true;
    if (y >= (int)g.size() || x >= (int)g[y].size()) return true;
    return g[y][x] == 'X';
}

inline bool is_water(int water, int y) { return y >= water; }

inline bool is_air(const std::vector<std::string> &g, int water, int x, int y) {
    return in_map(x, y) && !is_solid(g, x, y) && !is_water(water, y);
}

struct State {
    bool ok = false;       // STATE 行是否解析成功
    int seat = -1;         // 来自 TURN <seat>
    int round = 0;
    int wind = 0;          // 已减去 WIND_BIAS，与引擎内部一致，范围约 -10..10
    int water = 50;
    bool over = false;
    int winner = -1;
    int hp[SEATS] = {};    // 每席存活虫 HP 之和
    int dealt[SEATS] = {}; // 每席累计造成伤害
    std::string reason;    // RUNNING / ELIMINATION / HP / DAMAGE / WORMS / DRAW
    bool bad[SEATS] = {};  // 上一步该席是否出现非法动作
    uint64_t hash = 0;
    Worm worm[WORM_COUNT]; // seat s 拥有 worm[s*3 .. s*3+2]
    int grid_rows = 0;     // TURN 头声明的 GRID 行数；0 = 本轮没有地形块
    std::vector<std::string> grid; // grid_rows 行 × W 列；空 = 没有可用地形

    bool has_grid() const { return grid.size() == static_cast<std::size_t>(H); }
    const std::vector<std::string> *grid_or_null() const {
        return has_grid() ? &grid : nullptr;
    }

    bool is_own(int id) const {
        return id >= 0 && id < WORM_COUNT && worm[id].seat == seat;
    }
    bool is_enemy(int id) const {
        return seat >= 0 && seat < SEATS && id >= 0 && id < WORM_COUNT
               && worm[id].seat != seat;
    }
    bool own_alive(int id) const { return is_own(id) && worm[id].alive; }
    bool enemy_alive(int id) const { return is_enemy(id) && worm[id].alive; }

    // 单格查询；没有地形时一律返回 false（未知），调用方先用 has_grid() 分支，
    // 别把未知当成空气或墙。
    bool solid_at(int x, int y) const { return has_grid() && is_solid(grid, x, y); }
    bool air_at(int x, int y) const {
        return has_grid() && is_air(grid, water, x, y);
    }
    int worm_at(int x, int y) const {
        for (int i = 0; i < WORM_COUNT; i++) {
            if (worm[i].alive && worm[i].x == x && worm[i].y == y) return i;
        }
        return -1;
    }

    int own_alive_count() const {
        if (seat < 0 || seat >= SEATS) return 0;
        int n = 0;
        for (int i = seat * WORMS_PER_SEAT; i < seat * WORMS_PER_SEAT + WORMS_PER_SEAT; i++)
            if (worm[i].alive) n++;
        return n;
    }
    int enemy_alive_count() const {
        int n = 0;
        for (int i = 0; i < WORM_COUNT; i++)
            if (enemy_alive(i)) n++;
        return n;
    }
    int first_own_alive() const {
        for (int i = 0; i < WORMS_PER_SEAT; i++) {
            const int id = seat * WORMS_PER_SEAT + i;
            if (id >= 0 && id < WORM_COUNT && worm[id].alive) return id;
        }
        return -1;
    }
    // 离 act 最近的存活敌人；没有则 -1。
    int nearest_enemy(int act) const {
        if (act < 0 || act >= WORM_COUNT) return -1;
        int best = -1, best_d = 1 << 30;
        for (int i = 0; i < WORM_COUNT; i++) {
            if (!enemy_alive(i)) continue;
            const int d = manhattan(worm[act].x, worm[act].y, worm[i].x, worm[i].y);
            if (d < best_d) { best_d = d; best = i; }
        }
        return best;
    }
};

// ---------------------------------------------------------------- 合法性

inline bool valid_dir(char d) { return d == 'L' || d == 'R'; }
inline bool valid_angle(int angle) { return angle >= 0 && angle <= 180; }
inline bool valid_power(int power) { return power >= 0 && power <= 100; }

inline const char *weapon_name(int weapon) {
    if (weapon == W_ROCKET) return "ROCKET";
    if (weapon == W_GRENADE) return "GRENADE";
    if (weapon == W_DRILL) return "DRILL";
    return "";
}

inline bool has_ammo(const State &s, int act, int weapon) {
    if (act < 0 || act >= WORM_COUNT) return false;
    if (weapon == W_ROCKET) return true;
    if (weapon == W_GRENADE) return s.worm[act].grenades > 0;
    if (weapon == W_DRILL) return s.worm[act].drills > 0;
    return false;
}

// ACT 属于本席、存活，且角度/力度/弹药都合法。动作顺序和 AP 预算由调用方保证。
inline bool valid_fire(const State &s, int act, int weapon, int angle, int power) {
    return s.own_alive(act) && valid_angle(angle) && valid_power(power)
           && has_ammo(s, act, weapon);
}

// ---------------------------------------------------------------- 解析

inline void chomp(std::string &s) {
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n')) s.pop_back();
}

inline bool starts_with(const std::string &s, const char *prefix) {
    const std::string p(prefix);
    return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}

// `GRID <row>` -> 96 字符的行；多余列截掉，缺失列由 is_solid 边界兜住。
inline std::string grid_payload(const std::string &line) {
    std::string rest = line.substr(5);
    if (!rest.empty() && rest[0] == ' ') rest = rest.substr(1);
    if (rest.size() > static_cast<std::size_t>(W)) rest = rest.substr(0, W);
    return rest;
}

inline bool parse_state_line(const std::string &line, State &S) {
    S.ok = false;
    std::istringstream ss(line);
    std::string tok;
    std::string t[20 + WORM_COUNT * 6];
    int n = 0;
    while (ss >> tok && n < (int)(sizeof(t) / sizeof(t[0]))) t[n++] = tok;
    if (n < 20 + WORM_COUNT * 6 || t[0] != "STATE") return false;

    S.round = std::atoi(t[1].c_str());
    S.wind = std::atoi(t[2].c_str()) - WIND_BIAS;
    S.water = std::atoi(t[3].c_str());
    S.over = std::atoi(t[4].c_str()) != 0;
    S.winner = std::atoi(t[5].c_str());
    for (int s = 0; s < SEATS; s++) {
        S.hp[s] = std::atoi(t[6 + s].c_str());
        S.dealt[s] = std::atoi(t[10 + s].c_str());
        S.bad[s] = std::atoi(t[15 + s].c_str()) != 0;
    }
    S.reason = t[14];
    S.hash = std::strtoull(t[19].c_str(), nullptr, 16);

    for (int i = 0; i < WORM_COUNT; i++) {
        const int o = 20 + i * 6;
        Worm w;
        w.id = i;
        w.seat = i / WORMS_PER_SEAT;
        w.x = std::atoi(t[o].c_str());
        w.y = std::atoi(t[o + 1].c_str());
        w.hp = std::atoi(t[o + 2].c_str());
        w.grenades = std::atoi(t[o + 3].c_str());
        w.drills = std::atoi(t[o + 4].c_str());
        w.alive = std::atoi(t[o + 5].c_str()) != 0;
        S.worm[i] = w;
    }
    S.ok = true;
    return true;
}

// 读一个 TURN 块：TURN 头 + STATE 行 + 可选的 GRID×N + END。读到 EOF 返回 false。
// 非 TURN 的行会被跳过，因此也能容忍旧 harness 在前面加 INIT 段，或上一轮
// 遗留的 GRID/END 行（解析器没读完也不影响下一轮）。
inline bool read_turn(std::istream &in, State &S) {
    std::string line;
    bool got_turn = false;
    while (std::getline(in, line)) {
        chomp(line);
        if (!starts_with(line, "TURN ")) continue;
        std::istringstream hs(line);
        std::string tag;
        int seat = -1;
        int rows = 0;
        hs >> tag >> seat >> rows;
        if (seat < 0 || seat >= SEATS) continue;
        S.seat = seat;
        S.grid_rows = rows > 0 ? rows : 0;
        got_turn = true;
        break;
    }
    if (!got_turn) return false;

    S.ok = false;
    S.grid.clear();
    while (std::getline(in, line)) {
        chomp(line);
        if (line.empty()) continue;
        if (!starts_with(line, "STATE ")) continue;
        parse_state_line(line, S);
        break;
    }
    if (!S.ok) return false;

    // 地形块：只有 TURN 头声明了行数才继续读，旧裁判（没有第四个字段）不会卡住。
    // 协议保证 N 行 GRID 之后紧跟一行 END。
    for (int i = 0; i < S.grid_rows; i++) {
        if (!std::getline(in, line)) return false;
        chomp(line);
        if (line == "END") return true;   // 行数对不上时提前收尾，别空转
        if (starts_with(line, "GRID ")) S.grid.push_back(grid_payload(line));
    }
    if (S.grid_rows > 0) std::getline(in, line);  // 吃掉 END
    return true;
}

// ---------------------------------------------------------------- 近似瞄准

struct Aim {
    bool ok = false;
    int angle = 90;
    int power = 50;
    double miss = 1e9;  // 轨迹离目标格中心最近的距离（格）
};

// 与引擎同序的连续近似：先加风/重力，再前进。忽略虫体碰撞；
// 传入 grid 时按地形截断（撞墙后不再前进），不传则和旧版一样只算理想弹道。
inline double flight_miss(int wx, int wy, int tx, int ty, int wind,
                          int angle, int power, int water,
                          const std::vector<std::string> *grid = nullptr) {
    const double pi = 3.14159265358979323846;
    const double rad = angle * pi / 180.0;
    const double speed = power / 100.0;
    double px = wx + 0.5 + 0.5 * std::cos(rad);
    double py = wy + 0.5 - 0.5 * std::sin(rad);
    double vx = speed * std::cos(rad);
    double vy = -speed * std::sin(rad);
    const double ax = wind * (double)WIND_UNIT / Q;
    const double ay = (double)GRAVITY / Q;
    const double cx = tx + 0.5, cy = ty + 0.5;

    double best = (px - cx) * (px - cx) + (py - cy) * (py - cy);
    for (int tick = 0; tick < ROCKET_LIFE; tick++) {
        vx += ax;
        vy += ay;
        px += vx;
        py += vy;
        const double d2 = (px - cx) * (px - cx) + (py - cy) * (py - cy);
        if (d2 < best) best = d2;
        if (px < 0.0 || px >= (double)W || py >= (double)water) break;
        if (grid && py >= 0.0 && py < (double)H
            && is_solid(*grid, (int)std::floor(px), (int)std::floor(py))) break;
    }
    return std::sqrt(best);
}

// 粗搜 5..175 度、10..100 力度，再在最优解附近按 1 度/1 力度细化。
// grid 非空时按真实地形截断弹道（墙后目标自然拿不到低 miss），
// 仍是单发轨迹评估，不做多步搜索。
inline Aim aim_at(int wx, int wy, int tx, int ty, int wind, int water = H,
                  const std::vector<std::string> *grid = nullptr) {
    Aim out;
    int best_a = 90, best_p = 50;
    for (int a = 5; a <= 175; a += 5) {
        for (int p = 10; p <= 100; p += 5) {
            const double miss = flight_miss(wx, wy, tx, ty, wind, a, p, water, grid);
            if (miss < out.miss) {
                out.miss = miss;
                best_a = a;
                best_p = p;
            }
        }
    }
    const int a0 = best_a > 4 ? best_a - 4 : 0;
    const int a1 = best_a < 176 ? best_a + 4 : 180;
    const int p0 = best_p > 4 ? best_p - 4 : 0;
    const int p1 = best_p < 96 ? best_p + 4 : 100;
    for (int a = a0; a <= a1; a++) {
        for (int p = p0; p <= p1; p++) {
            const double miss = flight_miss(wx, wy, tx, ty, wind, a, p, water, grid);
            if (miss < out.miss) {
                out.miss = miss;
                best_a = a;
                best_p = p;
            }
        }
    }
    out.angle = best_a;
    out.power = best_p;
    out.ok = true;
    return out;
}

}  // namespace arena4
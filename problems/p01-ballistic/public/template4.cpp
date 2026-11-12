// P01 BALLISTIC · 4 席 Starter
//
// 保证：能编译、能接 judge4 跑完整局、只发协议内的指令。
// 策略刻意做得简单：每轮选离敌人最近的己方存活虫，朝最近的敌人打一发火箭。
// 弹道用 TURN 块里的 GRID 做地形截断：墙后的目标算不出低 miss，这时沿地形
// 朝敌人走一格（先查空气和占位）。裁判不下发地形时退回理想弹道，不做地形假设。
//
// 注意：4 席应答是 `<act> <cmds>`，不像 2 席模板那样带轮次前缀。

#include <iostream>

#include "arena4.hpp"

int main() {
    arena4::State s;
    while (arena4::read_turn(std::cin, s)) {
        int me = -1, foe = -1, best = 1 << 30;
        if (s.ok) {
            for (int i = 0; i < arena4::WORM_COUNT; i++) {
                if (!s.own_alive(i)) continue;
                const int e = s.nearest_enemy(i);
                if (e < 0) continue;
                const int d = arena4::manhattan(s.worm[i].x, s.worm[i].y,
                                                s.worm[e].x, s.worm[e].y);
                if (d < best) { best = d; me = i; foe = e; }
            }
        }

        if (me < 0 || foe < 0) {
            int act = s.first_own_alive();
            if (act < 0) act = s.seat >= 0 ? s.seat * arena4::WORMS_PER_SEAT : 0;
            std::cout << act << " WAIT\n" << std::flush;
            continue;
        }

        const arena4::Worm &a = s.worm[me];
        const arena4::Worm &b = s.worm[foe];
        const arena4::Aim aim = arena4::aim_at(a.x, a.y, b.x, b.y, s.wind, s.water,
                                               s.grid_or_null());
        if (aim.ok && aim.miss <= 3.0
            && arena4::valid_fire(s, me, arena4::W_ROCKET, aim.angle, aim.power)) {
            std::cout << me << " F ROCKET " << aim.angle << ' ' << aim.power
                      << '\n' << std::flush;
            continue;
        }

        // 弹道被地形挡住（或没有安全射击解）：用 GRID 朝敌人走一格。
        if (s.has_grid()) {
            const int dir = b.x >= a.x ? 1 : -1;
            const int nx = a.x + dir;
            if (s.air_at(nx, a.y) && s.worm_at(nx, a.y) < 0) {
                std::cout << me << " M " << (dir > 0 ? 'R' : 'L')
                          << '\n' << std::flush;
                continue;
            }
        }
        std::cout << me << " WAIT\n" << std::flush;
    }
    return 0;
}

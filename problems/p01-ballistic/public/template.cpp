// P01 BALLISTIC · Starter
//
// 保证：能编译、能跑完整局、不违反协议。
// 策略刻意做得笨：选第一条活虫，朝敌人方向走三格，走不动就 45 度 70 力度打一发。
// 它不预演弹道，也不看风。

#include <iostream>
#include <string>

#include "arena.hpp"

int main() {
    arena::Init init = arena::read_init(std::cin);
    if (!init.ok) return 1;

    for (;;) {
        arena::State s;
        if (!arena::read_turn(std::cin, s)) break;

        int me = -1;
        for (int i = 0; i < 6; i++) {
            if (s.worm[i].mine && s.worm[i].alive) { me = i; break; }
        }
        if (me < 0) {
            std::cout << s.round << " 0 WAIT\n" << std::flush;
            continue;
        }

        int foe = -1;
        for (int i = 0; i < 6; i++) {
            if (!s.worm[i].mine && s.worm[i].alive) { foe = i; break; }
        }
        const arena::Worm w = s.worm[me];

        std::string cmds;
        int x = w.x;
        if (foe >= 0) {
            const arena::Worm e = s.worm[foe];
            const int d = arena::manhattan(w.x, w.y, e.x, e.y);
            if (d > 10) {
                const char dir = (e.x > w.x) ? 'R' : 'L';
                const int dx = (dir == 'R') ? 1 : -1;
                for (int k = 0; k < 3; k++) {
                    const int nx = x + dx;
                    if (!arena::is_air(s.grid, s.water, nx, w.y)) break;
                    if (arena::worm_at(s, nx, w.y) >= 0) break;
                    if (!cmds.empty()) cmds += "; ";
                    cmds += std::string("M ") + dir;
                    x = nx;
                }
            }
        }

        if (!cmds.empty()) {
            std::cout << s.round << ' ' << me << ' ' << cmds << '\n' << std::flush;
            continue;
        }

        const int angle = (foe >= 0 && s.worm[foe].x < w.x) ? 135 : 45;
        std::cout << s.round << ' ' << me << " F ROCKET " << angle << " 70\n" << std::flush;
    }
    return 0;
}

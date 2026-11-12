// SPDX-License-Identifier: Apache-2.0
// P01 BALLISTIC · public random baseline (4 seats)
//
// Independent of the private grading anchors.  It never moves and never
// reads time/files: each turn it picks a live own worm and fires a random
// legal rocket.  Useful as a floor for local self-checks.
#include <cstdint>
#include <iostream>

#include "arena4.hpp"

namespace {
uint32_t g_state = 0x9e3779b9u;
bool g_seeded = false;

uint32_t next_u32() {
    g_state ^= g_state << 13;
    g_state ^= g_state >> 17;
    g_state ^= g_state << 5;
    return g_state;
}

int pick(int n) {
    return n <= 0 ? 0 : static_cast<int>(next_u32() % static_cast<uint32_t>(n));
}
}  // namespace

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    arena4::State s;
    while (arena4::read_turn(std::cin, s)) {
        if (!g_seeded) {
            g_state ^= static_cast<uint32_t>(s.seat + 1) * 0x85ebca6bu;
            g_seeded = true;
        }
        if (!s.ok || s.seat < 0 || s.seat >= arena4::SEATS) {
            const int act = s.seat >= 0 ? s.seat * arena4::WORMS_PER_SEAT : 0;
            std::cout << act << " WAIT\n" << std::flush;
            continue;
        }

        int own[arena4::WORMS_PER_SEAT];
        int n_own = 0;
        for (int i = s.seat * arena4::WORMS_PER_SEAT;
             i < (s.seat + 1) * arena4::WORMS_PER_SEAT; i++) {
            if (s.own_alive(i)) own[n_own++] = i;
        }
        if (n_own == 0) {
            int act = s.first_own_alive();
            if (act < 0) act = s.seat * arena4::WORMS_PER_SEAT;
            std::cout << act << " WAIT\n" << std::flush;
            continue;
        }

        const int act = own[pick(n_own)];
        bool fired = false;
        for (int tries = 0; tries < 32; tries++) {
            const int angle = pick(181);
            const int power = 10 + pick(91);
            if (arena4::valid_fire(s, act, arena4::W_ROCKET, angle, power)) {
                std::cout << act << " F ROCKET " << angle << ' ' << power
                          << '\n' << std::flush;
                fired = true;
                break;
            }
        }
        if (!fired) {
            std::cout << act << " F ROCKET 90 50\n" << std::flush;
        }
    }
    return 0;
}

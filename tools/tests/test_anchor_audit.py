"""Anchor audit must count unique effective openings, not repeated deterministic matches."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anchor_audit


class OpeningCoverage(unittest.TestCase):
    def test_p06_deduplicates_core_hp_variants_per_map(self):
        maps = [Path(f"public-0{i}.txt") for i in range(1, 4)]
        cases = anchor_audit.opening_cases("p06-core-tactics", maps, 12)
        keys = {(mp.stem, variant) for _seed, mp, variant in cases}
        self.assertEqual(len(cases), 6)
        self.assertEqual(len(keys), 6)
        for mp in maps:
            self.assertEqual({variant for name, variant in keys if name == mp.stem}, {0, 1})

    def test_p08_covers_three_gold_openings_once(self):
        cases = anchor_audit.opening_cases("p08-tower-duel", [], 12)
        self.assertEqual([seed for seed, _mp, _variant in cases], [1, 2, 3])
        self.assertEqual([variant for _seed, _mp, variant in cases], [1, 2, 0])

    def test_unbounded_seed_games_keep_requested_coverage(self):
        maps = [Path(f"public-0{i}.txt") for i in range(1, 4)]
        with self.subTest(game="p01-ballistic"):
            cases = anchor_audit.opening_cases("p01-ballistic", maps, 12)
            self.assertEqual(len(cases), 12)
        p07 = Path(__file__).resolve().parents[2] / "problems" / "p07-maze-hunt" / "public" / "maps"
        maps = sorted(p07.glob("*.txt"))
        with self.subTest(game="p07-maze-hunt"):
            cases = anchor_audit.opening_cases("p07-maze-hunt", maps, 12)
            self.assertEqual(len(cases), 12)
            self.assertEqual(len({anchor_audit.effective_opening_key("p07-maze-hunt", mp, seed)
                                  for seed, mp, _variant in cases}), 12)

    def test_p07_deduplicates_identical_seed_generated_grids(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "map.txt"
            rows = [list("." * 21) for _ in range(21)]
            rows[0][0] = "P"
            for ch, (x, y) in zip("1234", ((2, 2), (4, 4), (6, 6), (8, 8))):
                rows[y][x] = ch
            path.write_text("\n".join("".join(row) for row in rows), encoding="utf-8")
            self.assertEqual(anchor_audit.effective_opening_key("p07-maze-hunt", path, 21),
                             anchor_audit.effective_opening_key("p07-maze-hunt", path, 42))
            cases = anchor_audit.opening_cases("p07-maze-hunt", [path], 84)
            zero_cut_seeds = [seed for seed, _mp, _variant in cases if seed % 21 == 0]
            self.assertEqual(zero_cut_seeds, [21])
            self.assertEqual(len({anchor_audit.effective_opening_key("p07-maze-hunt", path, seed)
                                  for seed, _mp, _variant in cases}), len(cases))


if __name__ == "__main__":
    unittest.main()

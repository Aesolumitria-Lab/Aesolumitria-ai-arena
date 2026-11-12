"""Schedule regression: even map counts must not skip every second map."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tournament


class MapCoverage(unittest.TestCase):
    def test_round_robin_even_maps(self):
        maps = [Path(f"map-{i}.txt") for i in range(6)]
        units = tournament.round_robin_units(["a", "b", "c"], 6, [1, 2], maps, True)
        for pair in ({"a", "b"}, {"a", "c"}, {"b", "c"}):
            rows = [u for u in units if {u["a"], u["b"]} == pair]
            self.assertEqual({u["map"] for u in rows}, {p.stem for p in maps})
            self.assertEqual(len(rows), 6)
            self.assertEqual([u["seed"] for u in rows], [1, 2, 1, 2, 1, 2])

    def test_no_map_game_and_determinism(self):
        args = (["a", "b"], 4, [0, 1, 2], [], True)
        rows = tournament.round_robin_units(*args)
        self.assertEqual(rows, tournament.round_robin_units(*args))
        self.assertEqual({r["map"] for r in rows}, {"-"})
        self.assertEqual([r["a"] for r in rows], ["a", "b", "a", "b"])


class ArtifactCoverage(unittest.TestCase):
    def test_fingerprints_change_with_every_participant_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            judge = root / "public" / "judge" / "judge.py"
            judge.parent.mkdir(parents=True)
            judge.write_text("judge-v1", encoding="utf-8")
            engine = root / "engine.exe"
            bot = root / "bot.exe"
            mp = root / "public-01.txt"
            engine.write_bytes(b"engine-v1")
            bot.write_bytes(b"bot-v1")
            mp.write_text("map-v1", encoding="utf-8")
            meta = {"version": "1.0.0", "rules_hash": "rules-v1", "engine_hash": "engine-v1"}

            def current():
                return tournament.artifact_fingerprints(root, meta, engine, {"bot": bot}, [mp])

            base = current()
            self.assertEqual(base, current())
            self.assertEqual(len(base["bot_binary_sha256"]["bot"]), len("sha256:") + 64)
            for name, path in (("judge", judge), ("engine", engine),
                               ("bot", bot), ("map", mp)):
                original = path.read_bytes()
                path.write_bytes(original + b"-changed")
                with self.subTest(changed=name):
                    self.assertNotEqual(base, current())
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()

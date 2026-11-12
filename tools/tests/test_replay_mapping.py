import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import replay


class ReplayMapping(unittest.TestCase):
    def test_p01_keeps_initial_snapshot_as_first_replay_frame(self):
        records = [
            {"type": "header", "game": "p01-ballistic", "version": "2.0.0"},
            {"type": "map", "id": "fixture", "grid": ["map"]},
            {"type": "game", "label": "A", "seat_bots": [0, 1],
             "initial_state": {"t": 1, "state_hash": "initial-hash",
                               "state": {"hp": [300, 300], "grid": ["map"],
                                         "worms": []}}},
            {"type": "turn", "t": 2, "orders": {"0": "0 WAIT", "1": "3 WAIT"},
             "state_hash": "turn-hash", "state": {"hp": [300, 300],
                                                       "grid": ["map"], "worms": []}},
        ]
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "fixture.jsonl"
            source.write_text("\n".join(json.dumps(r) for r in records) + "\n",
                              encoding="utf-8")
            rep = replay.load_replay(source)
            self.assertEqual(rep["games"][0]["initial_state"]["t"], 1)
            output = Path(td) / "fixture.html"
            replay.render_html(rep, output)
            html = output.read_text(encoding="utf-8")
            self.assertIn("START T", html)
            self.assertIn("initial-hash", html)
            self.assertIn('"initial_state"', html)

    def test_p06_normalizes_bot_totals_without_relabeling_actions(self):
        class Engine:
            def __init__(self, exe): pass
            def load(self, *args, **kwargs): pass
            def step(self, *args): return {"core": [3, 5], "hash": "h"}
            def close(self): pass
        class Judge: pass
        Judge.Engine = Engine
        games = [{"label": tag, "first_seat": 0, "seat_bots": mapping,
                  "turns": [{"seat": 0, "t": 1, "orders": {"0": "0XNX"}, "state_hash": "h"}]}
                 for tag, mapping in (("A", [0, 1]), ("B", [1, 0]))]
        rep = {"header": {"game": "p06-core-tactics", "seed": 0}, "map": {"grid": ["map"]},
               "games": games, "result": {"games": [{"core": [3, 5]}, {"core": [3, 5]}], "total_core": [8, 8]}}
        with tempfile.TemporaryDirectory() as td:
            r = replay.Report("p06-core-tactics", Path("fixture"))
            replay._v_p06(Judge, Path("exe"), rep, Path(td), r)
            self.assertTrue(r.out["ok"])
            rep["result"]["total_core"] = [6, 10]
            r = replay.Report("p06-core-tactics", Path("fixture"))
            replay._v_p06(Judge, Path("exe"), rep, Path(td), r)
            self.assertFalse(r.out["ok"])
            self.assertEqual(r.out["result_mismatch"][0]["what"], "match total_core")


if __name__ == "__main__":
    unittest.main()

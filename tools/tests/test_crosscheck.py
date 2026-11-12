import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import crosscheck


class TraceEvidence(unittest.TestCase):
    def test_failed_load_is_not_evidence(self):
        with patch("crosscheck.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="ERR bad map\n", stderr="")):
            with self.assertRaisesRegex(RuntimeError, "failed to load"):
                crosscheck.run_trace(Path("engine"), Path("."), ["LOAD broken"])

    def test_empty_comparison_fails(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td)/"a.json", Path(td)/"b.json"
            for payload in ({}, {"case": []}):
                a.write_text(json.dumps(payload)); b.write_text(json.dumps(payload))
                self.assertEqual(crosscheck.do_compare(str(a), str(b)), 1)

    def test_games_keep_separate_judges_and_case_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games = [root/"p01-test", root/"p06-test"]
            for g in games:
                src = g/"public/judge/judge.py"
                src.parent.mkdir(parents=True)
                src.write_text(f"def build_engine(game):\n    return {g.name!r}\n")
            report = root/"trace.json"
            builders = {g.name: lambda _: [{"tag":"same", "lines":[]}] for g in games}
            def trace(exe, game, lines):
                self.assertEqual(exe, game.name)
                return ["0123456789abcdef"]
            with patch.dict(crosscheck.BUILDERS, builders), patch("crosscheck.run_trace", side_effect=trace):
                self.assertEqual(crosscheck.do_run(games, str(report)), 0)
            self.assertEqual(set(json.loads(report.read_text())), {g.name+"/same" for g in games})

    def test_matching_nonempty_comparison(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td)/"a.json", Path(td)/"b.json"
            a.write_text(json.dumps({"game/case": ["0123456789abcdef"]}))
            b.write_text(a.read_text())
            self.assertEqual(crosscheck.do_compare(str(a), str(b)), 0)


if __name__ == "__main__":
    unittest.main()

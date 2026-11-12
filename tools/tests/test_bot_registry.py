"""Bot registry identity / integrity tests (standard library only)."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot_registry  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_entry(root: Path, dirname: str, *, source: bytes = b"int main(){return 0;}\n",
                **overrides) -> dict:
    entry_dir = root / dirname
    entry_dir.mkdir(parents=True)
    (entry_dir / "bot.cpp").write_bytes(source)
    meta = {
        "schema": 1,
        "id": "demo@harness#1",
        "display_name": "Demo",
        "model": "demo",
        "model_key": "demo",
        "harness": "harness",
        "run": 1,
        "entry": "bot.cpp",
        "language": "cpp",
        "code_sha256": sha256(source),
    }
    meta.update(overrides)
    (entry_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


class IdGrammar(unittest.TestCase):
    def test_valid_ids(self):
        for value in ("some-model-1.2@some-harness#1", "random4@anchors#12", "a@b#1"):
            with self.subTest(value=value):
                self.assertTrue(bot_registry.is_valid_id(value))

    def test_rejects_bad_ids(self):
        for value in (
            "Random@harness#1", "demo@harness#0", "demo@harness#01",
            "demo@harness", "demo#1", "a..b@h#1", "a/b@h#1", "a\\b@h#1",
            "a@b#1#2", "a@b@c#1", "a@b#1 " + "x" * 80,
        ):
            with self.subTest(value=value):
                self.assertFalse(bot_registry.is_valid_id(value))


class Validation(unittest.TestCase):
    def test_valid_registry_and_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "demo@harness#1")
            report = bot_registry.validate_entries(root)
            self.assertTrue(report["ok"], report)
            rec = bot_registry.resolve_entry(root, "demo@harness#1")
            self.assertEqual(rec["entry_rel"], "bot.cpp")
            self.assertEqual(rec["code_sha256"], sha256(b"int main(){return 0;}\n"))

    def test_duplicate_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "first")
            write_entry(root, "second")
            report = bot_registry.validate_entries(root)
            self.assertFalse(report["ok"])
            self.assertTrue(any(e["what"] == "duplicate_id" for e in report["errors"]))
            with self.assertRaises(bot_registry.BotRegistryError):
                bot_registry.resolve_entry(root, "demo@harness#1")

    def test_wrong_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "demo@harness#1", code_sha256="0" * 64)
            report = bot_registry.validate_entries(root)
            self.assertFalse(report["ok"])
            self.assertIn("code_sha256 不匹配", " ".join(report["entries"][0]["errors"]))

    def test_invalid_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "bad", id="demo@harness#0", run=0)
            report = bot_registry.validate_entries(root)
            self.assertFalse(report["ok"])
            self.assertFalse(report["entries"][0]["ok"])

    def test_id_must_match_model_harness_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "mismatch", id="other@harness#1")
            report = bot_registry.validate_entries(root)
            self.assertFalse(report["ok"])
            self.assertIn("不一致", " ".join(report["entries"][0]["errors"]))

    def test_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            for raw in ("../outside.cpp", "sub/../../outside.cpp", "C:/outside.cpp",
                        "/outside.cpp"):
                with self.subTest(entry=raw):
                    root = Path(td) / f"case{abs(hash(raw))}"
                    root.mkdir()
                    (root / "outside.cpp").write_text("// outside\n", encoding="utf-8")
                    write_entry(root, "demo@harness#1", entry=raw)
                    report = bot_registry.validate_entries(root)
                    self.assertFalse(report["ok"])
                    errors = " ".join(report["entries"][0]["errors"])
                    self.assertIn("entry", errors)

    def test_missing_entry_and_bad_language(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "demo@harness#1", entry="missing.cpp", language="python")
            report = bot_registry.validate_entries(root)
            self.assertFalse(report["ok"])
            errors = " ".join(report["entries"][0]["errors"])
            self.assertIn("不存在", errors)
            self.assertIn("language", errors)

    def test_dir_name_mismatch_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_entry(root, "some-directory")
            report = bot_registry.validate_entries(root)
            self.assertTrue(report["ok"], report)
            self.assertTrue(report["entries"][0]["warnings"])


if __name__ == "__main__":
    unittest.main()

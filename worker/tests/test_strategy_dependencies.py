"""Dependency integrity failures must disable the professional strategy module."""

from __future__ import annotations

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[2] / "skills/clinical-protocol-workbench"
          / "scripts/verify_strategy_dependencies.py")
SPEC = importlib.util.spec_from_file_location("strategy_dependencies", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class StrategyDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "references").mkdir()
        (self.root / "SKILL.md").write_text("synthetic skill\n")
        (self.root / "references/indication.md").write_text("synthetic reference\n")
        self.lock = {
            "schema_version": 1,
            "modules": {"synthetic": {
                "repository": "https://example.invalid/synthetic",
                "commit": "1" * 40,
                "files": {p.relative_to(self.root).as_posix(): checker.sha256(p.read_bytes())
                          for p in self.root.rglob("*") if p.is_file()},
            }},
        }

    def check(self):
        return checker.verify_module(self.lock, "synthetic", self.root)

    def test_exact_copy_passes_without_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.check()["status"], "matched")
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_changed_reference_fails_even_with_unchanged_entrypoint(self):
        (self.root / "references/indication.md").write_text("changed recommendation\n")
        result = self.check()
        self.assertEqual(result["status"], "mismatch")
        self.assertIn({"kind": "hash_mismatch", "path": "references/indication.md"}, result["errors"])

    def test_missing_root_disables_module(self):
        result = checker.verify_module(self.lock, "synthetic", self.root / "not-installed")
        self.assertEqual(result["status"], "missing")

    def test_missing_reference_disables_module(self):
        (self.root / "references/indication.md").unlink()
        self.assertEqual(self.check()["status"], "missing")

    def test_added_instruction_reference_fails(self):
        (self.root / "references/extra.md").write_text("unreviewed instructions\n")
        self.assertEqual(self.check()["status"], "mismatch")

    def test_symlink_cannot_substitute_for_pinned_file(self):
        reference = self.root / "references/indication.md"
        payload = reference.read_bytes()
        reference.unlink()
        other = self.root / "outside.txt"
        other.write_bytes(payload)
        reference.symlink_to(other)
        self.assertEqual(self.check()["status"], "mismatch")

    def test_unlocked_symlink_directory_fails(self):
        (self.root / "references/extra").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.check()["status"], "mismatch")

    def test_unknown_module_and_schema_rejected(self):
        with self.assertRaises(ValueError):
            checker.verify_module(self.lock, "unknown", self.root)
        self.lock["schema_version"] = 2
        with self.assertRaises(ValueError):
            self.check()

    def test_lock_path_cannot_escape_root(self):
        for path in ("../outside.md", "/outside.md", "references/../SKILL.md", "./SKILL.md"):
            with self.subTest(path=path):
                lock = copy.deepcopy(self.lock)
                lock["modules"]["synthetic"]["files"][path] = "0" * 64
                with self.assertRaises(ValueError):
                    checker.verify_module(lock, "synthetic", self.root)


if __name__ == "__main__":
    unittest.main()

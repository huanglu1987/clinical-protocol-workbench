"""Installer failure tests use isolated fake dependencies; no network or user skills."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "skills/clinical-protocol-workbench"
SPEC = importlib.util.spec_from_file_location("bundle_test_target", CORE / "scripts/install_bundle.py")
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "skills"
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("core")
        self.core = {"name": bundle.CORE, "files": bundle.fingerprints(self.source)}
        self.dep_source = self.root / "dependency"
        self.dep_source.mkdir()
        (self.dep_source / "SKILL.md").write_text("dependency")
        self.dep = {"name": "test-dependency", "files": bundle.fingerprints(self.dep_source)}

    def fake_fetch(self, item, stage, installer):
        bundle.shutil.copytree(self.dep_source, stage / item["name"])

    def test_core_and_idempotent_skip(self):
        rows = bundle.install([self.core], self.source, self.dest, None)
        self.assertEqual(rows[0]["action"], "installed")
        with patch.object(bundle, "fetch", side_effect=AssertionError("network called")):
            rows = bundle.install([self.core], self.source, self.dest, None)
        self.assertEqual(rows[0]["action"], "skipped")

    def test_conflict_preserves_user_edit_and_installs_nothing(self):
        bundle.install([self.core], self.source, self.dest, None)
        (self.dest / bundle.CORE / "SKILL.md").write_text("user changes")
        with self.assertRaises(bundle.BundleError):
            bundle.install([self.core, self.dep], self.source, self.dest, None)
        self.assertFalse((self.dest / self.dep["name"]).exists())
        self.assertEqual((self.dest / bundle.CORE / "SKILL.md").read_text(), "user changes")

    def test_additional_source_file_is_conflict(self):
        bundle.install([self.core], self.source, self.dest, None)
        (self.dest / bundle.CORE / "extra.md").write_text("user addition")
        self.assertEqual(bundle.inventory([self.core], self.dest)[0]["status"], "conflict")

    def test_generated_caches_do_not_cause_conflict(self):
        bundle.install([self.core], self.source, self.dest, None)
        cache = self.dest / bundle.CORE / "__pycache__"
        cache.mkdir()
        (cache / "test.pyc").write_bytes(b"cache")
        self.assertEqual(bundle.inventory([self.core], self.dest)[0]["status"], "matched")

    def test_symlink_rejected(self):
        (self.source / "linked.md").symlink_to(self.dep_source / "SKILL.md")
        with self.assertRaises(bundle.BundleError):
            bundle.fingerprints(self.source)

    def test_download_failure_leaves_no_installed_core(self):
        with patch.object(bundle, "find_installer", return_value=Path("unused")), patch.object(bundle, "fetch", side_effect=bundle.BundleError("download failed")):
            with self.assertRaises(bundle.BundleError):
                bundle.install([self.core, self.dep], self.source, self.dest, None)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_hash_mismatch_before_install(self):
        bad = {**self.dep, "files": {"SKILL.md": "0" * 64}}
        with patch.object(bundle, "find_installer", return_value=Path("unused")), patch.object(bundle, "fetch", side_effect=self.fake_fetch):
            with self.assertRaises(bundle.BundleError):
                bundle.install([self.core, bad], self.source, self.dest, None)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_standard_installs_all_and_reads_back(self):
        with patch.object(bundle, "find_installer", return_value=Path("unused")), patch.object(bundle, "fetch", side_effect=self.fake_fetch):
            rows = bundle.install([self.core, self.dep], self.source, self.dest, None)
        self.assertTrue(all(row["status"] == "matched" for row in rows))
        self.assertEqual(bundle.fingerprints(self.dest / self.dep["name"]), self.dep["files"])

    def test_publish_failure_rolls_back_new_trees_only(self):
        original_copy = bundle.shutil.copytree
        def fail_publish(src, dst, *args, **kwargs):
            if Path(dst) == self.dest / self.dep["name"]:
                raise OSError("simulated disk error")
            return original_copy(src, dst, *args, **kwargs)
        with patch.object(bundle, "find_installer", return_value=Path("unused")), patch.object(bundle, "fetch", side_effect=self.fake_fetch), patch.object(bundle.shutil, "copytree", side_effect=fail_publish):
            with self.assertRaises(OSError):
                bundle.install([self.core, self.dep], self.source, self.dest, None)
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_concurrent_install_is_blocked(self):
        self.dest.mkdir()
        guard = self.dest / ".clinical-protocol-bundle-install.lock"
        guard.mkdir()
        with self.assertRaises(bundle.BundleError):
            bundle.install([self.core], self.source, self.dest, None)
        self.assertTrue(guard.exists())

    def test_root_download_keeps_subdirectories(self):
        item = {**self.dep, "repo": "example/skill", "commit": "a" * 40, "path": "."}
        with patch.object(bundle.subprocess, "run") as run:
            run.return_value.returncode = 0
            bundle.fetch(item, self.root, Path("installer.py"))
        self.assertEqual(run.call_args.args[0][-1], "download")

    def test_root_archive_failure_uses_exact_commit_full_git(self):
        item = {**self.dep, "repo": "example/skill", "commit": "a" * 40, "path": "."}
        with patch.object(bundle.subprocess, "run") as run:
            from types import SimpleNamespace
            run.side_effect = [SimpleNamespace(returncode=n) for n in [1, 0, 0, 0, 0]]
            bundle.fetch(item, self.root, Path("installer.py"))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["git", "-C", str(self.root / item["name"]), "fetch", "--depth=1", "origin", item["commit"]], commands)
        self.assertFalse(any("sparse-checkout" in command for command in commands))

    def test_release_manifest_and_both_profiles(self):
        lock = bundle.load_lock(CORE)
        self.assertEqual(len(lock["skills"]), 7)
        self.assertEqual(len([s for s in lock["skills"] if s["name"] == bundle.CORE]), 1)
        strategy = json.loads((CORE / "assets/strategy-dependencies.lock.json").read_text())
        for item in lock["skills"]:
            if item["name"] in strategy["modules"]:
                expected = strategy["modules"][item["name"]]
                self.assertEqual(item["commit"], expected["commit"])
                self.assertTrue(all(item["files"][f] == sha for f, sha in expected["files"].items()))


if __name__ == "__main__":
    unittest.main()

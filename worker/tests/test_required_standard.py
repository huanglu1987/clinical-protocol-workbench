import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("required_standard", ROOT / "skills/clinical-protocol-workbench/scripts/check_writing_standard.py")
standard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(standard)


class RequiredStandardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.md"
        self.data = "虚构方法文件\n第二行\n第三行\n".encode()
        self.source.write_bytes(self.data)
        self.lock = {"filename": "synthetic.md", "content_version": "test", "sha256": hashlib.sha256(self.data).hexdigest(), "line_count": 3}
        self.lockpath = self.root / "lock.json"
        self.lockpath.write_text(json.dumps(self.lock))

    def test_exact_file_verified_but_not_attested_read(self):
        output = io.StringIO()
        with patch.object(standard, "LOCK", self.lockpath), contextlib.redirect_stdout(output):
            code = standard.main(["--path", str(self.source)])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "matched")
        self.assertEqual(report["agent_read_complete"], "not_attested")

    def test_missing_blocks(self):
        with self.assertRaises(FileNotFoundError):
            standard.verify(self.root / "missing.md", self.lock)

    def test_tamper_blocks(self):
        self.source.write_bytes(self.data + b"changed")
        with self.assertRaises(ValueError):
            standard.verify(self.source, self.lock)

    def test_explicit_bad_path_does_not_fall_back(self):
        bad = self.root / "missing.md"
        self.assertEqual(standard.locate(bad, self.lock), bad)
        with self.assertRaises(FileNotFoundError):
            standard.verify(standard.locate(bad, self.lock), self.lock)

    def test_local_install_idempotent_and_source_preserved(self):
        with patch.object(standard, "standard_home", return_value=self.root / "support"):
            first = standard.install_source(self.source, self.lock)
            second = standard.install_source(self.source, self.lock)
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_local_install_never_overwrites_other_content(self):
        support = self.root / "support"
        support.mkdir()
        target = support / "synthetic.md"
        target.write_text("existing content")
        with patch.object(standard, "standard_home", return_value=support), self.assertRaises(ValueError):
            standard.install_source(self.source, self.lock)
        self.assertEqual(target.read_text(), "existing content")

    def test_chunk_output_covers_requested_lines_and_rejects_invalid_range(self):
        output = io.StringIO()
        with patch.object(standard, "LOCK", self.lockpath), contextlib.redirect_stdout(output):
            self.assertEqual(standard.main(["--path", str(self.source), "--show", "--start", "2", "--end", "3"]), 0)
        self.assertTrue(output.getvalue().endswith("2: 第二行\n3: 第三行\n"))
        with patch.object(standard, "LOCK", self.lockpath), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(standard.main(["--path", str(self.source), "--show", "--start", "4"]), 1)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "sync_version", Path(__file__).resolve().parents[1] / "scripts" / "sync-version.py"
)
sync_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_module)


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        (self.root / "package.json").write_text(
            json.dumps({"version": "1.2.3"}), encoding="utf-8"
        )
        self.project = self.root / "pyproject.toml"
        self.project.write_bytes(
            b'[tool.poetry]\r\nname = "custom-name"\r\nversion = "0.1.0"\r\n'
            b'[tool.poetry.dependencies]\r\ntorch = { version = "2.6.0" }\r\n'
        )
        self.backend = self.root / "backend" / "__init__.py"
        self.backend.write_bytes(b'__all__ = ["__version__"]\n__version__ = "0.1.0"\n')

    def test_sync_preserves_unrelated_content_and_is_idempotent(self):
        original = self.project.read_bytes()
        self.assertTrue(sync_module.sync_version(self.root))
        self.assertEqual(self.project.read_bytes(), original.replace(b"0.1.0", b"1.2.3"))
        self.assertIn(b'__version__ = "1.2.3"', self.backend.read_bytes())
        modified = self.project.stat().st_mtime_ns
        self.assertTrue(sync_module.sync_version(self.root))
        self.assertEqual(modified, self.project.stat().st_mtime_ns)
        self.assertTrue(sync_module.sync_version(self.root, check=True))

    def test_check_detects_drift_without_writing(self):
        originals = [path.read_bytes() for path in (self.project, self.backend)]
        self.assertFalse(sync_module.sync_version(self.root, check=True))
        self.assertEqual(
            originals, [path.read_bytes() for path in (self.project, self.backend)]
        )

    def test_missing_field_fails_before_any_write(self):
        original = self.project.read_bytes()
        self.backend.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            sync_module.sync_version(self.root)
        self.assertEqual(original, self.project.read_bytes())


if __name__ == "__main__":
    unittest.main()

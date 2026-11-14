import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("experiment_queue", Path(__file__).with_name("experiment_queue.py"))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


class ArchiveTests(unittest.TestCase):
    def test_project_bundle_excludes_outputs_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"; root.mkdir()
            (root / "src").mkdir(); (root / "src" / "train.py").write_text("print('ok')", encoding="utf-8")
            (root / "runs").mkdir(); (root / "runs" / "old.log").write_text("old")
            (root / ".env.local").write_text("SECRET=not-uploaded")
            archive = Path(td) / "source.zip"
            manifest = q.write_package(root, archive)
            self.assertEqual([x["path"] for x in manifest["files"]], ["src/train.py"])
            target = Path(td) / "unpacked"
            got = q.safe_extract(archive, target, q.hash_file(archive))
            self.assertEqual(got["files"][0]["sha256"], q.hash_file(target / "src/train.py"))

    def test_bad_archive_paths_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("../escape", "/absolute", "C:/drive", "a\\b", "a:stream", "NUL.txt", "trailing."):
                with self.subTest(name=name), self.assertRaises(RuntimeError): q.safe_rel(name)
            archive = Path(td) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as z: z.writestr("../escape", "x")
            with self.assertRaises(RuntimeError): q.safe_extract(archive, Path(td) / "out")

    def test_short_json_password_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "config.json").write_text('{"password":"short"}')
            with self.assertRaises(RuntimeError): q.project_files(root)

    def test_tampered_package_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"; root.mkdir(); (root / "a.py").write_text("x")
            archive = Path(td) / "a.zip"; q.write_package(root, archive)
            with self.assertRaises(RuntimeError): q.safe_extract(archive, Path(td) / "out", "0" * 64)

    def test_hardcoded_credential_stops_submission_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "config.py").write_text("password = 'abcDEF0123456789'")
            with self.assertRaises(RuntimeError): q.project_files(root)


if __name__ == "__main__": unittest.main()

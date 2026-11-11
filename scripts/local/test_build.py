import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("local_build", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class SourceVerificationTests(unittest.TestCase):
    def test_exact_patch_accepts_new_files_but_rejects_extra_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sources/test"
            source.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=source, text=True,
                                               env=os.environ | {"GIT_CONFIG_NOSYSTEM": "1"})
            git("init", "-q")
            git("config", "user.name", "Local build test")
            git("config", "user.email", "test@example.invalid")
            git("config", "commit.gpgsign", "false")
            (source / "main.txt").write_text("original\n")
            git("add", "main.txt")
            git("commit", "-qm", "test fixture")
            commit = git("rev-parse", "HEAD").strip()
            (source / "policy.txt").write_text("reviewed policy\n")
            git("add", "-N", "policy.txt")
            delta = git("diff", "--binary", "HEAD")
            git("reset", "-q", "HEAD", "policy.txt")
            patch_dir = root / "local-build/patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "test.patch").write_text(delta)
            source_spec = {"commit": commit, "patch": "patches/test.patch",
                           "patchSha256": hashlib.sha256(delta.encode()).hexdigest()}
            with patch.object(build, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build.source(root, "test", source_spec), source)
                (source / "unexpected.go").write_text("package main\n")
                with self.assertRaisesRegex(RuntimeError, "Unrecorded tracked/untracked"):
                    build.source(root, "test", source_spec)
                (source / "unexpected.go").unlink()
                (source / "main.txt").write_text("unreviewed change\n")
                with self.assertRaisesRegex(RuntimeError, "Unrecorded tracked/untracked"):
                    build.source(root, "test", source_spec)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from team.sandbox import sandbox_command


class SandboxCommandTests(unittest.TestCase):
    def test_disabled_returns_unwrapped_copy(self):
        command = ["example", "--flag"]
        result = sandbox_command(
            command,
            ["/does/not/need/to/exist"],
            enabled=False,
            readable_paths=["/also/missing"],
        )
        self.assertEqual(result, command)
        self.assertIsNot(result, command)

    def test_rejects_missing_command(self):
        with self.assertRaises(ValueError):
            sandbox_command([], [], enabled=False)

    @unittest.skipIf(sys.platform == "linux", "requires a non-Linux system")
    def test_rejects_unsupported_system(self):
        with self.assertRaises(RuntimeError):
            sandbox_command(["true"], [])

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap requires Linux")
    def test_rejects_missing_bwrap(self):
        with mock.patch("team.sandbox.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                sandbox_command(["true"], [])

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap requires Linux")
    def test_builds_read_only_root_and_exact_overrides(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as root:
            writable = Path(root, "output")
            writable.mkdir()
            public = Path(root, "public")
            public.mkdir()
            with mock.patch(
                "team.sandbox.shutil.which", return_value="/usr/bin/bwrap"
            ):
                command = sandbox_command(
                    ["tool", "argument"],
                    [writable],
                    readable_paths=[public],
                )

        self.assertEqual(
            command[:6],
            [
                "/usr/bin/bwrap",
                "--die-with-parent",
                "--unshare-user",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-uts",
            ],
        )
        self.assertIn(["--ro-bind", "/", "/"], self._triples(command))
        self.assertIn(["--tmpfs", "/opt/data"], self._pairs(command))
        self.assertIn(["--tmpfs", "/tmp"], self._pairs(command))
        self.assertIn(["--bind", str(writable), str(writable)], self._triples(command))
        self.assertNotIn(
            ["--ro-bind", str(public), str(public)], self._triples(command)
        )
        self.assertEqual(command[-3:], ["--", "tool", "argument"])

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap requires Linux")
    def test_restores_only_exact_opt_data_paths(self):
        public = Path("/opt/data/skills")
        credential = Path("/opt/data/.env")
        with mock.patch(
            "team.sandbox.shutil.which", return_value="/usr/bin/bwrap"
        ), mock.patch("team.sandbox.Path.exists", return_value=True), mock.patch(
            "team.sandbox.Path.is_symlink", return_value=False
        ):
            command = sandbox_command(
                ["true"], [], readable_paths=[public, credential, "/usr"]
            )

        triples = self._triples(command)
        self.assertIn(["--ro-bind", str(public), str(public)], triples)
        self.assertIn(["--ro-bind", str(credential), str(credential)], triples)
        self.assertNotIn(["--ro-bind", "/opt/data", "/opt/data"], triples)
        self.assertNotIn(["--ro-bind", "/usr", "/usr"], triples)

    @unittest.skipUnless(sys.platform == "linux", "bubblewrap requires Linux")
    def test_rejects_broad_missing_and_symbolic_link_paths(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root, "target")
            target.mkdir()
            link = Path(root, "link")
            link.symlink_to(target, target_is_directory=True)
            with mock.patch(
                "team.sandbox.shutil.which", return_value="/usr/bin/bwrap"
            ):
                for broad_path in ("/", "/opt", "/opt/data"):
                    with self.subTest(path=broad_path), self.assertRaises(ValueError):
                        sandbox_command(["true"], [broad_path])
                with self.assertRaises(ValueError):
                    sandbox_command(["true"], [link])
                with self.assertRaises(ValueError):
                    sandbox_command(["true"], [Path(root, "missing")])

    @staticmethod
    def _pairs(command):
        return [command[index : index + 2] for index in range(len(command) - 1)]

    @staticmethod
    def _triples(command):
        return [command[index : index + 3] for index in range(len(command) - 2)]


@unittest.skipUnless(sys.platform == "linux", "bubblewrap requires Linux")
class BubblewrapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("bwrap") is None:
            raise unittest.SkipTest("bwrap is unavailable")

    def setUp(self):
        base = "/workspace" if Path("/workspace").is_dir() else "/var/tmp"
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="hermes-bwrap-test-", dir=base
        )
        self.root = Path(self.temporary_directory.name)
        self.allowed = self.root / "allowed"
        self.protected = self.root / "protected"
        self.allowed.mkdir()
        self.protected.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def run_python(self, source):
        return subprocess.run(
            sandbox_command([sys.executable, "-c", source], [self.allowed]),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_allows_host_mount_write_and_rename_in_exact_directory(self):
        source = (
            "from pathlib import Path; import os; "
            f"first=Path({str(self.allowed / 'first.txt')!r}); "
            f"second=Path({str(self.allowed / 'second.txt')!r}); "
            "first.write_text('ok'); os.rename(first, second)"
        )
        result = self.run_python(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.allowed / "second.txt").read_text(), "ok")

    def test_denies_protected_write_and_cross_directory_rename(self):
        protected_file = self.protected / "target.txt"
        protected_file.write_text("original")
        allowed_file = self.allowed / "source.txt"
        allowed_file.write_text("move")
        write_result = self.run_python(
            "from pathlib import Path; "
            f"Path({str(protected_file)!r}).write_text('changed')"
        )
        moved_file = self.protected / "moved.txt"
        rename_result = self.run_python(
            f"import os; os.rename({str(allowed_file)!r}, {str(moved_file)!r})"
        )
        self.assertNotEqual(write_result.returncode, 0)
        self.assertNotEqual(rename_result.returncode, 0)
        self.assertEqual(protected_file.read_text(), "original")
        self.assertTrue(allowed_file.exists())

    def test_denies_write_through_symlink_into_protected_directory(self):
        protected_file = self.protected / "target.txt"
        protected_file.write_text("original")
        link = self.allowed / "outside.txt"
        link.symlink_to(protected_file)
        result = self.run_python(
            f"from pathlib import Path; Path({str(link)!r}).write_text('changed')"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(protected_file.read_text(), "original")


if __name__ == "__main__":
    unittest.main(verbosity=2)

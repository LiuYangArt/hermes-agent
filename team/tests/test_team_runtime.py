"""Real Linux tool execution against a disposable team home and workspace."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from gateway.session_context import reset_session_vars, set_session_vars
from team import governance


@unittest.skipUnless(sys.platform == "linux", "Requires the team Docker sandbox")
class TeamRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="team-test-home-")
        self.home = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home), "FEISHU_APP_ID": "cli_test", "HERMES_WRITE_SAFE_ROOT": "/opt/data:/workspace/artifacts"})
        self.env.start()
        (self.home / "config.yaml").write_text("team_governance:\n  enabled: true\n  app_id: cli_test\nplatforms:\n  feishu:\n    extra:\n      admins: [ou_admin]\nterminal:\n  backend: local\n  cwd: /workspace\n")
        Path("/workspace/artifacts").mkdir(parents=True, exist_ok=True)
        self.outputs = tempfile.TemporaryDirectory(dir="/workspace/artifacts")
        self.out = Path(self.outputs.name)
        governance._load.cache_clear()
        reset_session_vars()
        set_session_vars(platform="feishu", user_id="ou_member", message_id="om_test")

    def tearDown(self):
        from tools.file_tools import clear_file_ops_cache
        from tools.terminal_tool import cleanup_all_environments
        clear_file_ops_cache()
        cleanup_all_environments()
        reset_session_vars()
        governance._load.cache_clear()
        self.env.stop()
        self.outputs.cleanup()
        self.temp.cleanup()

    def test_real_file_tools_write_public_artifact_and_reject_shared_paths(self):
        from tools.file_tools import read_file_tool, write_file_tool, patch_tool
        output = self.out / "result.txt"
        result = json.loads(write_file_tool(str(output), "before"))
        self.assertFalse(result.get("error"), result)
        self.assertEqual(output.read_text(), "before")
        result = json.loads(patch_tool(path=str(output), old_string="before", new_string="after"))
        self.assertFalse(result.get("error"), result)
        self.assertEqual(output.read_text(), "after")
        before = (self.home / "config.yaml").read_bytes()
        self.assertIn("error", json.loads(write_file_tool(str(self.home / "config.yaml"), "changed", cross_profile=True)))
        self.assertEqual((self.home / "config.yaml").read_bytes(), before)
        self.assertIn("error", json.loads(read_file_tool(str(self.home / "config.yaml"))))

    def test_local_process_cannot_escape_through_symlink_or_python(self):
        from tools.environments.local import LocalEnvironment
        secret = self.home / "protected"
        secret.write_text("unchanged")
        link = self.out / "link"
        link.symlink_to(secret)
        env = LocalEnvironment(cwd="/workspace")
        try:
            result = env.execute(f"printf forbidden > {link}")
            self.assertNotEqual(result["returncode"], 0)
            self.assertEqual(secret.read_text(), "unchanged")
            result = env.execute(f"cat {secret}")
            self.assertNotEqual(result["returncode"], 0)
        finally:
            env.cleanup()

    def test_bridge_rejects_ambiguous_and_encoded_lingo_before_subprocess(self):
        source = Path(__file__).resolve().parents[1] / "plugins/lark-cli-bridge/__init__.py"
        if not source.exists():
            source = Path("/opt/hermes/team/plugins/lark-cli-bridge/__init__.py")
        spec = importlib.util.spec_from_file_location("bridge_under_test", source)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        handlers = {}
        class Context:
            def register_tool(self, **kwargs):
                handlers[kwargs["name"]] = kwargs["handler"]
        bridge.register(Context())
        with mock.patch("team.governance.run_command", side_effect=AssertionError("must not execute")):
            for action, args in [
                ("", ["POST", "/open-apis/lingo/v1/entities", "--data", "{}"]),
                ("POST", ["/open-apis/lingo/v1/entities", "--data", "{}"]),
                ("POST", ["/open-apis/%6cingo/v1/entities", "--data", "{}"]),
            ]:
                result = json.loads(handlers["lark_cli"]({"resource": "api", "action": action, "arguments": args}))
                self.assertFalse(result["success"], result)

    def test_cli_local_inputs_cannot_reach_credentials_via_json_or_symlink(self):
        outside = self.home / "secret"
        outside.write_text("private")
        link = self.out / "secret-link"
        link.symlink_to(outside)
        for args in (["+upload", "--file", str(outside)], ["+upload", "--file=" + str(link)], ["upload", "--file", "file=" + str(outside)], ["upload", "--params", json.dumps({"file": str(outside)})]):
            self.assertIsNotNone(governance.cli_denial("drive", args))

    def attachment_bridge(self):
        source = Path(governance.__file__).parent / "plugins/lark-cli-bridge/__init__.py"
        spec = importlib.util.spec_from_file_location("attachment_bridge", source)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        handlers = {}
        class Context:
            def register_tool(self, **kwargs):
                handlers[kwargs["name"]] = kwargs["handler"]
        bridge.register(Context())
        return bridge, handlers["lark_cli"]

    def test_received_image_reaches_member_upload_sandbox_unchanged(self):
        from gateway.platforms.base import cache_image_from_bytes
        original = b"\xff\xd8\xff" + bytes(range(256))
        image = Path(cache_image_from_bytes(original))
        self.assertTrue(governance.public_path(str(image)))
        bridge, handle = self.attachment_bridge()
        run = governance.run_command
        staged_dirs = []
        def inspect(command, **kwargs):
            self.assertEqual(command[-2:], ["--as", "bot"])
            file_arg = next((word.split("=", 1)[1] for word in command if word.startswith("--file=")), None)
            if file_arg is None:
                file_arg = command[command.index("--file") + 1]
            self.assertFalse(Path(file_arg).is_absolute())
            staged_dirs.append(Path(kwargs["cwd"]))
            # Exercise real bubblewrap with the same staged bytes a CLI upload reads.
            return run([sys.executable, "-c", "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())", file_arg], **kwargs)
        for flags in (["--file", str(image)], ["--file=" + str(image)]):
            with mock.patch("team.governance.run_command", side_effect=inspect):
                result = json.loads(handle({"resource": "task", "action": "+upload-attachment", "arguments": ["--resource-id", "test-guid", *flags]}))
            self.assertTrue(result["success"], result)
            self.assertEqual(result["output"].strip(), hashlib.sha256(original).hexdigest())
        self.assertTrue(all(not path.exists() for path in staged_dirs))
        self.assertEqual(image.read_bytes(), original)

    def test_task_upload_rejects_secret_symlink_and_missing_file(self):
        bridge, handle = self.attachment_bridge()
        link = self.out / "secret.jpg"
        link.symlink_to(self.home / "config.yaml")
        with mock.patch("team.governance.run_command", side_effect=AssertionError("must not execute")):
            for path in (link, self.home / "config.yaml", self.out / "missing.jpg"):
                result = json.loads(handle({"resource": "task", "action": "+upload-attachment", "arguments": ["--resource-id", "test-guid", "--file", str(path)]}))
                self.assertFalse(result["success"], result)
        self.assertFalse(governance.public_path(str(self.home / "cache/private-token")))

    def test_task_upload_cleans_staging_when_execution_fails(self):
        bridge, handle = self.attachment_bridge()
        image = self.out / "sample.png"
        image.write_bytes(b"original")
        staged = []
        def fail(command, **kwargs):
            staged.append(Path(kwargs["cwd"]))
            raise OSError("test execution failure")
        with mock.patch("team.governance.run_command", side_effect=fail):
            result = json.loads(handle({"resource": "task", "action": "+upload-attachment", "arguments": ["--resource-id", "test-guid", "--file", str(image)]}))
        self.assertFalse(result["success"])
        self.assertTrue(staged)
        self.assertTrue(all(not path.exists() for path in staged))

    def test_task_upload_help_and_other_commands_keep_workspace(self):
        bridge, _ = self.attachment_bridge()
        for command in (["lark-cli", "task", "+upload-attachment", "--help"], ["lark-cli", "task", "+update"]):
            with bridge._task_attachment_command(command) as (prepared, cwd):
                self.assertEqual(prepared, command)
                self.assertEqual(cwd, "/workspace")


if __name__ == "__main__":
    unittest.main(verbosity=2)

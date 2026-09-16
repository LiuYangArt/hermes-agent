import importlib.util
from pathlib import Path
import unittest

from gateway.session_context import clear_session_vars, set_session_vars
import plugins.platforms.feishu.adapter as adapter

source = Path(adapter.__file__).resolve().parents[3] / "team/plugins/lark-cli-bridge/__init__.py"
spec = importlib.util.spec_from_file_location("team_lark_bridge", source)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tokens = set_session_vars(platform="feishu", chat_type="group", user_id="alice")

    def tearDown(self):
        clear_session_vars(self.tokens)

    def test_group_uses_bot_and_rejects_personal_identity(self):
        command = ["lark-cli", "im", "+chat-list"]
        self.assertEqual(bridge._shared_group_command(command)[-2:], ["--as", "bot"])
        self.assertEqual(bridge._shared_group_command(command + ["--text", "--as=bot"])[-2:], ["--as", "bot"])
        for flags in (["--as", "user"], ["--as=user"], ["--profile", "alice"], ["--profile=alice"], ["--"]):
            with self.assertRaises(ValueError):
                bridge._shared_group_command(command + flags)

    def test_group_cannot_rebind_accounts(self):
        for resource in ("auth", "config", "profile", "update", "event", "--profile=alice"):
            with self.assertRaises(ValueError):
                bridge._shared_group_command(["lark-cli", resource])

    def test_acp_retains_own_identity_policy(self):
        clear_session_vars(self.tokens)
        self.tokens = set_session_vars(platform="acp", user_id="tasks-user")
        command = ["lark-cli", "task", "+get", "--as", "user"]
        self.assertEqual(bridge._shared_group_command(command), command)


if __name__ == "__main__":
    unittest.main(verbosity=2)

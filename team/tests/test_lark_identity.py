import importlib.util
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

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

    def test_team_sender_uses_trusted_open_id_even_when_tenant_id_is_present(self):
        instance = object.__new__(adapter.FeishuAdapter)
        instance._resolve_sender_name_from_api = AsyncMock(return_value="Member")
        sender = SimpleNamespace(open_id="ou_admin", user_id="tenant_user", union_id="on_union")
        with patch("team.governance.enabled", return_value=True):
            profile = asyncio.run(instance._resolve_sender_profile(sender))
            self.assertEqual(profile["user_id"], "ou_admin")
            self.assertEqual(profile["user_id_alt"], "on_union")
            sender.open_id = None
            self.assertIsNone(asyncio.run(instance._resolve_sender_profile(sender))["user_id"])
        with patch("team.governance.enabled", return_value=False):
            self.assertEqual(asyncio.run(instance._resolve_sender_profile(sender))["user_id"], "tenant_user")


if __name__ == "__main__":
    unittest.main(verbosity=2)

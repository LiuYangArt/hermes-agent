import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gateway.session_context import reset_session_vars, set_session_vars
from team import governance
from tools.memory_tool import MemoryStore, apply_memory_pending
from tools.skill_manager_tool import apply_skill_pending, skill_manage
from tools.skill_provenance import (
    reset_current_write_origin,
    set_current_write_origin,
)


class TeamGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary_directory.name)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.home),
                "FEISHU_APP_ID": "cli_test",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write_config()
        reset_session_vars()
        governance._load.cache_clear()

    def tearDown(self):
        reset_session_vars()
        governance._load.cache_clear()
        self.temporary_directory.cleanup()

    def write_config(self, *, admins=("ou_admin",), enabled=True, app_id="cli_test", extra=""):
        admin_lines = "\n".join(f"        - {admin}" for admin in admins)
        if not admin_lines:
            admin_lines = "        []"
        (self.home / "config.yaml").write_text(
            "team_governance:\n"
            f"  enabled: {'true' if enabled else 'false'}\n"
            f"  app_id: {app_id}\n"
            "platforms:\n"
            "  feishu:\n"
            "    extra:\n"
            "      admins:\n"
            f"{admin_lines}\n"
            f"{extra}",
            encoding="utf-8",
        )
        governance._load.cache_clear()

    def bind(self, user_id, *, message_id="om_test", thread_id="omt_same"):
        set_session_vars(
            platform="feishu",
            user_id=user_id,
            message_id=message_id,
            thread_id=thread_id,
        )

    def test_member_and_admin_permissions_follow_bound_request_identity(self):
        self.bind("ou_member")
        self.assertFalse(governance.is_admin())
        self.assertEqual(
            governance.shared_write_denial("memory:add", "memory"),
            governance.DENIED,
        )

        self.bind("ou_admin")
        self.assertTrue(governance.is_admin())
        self.assertIsNone(governance.shared_write_denial("memory:add", "memory"))

    def test_no_configured_admin_and_missing_message_identity_fail_closed(self):
        self.write_config(admins=())
        self.bind("ou_admin")
        self.assertFalse(governance.is_admin())
        self.assertEqual(
            governance.shared_write_denial("skill_manage:create", "sample"),
            governance.DENIED,
        )

        self.write_config()
        self.bind("ou_admin", message_id="")
        self.assertFalse(governance.is_admin())

    def test_empty_mismatched_app_and_invalid_admin_list_fail_closed(self):
        for app_id, environment_app_id, admins in (
            ("", "cli_test", ("ou_admin",)),
            ("other_app", "cli_test", ("ou_admin",)),
            ("cli_test", "cli_test", ("ou_admin", "invalid_member_id")),
        ):
            with self.subTest(app_id=app_id, admins=admins):
                self.write_config(app_id=app_id, admins=admins)
                self.bind("ou_admin")
                with mock.patch.dict(
                    os.environ,
                    {"FEISHU_APP_ID": environment_app_id},
                    clear=False,
                ):
                    self.assertFalse(governance.is_admin())
                    self.assertEqual(
                        governance.shared_write_denial("memory:add", "memory"),
                        governance.DENIED,
                    )

    def test_process_environment_cannot_forge_a_bound_member_identity(self):
        with mock.patch.dict(
            os.environ,
            {
                "HERMES_SESSION_PLATFORM": "feishu",
                "HERMES_SESSION_USER_ID": "ou_admin",
                "HERMES_SESSION_MESSAGE_ID": "om_forged",
            },
            clear=False,
        ):
            self.bind("ou_member")
            self.assertFalse(governance.is_admin())
            self.assertEqual(
                governance.shared_write_denial("memory:add", "memory"),
                governance.DENIED,
            )

    def test_same_thread_rechecks_each_members_identity(self):
        self.bind("ou_admin", thread_id="omt_shared")
        self.assertTrue(governance.is_admin())

        self.bind("ou_member", thread_id="omt_shared")
        self.assertFalse(governance.is_admin())
        self.assertEqual(
            governance.shared_write_denial("memory:add", "memory"),
            governance.DENIED,
        )

    def test_background_review_has_no_admin_authority(self):
        self.bind("ou_admin")
        token = set_current_write_origin("background_review")
        try:
            self.assertFalse(governance.is_admin())
            self.assertEqual(
                governance.shared_write_denial("skill_manage:create", "reviewed"),
                governance.DENIED,
            )
        finally:
            reset_current_write_origin(token)

    def test_approval_replay_cannot_bypass_memory_or_skill_governance(self):
        self.bind("ou_member")
        store = MemoryStore()
        memory_result = apply_memory_pending(
            {"action": "add", "target": "memory", "content": "private note"},
            store,
        )
        self.assertFalse(memory_result["success"])
        self.assertIn(governance.DENIED, memory_result["error"])
        self.assertFalse((self.home / "memories" / "MEMORY.md").exists())

        skill_result = json.loads(
            apply_skill_pending(
                {
                    "action": "create",
                    "name": "replayed-skill",
                    "content": "---\nname: replayed-skill\ndescription: test\n---\n# Test\n",
                }
            )
        )
        self.assertFalse(skill_result["success"])
        self.assertIn(governance.DENIED, skill_result["error"])
        self.assertFalse((self.home / "skills" / "replayed-skill").exists())

    def test_admin_can_write_read_back_and_delete_memory_and_skill(self):
        self.write_config(
            extra=(
                "memory:\n"
                "  write_approval: false\n"
                "skills:\n"
                "  write_approval: false\n"
            )
        )
        self.bind("ou_admin")

        store = MemoryStore()
        added = store.add("memory", "administrator managed memory")
        self.assertTrue(added["success"], added)
        memory_path = self.home / "memories" / "MEMORY.md"
        self.assertIn("administrator managed memory", memory_path.read_text(encoding="utf-8"))
        reloaded_store = MemoryStore()
        reloaded_store.load_from_disk()
        self.assertIn("administrator managed memory", reloaded_store.memory_entries)
        removed = reloaded_store.remove("memory", "administrator managed memory")
        self.assertTrue(removed["success"], removed)
        final_store = MemoryStore()
        final_store.load_from_disk()
        self.assertNotIn("administrator managed memory", final_store.memory_entries)

        skill_content = (
            "---\n"
            "name: admin-managed-skill\n"
            "description: Temporary governance integration test skill.\n"
            "---\n"
            "# Admin Managed Skill\n"
        )
        created = json.loads(
            skill_manage(
                action="create",
                name="admin-managed-skill",
                content=skill_content,
            )
        )
        self.assertTrue(created["success"], created)
        skill_path = self.home / "skills" / "admin-managed-skill" / "SKILL.md"
        self.assertEqual(skill_path.read_text(encoding="utf-8"), skill_content)
        deleted = json.loads(
            skill_manage(
                action="delete",
                name="admin-managed-skill",
                absorbed_into="",
            )
        )
        self.assertTrue(deleted["success"], deleted)
        self.assertFalse(skill_path.parent.exists())

    def test_member_tool_allowlist_and_execution_denials(self):
        self.bind("ou_member")
        for allowed in ("read_file", "write_file", "lark_cli"):
            with self.subTest(allowed=allowed):
                self.assertIsNone(governance.tool_denial(allowed, {}))
        for denied in ("terminal", "execute_code", "unreviewed_plugin_tool"):
            with self.subTest(denied=denied):
                self.assertIsNotNone(governance.tool_denial(denied, {}))

    def test_disabling_team_governance_leaves_non_team_runtime_unchanged(self):
        self.write_config(enabled=False)
        reset_session_vars()
        for tool_name in ("memory", "skill_manage", "terminal", "unknown"):
            with self.subTest(tool=tool_name):
                self.assertIsNone(governance.tool_denial(tool_name, {}))

    def test_absent_team_policy_leaves_default_runtime_unchanged(self):
        for content in ("{}", "terminal:\n  backend: local\n"):
            (self.home / "config.yaml").write_text(content, encoding="utf-8")
            governance._load.cache_clear()
            reset_session_vars()
            self.assertFalse(governance.enabled())
            self.assertIsNone(governance.tool_denial("terminal", {}))
            self.assertIsNone(governance.shared_write_denial("memory:add"))

    def test_enabled_team_deployment_denies_unbound_and_other_platform_writes(self):
        reset_session_vars()
        self.assertIsNotNone(governance.shared_write_denial("memory:add"))
        set_session_vars(platform="acp", user_id="ou_admin", message_id="om_test")
        self.assertIsNotNone(governance.shared_write_denial("memory:add"))
        self.assertIsNone(governance.tool_denial("lark_cli", {}))

    def test_approval_configuration_does_not_grant_admin_authority(self):
        self.write_config(
            extra=(
                "memory:\n"
                "  write_approval: false\n"
                "skills:\n"
                "  write_approval: false\n"
                "approvals:\n"
                "  mode: off\n"
            )
        )
        self.bind("ou_member")
        self.assertFalse(governance.is_admin())
        self.assertEqual(
            governance.shared_write_denial("memory:add", "memory"),
            governance.DENIED,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

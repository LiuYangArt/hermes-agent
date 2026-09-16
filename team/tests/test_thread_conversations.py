import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch


sys.path.insert(0, "/opt/hermes")

import plugins.platforms.feishu.adapter as feishu
from gateway.config import PlatformConfig


class ThreadConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_HOME": self.home.name,
                "FEISHU_APP_ID": "cli_test_app",
                "FEISHU_BOT_OPEN_ID": "ou_hermes",
                "FEISHU_BOT_NAME": "Hermes",
                "FEISHU_GROUP_POLICY": "open",
                "FEISHU_REQUIRE_MENTION": "true",
            },
            clear=True,
        )
        self.environment.start()
        self.adapters = []
        self.handled = []
        self.adapter = self._make_adapter()

    async def asyncTearDown(self):
        for adapter in self.adapters:
            await adapter.cancel_background_tasks()
            if hasattr(adapter, "_topic_state"):
                adapter._topic_state.close()
        self.environment.stop()
        self.home.cleanup()

    def _make_adapter(self):
        adapter = feishu.FeishuAdapter(
            PlatformConfig(
                typing_indicator=False,
                extra={
                    "app_id": "cli_test_app",
                    "default_group_policy": "open",
                    "require_mention": True,
                    "group_sessions_per_user": False,
                    "thread_sessions_per_user": False,
                },
            )
        )
        adapter._text_batch_delay_seconds = 0

        async def extract(message):
            normalized = feishu.normalize_feishu_message(
                message_type=message.message_type,
                raw_content=message.content,
                mentions=message.mentions,
                bot=adapter._bot_identity(),
            )
            return normalized.text_content, feishu.MessageType.TEXT, [], [], normalized.mentions

        async def profile(sender_id, **_kwargs):
            user_id = sender_id.open_id
            return {
                "user_id": user_id,
                "user_name": user_id.replace("ou_", "User "),
                "user_id_alt": None,
            }

        async def model_handler(event):
            self.handled.append(event)
            return None

        async def chat_info(_chat_id):
            return {"name": "Thread test group", "chat_mode": "group"}

        adapter._extract_message_content = extract
        adapter._resolve_sender_profile = profile
        adapter.get_chat_info = chat_info
        adapter._fetch_message_text = self._async_value(None)
        adapter.set_message_handler(model_handler)
        self.adapters.append(adapter)
        return adapter

    @staticmethod
    def _async_value(value):
        async def read(*_args, **_kwargs):
            return value

        return read

    @staticmethod
    def _mention(key, open_id, name):
        return NS(
            key=key,
            id=NS(open_id=open_id, user_id="", union_id=""),
            name=name,
        )

    async def _inbound(
        self,
        message_id,
        text,
        *,
        user="ou_alice",
        tenant_user="",
        root=None,
        thread=None,
        mentions=(),
        adapter=None,
        chat="oc_team",
    ):
        adapter = adapter or self.adapter
        mention_objects = []
        rendered = text
        for index, target in enumerate(mentions, start=1):
            key = f"@_user_{index}"
            if target == "hermes":
                mention = self._mention(key, "ou_hermes", "Hermes")
            else:
                mention = self._mention(key, f"ou_{target}", target.title())
            mention_objects.append(mention)
            rendered = f"{key} {rendered}"
        message = NS(
            message_id=message_id,
            chat_id=chat,
            chat_type="group",
            message_type="text",
            content=json.dumps({"text": rendered}),
            mentions=mention_objects,
            root_id=root,
            thread_id=thread,
            parent_id=root,
            upper_message_id=None,
            create_time="2026-09-16T10:00:00+08:00",
        )
        sender = NS(
            sender_type="user",
            sender_id=NS(open_id=user, user_id=tenant_user, union_id=""),
        )
        await adapter._handle_message_event_data(NS(event=NS(message=message, sender=sender)))

    async def _wait_idle(self, adapter=None):
        adapter = adapter or self.adapter
        for _ in range(100):
            tasks = [
                task
                for task in (
                    list(getattr(adapter, "_topic_workers", {}).values())
                    + list(adapter._session_tasks.values())
                )
                if not task.done()
            ]
            if not tasks:
                await asyncio.sleep(0)
                tasks = [
                    task
                    for task in (
                        list(getattr(adapter, "_topic_workers", {}).values())
                        + list(adapter._session_tasks.values())
                    )
                    if not task.done()
                ]
                if not tasks:
                    return
            await asyncio.sleep(0.01)
        self.fail("thread queue did not become idle")

    async def _start_topic(self, message_id="m_start", user="ou_alice", adapter=None):
        await self._inbound(
            message_id,
            "start",
            user=user,
            mentions=("hermes",),
            adapter=adapter,
        )
        await self._wait_idle(adapter)

    async def test_group_quote_reuses_native_root_without_enabling_group_followups(self):
        await self._inbound("quote_request", "read quote", root="original", mentions=("hermes",))
        await self._wait_idle()
        self.assertEqual(self.handled[-1].source.thread_id, "original")
        await self._inbound("group_reply", "human discussion", root="original")
        await self._wait_idle()
        self.assertEqual(len(self.handled), 1)
        await self._inbound("second_quote", "read again", root="original", mentions=("hermes",))
        await self._wait_idle()
        self.assertEqual(self.handled[-1].source.thread_id, "original")
        await self._inbound("native_followup", "continue", root="original", thread="omt_quote")
        await self._wait_idle()
        self.assertEqual(self.handled[-1].source.thread_id, "original")
        self.assertEqual(len(self.handled), 3)

    async def test_team_admin_identity_survives_real_inbound_mapping(self):
        from team import governance
        from gateway.run import GatewayRunner
        from gateway.session_context import clear_session_vars

        Path(self.home.name, "config.yaml").write_text(
            "team_governance:\n  enabled: true\n  app_id: cli_test_app\n"
            "platforms:\n  feishu:\n    extra:\n      admins: [ou_alice]\n",
            encoding="utf-8",
        )
        governance._load.cache_clear()
        self.addCleanup(governance._load.cache_clear)
        self.adapter._resolve_sender_profile = feishu.FeishuAdapter._resolve_sender_profile.__get__(self.adapter)
        self.adapter._resolve_sender_name_from_api = self._async_value("Alice")
        await self._inbound("om_governance", "test", tenant_user="tenant_alice", mentions=("hermes",))
        await self._wait_idle()
        event = self.handled[-1]
        self.assertEqual(event.source.user_id, "ou_alice")
        self.assertEqual(event.source.message_id, "om_governance")
        self.assertTrue(governance.is_admin(event.source))
        runner = object.__new__(GatewayRunner)
        tokens = runner._set_session_env(NS(source=event.source, session_key="test-session"))
        try:
            self.assertTrue(governance.is_admin())
        finally:
            clear_session_vars(tokens)

    async def test_participation_background_and_topic_isolation(self):
        await self._start_topic()
        self.assertEqual([event.text for event in self.handled], ["start"])

        await self._inbound("m_follow", "continue", root="m_start", thread="omt_one")
        await self._wait_idle()
        self.assertEqual(self.handled[-1].text, "continue")

        await self._inbound(
            "m_other",
            "please review",
            root="m_start",
            thread="omt_one",
            mentions=("bob",),
        )
        await self._wait_idle()
        self.assertEqual(len(self.handled), 2)

        await self._inbound(
            "m_new_member",
            "I am only joining",
            user="ou_carol",
            root="m_start",
            thread="omt_one",
        )
        await self._wait_idle()
        self.assertEqual(len(self.handled), 2)

        await self._inbound(
            "m_resume",
            "summarize",
            root="m_start",
            thread="omt_one",
            mentions=("hermes",),
        )
        await self._wait_idle()
        resumed = self.handled[-1].text
        self.assertIn("please review", resumed)
        self.assertIn("I am only joining", resumed)
        self.assertIn("当前请求：\nsummarize", resumed)

        await self._start_topic("m_second", user="ou_bob")
        second = self.handled[-1]
        self.assertEqual(second.source.thread_id, "m_second")
        self.assertNotIn("please review", second.text)
        self.assertNotIn("I am only joining", second.text)

    async def test_active_member_survives_adapter_restart(self):
        await self._start_topic("m_restart")
        restarted = self._make_adapter()
        await self._inbound(
            "m_after_restart",
            "continue after restart",
            root="m_restart",
            thread="omt_restart",
            adapter=restarted,
        )
        await self._wait_idle(restarted)
        self.assertEqual(self.handled[-1].text, "continue after restart")
        self.assertEqual(self.handled[-1].source.thread_id, "m_restart")

    async def test_queued_turns_from_different_members_are_not_merged(self):
        await self._start_topic("m_queue")
        await self._inbound(
            "m_activate_bob",
            "join",
            user="ou_bob",
            root="m_queue",
            thread="omt_queue",
            mentions=("hermes",),
        )
        await self._wait_idle()
        self.handled.clear()
        self.adapter._text_batch_delay_seconds = 0.03

        await self._inbound(
            "m_alice_turn",
            "alice turn",
            user="ou_alice",
            root="m_queue",
            thread="omt_queue",
        )
        await self._inbound(
            "m_bob_turn",
            "bob turn",
            user="ou_bob",
            root="m_queue",
            thread="omt_queue",
        )
        await self._wait_idle()

        self.assertEqual(len(self.handled), 2)
        self.assertEqual(
            [(event.source.user_id, event.text) for event in self.handled],
            [("ou_alice", "alice turn"), ("ou_bob", "bob turn")],
        )

    async def test_pause_invalidates_unstarted_implicit_turn_but_keeps_explicit(self):
        await self._start_topic("m_pause")
        self.handled.clear()
        self.adapter._text_batch_delay_seconds = 0.03

        await self._inbound(
            "m_stale_implicit",
            "implicit before pause",
            root="m_pause",
            thread="omt_pause",
        )
        await self._inbound(
            "m_explicit",
            "explicit request",
            root="m_pause",
            thread="omt_pause",
            mentions=("hermes",),
        )
        await self._inbound(
            "m_pause_to_bob",
            "talking to bob",
            root="m_pause",
            thread="omt_pause",
            mentions=("bob",),
        )
        await self._wait_idle()

        self.assertEqual(len(self.handled), 1)
        self.assertIn("当前请求：\nexplicit request", self.handled[0].text)
        self.assertIn("implicit before pause", self.handled[0].text)
        self.assertIn("talking to bob", self.handled[0].text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

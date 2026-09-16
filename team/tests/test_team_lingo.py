import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from gateway.session_context import reset_session_vars, set_session_vars
from team import governance
from team.lingo import MATCH_PATH, MAX_MATCHES, query


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(command, 0, json.dumps(response), "")


class TeamLingoTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary_directory.name)
        self.environment = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write_config()
        reset_session_vars()
        self.bind()

    def tearDown(self):
        reset_session_vars()
        governance._load.cache_clear()
        self.temporary_directory.cleanup()

    def write_config(self, repo_ids=("repo_one",), chats=("oc_allowed",)):
        repos = "\n".join(f"      - {item}" for item in repo_ids) or "      []"
        allowed = "\n".join(f"      - {item}" for item in chats) or "      []"
        (self.home / "config.yaml").write_text(
            "team_governance:\n"
            "  enabled: true\n"
            "  lingo:\n"
            "    repo_ids:\n"
            f"{repos}\n"
            "    allowed_chat_ids:\n"
            f"{allowed}\n",
            encoding="utf-8",
        )
        governance._load.cache_clear()

    def bind(self, platform="feishu", chat_id="oc_allowed"):
        set_session_vars(platform=platform, chat_id=chat_id, user_id="ou_member", message_id="om_test")

    def test_rejects_unconfigured_repo_and_unauthorized_context_without_calling_cli(self):
        runner = FakeRunner([])
        self.bind(platform="slack")
        self.assertFalse(query("术语", run=runner)["success"])
        self.bind(chat_id="oc_other")
        self.assertFalse(query("术语", run=runner)["success"])
        self.write_config(repo_ids=())
        self.bind()
        self.assertFalse(query("术语", run=runner)["success"])
        self.assertEqual(runner.calls, [])

    def test_validates_word_length(self):
        runner = FakeRunner([])
        for word in ("", " " * 4, "x" * 101):
            with self.subTest(word_length=len(word)):
                result = query(word, run=runner)
                self.assertFalse(result["success"])
                self.assertIn("1 到 100", result["error"])
        self.assertEqual(runner.calls, [])

    def test_uses_fixed_bot_post_then_get_and_returns_aliases_and_sources(self):
        runner = FakeRunner([
            {"code": 0, "data": {"results": [{"entity_id": "ent_1", "type": "entity"}]}},
            {"code": 0, "data": {"entity": {
                "main_keys": [{"key": "苍穹"}],
                "aliases": [{"key": "天空"}, {"key": "Sky"}],
                "description": "项目中的区域名",
                "rich_text": "详细说明",
                "related_meta": {
                    "docs": [{"title": "设定文档", "url": "https://example/doc"}],
                    "links": [{"title": "参考页", "url": "https://example/link"}],
                },
            }}},
        ])
        result = query("苍穹", run=runner)
        self.assertTrue(result["success"])
        self.assertEqual(result["matches"][0]["aliases"], ["天空", "Sky"])
        self.assertEqual(len(result["matches"][0]["sources"]), 2)
        post, post_kwargs = runner.calls[0]
        self.assertEqual(post[2:4], ["POST", MATCH_PATH])
        self.assertEqual(post[4:6], ["--as", "bot"])
        self.assertEqual(json.loads(post[post.index("--params") + 1]), {"repo_id": "repo_one"})
        self.assertEqual(json.loads(post[post.index("--data") + 1]), {"word": "苍穹"})
        get, get_kwargs = runner.calls[1]
        self.assertEqual(get[2:4], ["GET", "/open-apis/lingo/v1/entities/ent_1"])
        self.assertEqual(get[4:6], ["--as", "bot"])
        for kwargs in (post_kwargs, get_kwargs):
            self.assertEqual(kwargs["cwd"], "/workspace")
            self.assertEqual(kwargs["timeout"], 90)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

    def test_returns_all_meanings_and_deduplicates_within_a_repo(self):
        runner = FakeRunner([
            {"data": {"results": [
                {"entity_id": "same", "type": "first"},
                {"entity_id": "same", "type": "duplicate"},
                {"entity_id": "other", "type": "second"},
            ]}},
            {"data": {"entity": {"main_keys": [{"key": "同名甲"}]}}},
            {"data": {"entity": {"main_keys": [{"key": "同名乙"}]}}},
        ])
        result = query("同名", run=runner)
        self.assertEqual([item["entity_id"] for item in result["matches"]], ["same", "other"])
        self.assertTrue(result["ambiguous"])
        self.assertIn("不能默认", result["message"])
        self.assertTrue(all(item["source_missing"] for item in result["matches"]))
        self.assertEqual(len(runner.calls), 3)

    def test_deduplicates_an_entity_returned_by_multiple_repositories(self):
        self.write_config(repo_ids=("repo_one", "repo_two"))
        runner = FakeRunner([
            {"data": {"results": [{"entity_id": "shared", "type": "entity"}]}},
            {"data": {"results": [{"entity_id": "shared", "type": "entity"}]}},
            {"data": {"entity": {"main_keys": [{"key": "共享词条"}]}}},
        ])
        result = query("共享", run=runner)
        self.assertEqual([item["entity_id"] for item in result["matches"]], ["shared"])
        self.assertEqual(len(runner.calls), 3)
        second_post = runner.calls[1][0]
        self.assertEqual(json.loads(second_post[second_post.index("--params") + 1]), {"repo_id": "repo_two"})

    def test_empty_result_is_distinct_from_interface_failure(self):
        empty = query("不存在", run=FakeRunner([{"code": 0, "data": {"results": []}}]))
        self.assertTrue(empty["success"])
        self.assertFalse(empty["found"])
        self.assertIn("未找到", empty["message"])

        failed_runner = FakeRunner([
            subprocess.CompletedProcess([], 7, "", "permission denied")
        ])
        failed = query("存在", run=failed_runner)
        self.assertFalse(failed["success"])
        self.assertIn("接口调用失败", failed["error"])

    def test_caps_details_at_ten_and_reports_truncation(self):
        results = [{"entity_id": f"ent_{index}", "type": "entity"} for index in range(MAX_MATCHES + 2)]
        responses = [{"data": {"results": results}}]
        responses.extend(
            {"data": {"entity": {"main_keys": [{"key": f"词义{index}"}]}}}
            for index in range(MAX_MATCHES)
        )
        result = query("多义", run=FakeRunner(responses))
        self.assertEqual(len(result["matches"]), MAX_MATCHES)
        self.assertTrue(result["truncated"])
        self.assertIn(str(MAX_MATCHES), result["limit_message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

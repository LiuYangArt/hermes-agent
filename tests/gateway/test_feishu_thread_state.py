import importlib.util
import stat
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2] / "plugins" / "platforms" / "feishu" / "thread_state.py"
)
SPEC = importlib.util.spec_from_file_location("feishu_thread_state", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
ThreadState = MODULE.ThreadState


def test_topics_aliases_members_and_apps_are_isolated(tmp_path):
    path = tmp_path / "thread-state.db"
    first = ThreadState(path, "app-one")
    second = ThreadState(path, "app-two")

    first.activate("chat-a", "topic-a", ("root-a", "thread-a"))
    first.activate("chat-b", "topic-b", ("root-a",))
    second.activate("chat-a", "topic-other", ("root-a",))

    assert first.resolve("chat-a", root_id="root-a") == "topic-a"
    assert first.resolve("chat-a", thread_id="thread-a") == "topic-a"
    assert first.resolve("chat-b", root_id="root-a") == "topic-b"
    assert second.resolve("chat-a", root_id="root-a") == "topic-other"
    assert first.resolve("chat-a", root_id="missing") is None

    assert first.generation("chat-a", "topic-a", "user-a") == 0
    assert first.set_active("chat-a", "topic-a", "user-a", True) == 1
    assert first.set_active("chat-a", "topic-a", "user-a", False) == 2
    assert first.set_active("chat-a", "topic-a", "user-b", True) == 1
    assert not first.is_active("chat-a", "topic-a", "user-a")
    assert first.is_active("chat-a", "topic-a", "user-b")
    assert not second.is_active("chat-a", "topic-a", "user-b")

    first.bind("chat-a", "topic-a", ("late-alias",))
    assert first.resolve("chat-a", thread_id="late-alias") == "topic-a"


def test_state_persists_after_reopen_and_database_is_private(tmp_path):
    path = tmp_path / "nested" / "thread-state.db"
    state = ThreadState(path, "app")
    state.activate("chat", "topic", ("root",))
    state.set_active("chat", "topic", "user", True)
    state.mark_seen("seen-message")
    state.close()

    reopened = ThreadState(path, "app")
    assert reopened.resolve("chat", root_id="root") == "topic"
    assert reopened.is_active("chat", "topic", "user")
    assert reopened.generation("chat", "topic", "user") == 1
    assert reopened.seen("seen-message")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_context_deduplicates_and_consume_only_removes_snapshot(tmp_path):
    state = ThreadState(tmp_path / "state.db", "app")
    assert state.remember("chat", "topic", "m1", "u1", "甲", "第一条", "10:00")
    assert not state.remember("chat", "topic", "m1", "u1", "甲", "重复")
    assert state.remember("chat", "topic", "m2", "u2", "乙", "第二条")
    assert state.remember("chat", "other-topic", "m3", "u3", "丙", "其他话题")

    context, snapshot_ids = state.context("chat", "topic")
    assert snapshot_ids == ["m1", "m2"]
    assert "[甲 (10:00)]\n第一条" in context
    assert "[乙]\n第二条" in context
    assert "其他话题" not in context

    assert state.remember("chat", "topic", "m4", "u4", "丁", "快照后的消息")
    state.consume("chat", "topic", snapshot_ids)
    remaining, remaining_ids = state.context("chat", "topic")
    assert remaining_ids == ["m4"]
    assert "快照后的消息" in remaining


def test_context_reports_truncation_and_returns_snapshot_ids(tmp_path):
    state = ThreadState(tmp_path / "state.db", "app")
    for number in range(4):
        state.remember(
            "chat",
            "topic",
            f"m{number}",
            "user",
            "成员",
            str(number) * 4_000,
        )

    context, ids = state.context("chat", "topic")
    assert len(context) <= 12_000
    assert context.startswith("[旁听背景已截断：省略较早内容约 ")
    assert "333333" in context
    assert ids == ["m0", "m1", "m2", "m3"]


def test_seen_is_deduplicated_per_app(tmp_path):
    path = tmp_path / "state.db"
    first = ThreadState(path, "first")
    second = ThreadState(path, "second")

    assert not first.seen("message")
    first.mark_seen("message")
    first.mark_seen("message")
    assert first.seen("message")
    assert not second.seen("message")

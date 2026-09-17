"""Keep a Lark turn's temporary progress and answer in the same message."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field

from gateway.platforms.base import ProcessingOutcome, SendResult


@dataclass
class ReplyState:
    app_id: str
    chat_id: str
    event_id: str
    message_id: str | None = None
    answering: bool = False
    delivered: bool = False
    closed: bool = False
    waiting: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Child progress tasks inherit the same turn object, never another user's turn.
_reply: ContextVar[ReplyState | None] = ContextVar("feishu_reply", default=None)


async def _settle_io(operation):
    # Cancelling an asyncio waiter cannot cancel an SDK request already in its
    # thread. Keep its lock until the remote edit and local bookkeeping finish.
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class FeishuReplyMixin:
    def _begin_reply(self, event):
        _reply.set(ReplyState(self._app_id, event.source.chat_id, event.message_id))

    def _reply_for_chat(self, chat_id):
        state = _reply.get()
        if state and state.app_id == self._app_id and state.chat_id == chat_id:
            return state
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return await _settle_io(self._send_reply(chat_id, content, reply_to, metadata))

    async def _send_reply(self, chat_id, content, reply_to, metadata):
        state = self._reply_for_chat(chat_id)
        meta = metadata or {}
        progress = bool(meta.get("progress"))
        answer = bool(meta.get("notify") or meta.get("expect_edits"))
        if state is None or not (progress or answer):
            return await self._send_message(chat_id, content, reply_to, metadata)

        async with state.lock:
            if progress:
                question = bool(meta.get("awaiting_input"))
                if state.closed or ((state.answering or state.waiting) and not question):
                    return SendResult(success=True, message_id=state.message_id)
                # Progress is a transient view: overflow must not leave history
                # bubbles behind when the final answer replaces this message.
                text = self.format_message(content)[-self.MAX_MESSAGE_LENGTH:]
                if state.message_id:
                    result = await self._edit_message(chat_id, state.message_id, text, prefer_post=True)
                else:
                    result = await self._send_message(chat_id, text, reply_to, metadata)
                if result.success:
                    state.message_id = result.message_id
                    if question:
                        state.waiting = True
                        state.answering = state.delivered = False
                return result

            continuation = state.answering and meta.get("expect_edits")
            state.answering = True
            if not state.message_id or continuation:
                return await self._send_message(chat_id, content, reply_to, metadata)
            chunks = self.truncate_message(self.format_message(content), self.MAX_MESSAGE_LENGTH)
            if not chunks:
                return SendResult(success=False, error="Empty final reply")
            result = await self._edit_message(chat_id, state.message_id, chunks[0], finalize=True, prefer_post=True)
            if not result.success:
                return result
            for chunk in chunks[1:]:
                result = await self._send_message(chat_id, chunk, reply_to, metadata)
                if not result.success:
                    return result
            state.delivered = bool(meta.get("notify"))
            return result

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        return await _settle_io(self._edit_reply(chat_id, message_id, content, finalize, metadata))

    async def _edit_reply(self, chat_id, message_id, content, finalize, metadata):
        state = self._reply_for_chat(chat_id)
        if state is None or message_id != state.message_id:
            return await self._edit_message(chat_id, message_id, content, finalize=finalize)
        async with state.lock:
            if state.closed or ((metadata or {}).get("progress") and (state.answering or state.waiting)):
                return SendResult(success=True, message_id=message_id)
            if (metadata or {}).get("progress"):
                content = self.format_message(content)[-self.MAX_MESSAGE_LENGTH:]
            result = await self._edit_message(chat_id, message_id, content, finalize=finalize, prefer_post=True)
            if result.success and finalize:
                state.answering = state.delivered = True
            return result

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key, metadata=None):
        return await super().send_clarify(
            chat_id, question, choices, clarify_id, session_key,
            metadata=dict(metadata or {}, progress=True, awaiting_input=True),
        )

    async def _show_approval_wait(self, chat_id):
        await _settle_io(self._wait_for_approval(chat_id))

    async def _wait_for_approval(self, chat_id):
        state = self._reply_for_chat(chat_id)
        if state and state.message_id and not state.closed:
            async with state.lock:
                state.waiting = True
                await self._edit_message(
                    chat_id, state.message_id, "等待你确认操作，请查看本话题中的审批卡片。",
                    prefer_post=True,
                )

    async def _finish_reply(self, event, outcome):
        await _settle_io(self._close_reply(event, outcome))

    async def _close_reply(self, event, outcome):
        state = self._reply_for_chat(event.source.chat_id)
        if state is None or state.event_id != event.message_id:
            return
        async with state.lock:
            state.closed = True
            if not state.message_id or state.delivered:
                return
            if outcome is ProcessingOutcome.CANCELLED:
                text = "本次处理已停止。如需继续，请重新发送请求。"
            elif outcome is ProcessingOutcome.FAILURE:
                text = "本次处理未能完成，请重试；若仍失败，请联系管理员查看日志。"
            elif state.waiting:
                return
            else:
                text = "本次处理已结束，请查看本话题中的结果或待确认事项。"
            await self._edit_message(state.chat_id, state.message_id, text, finalize=True, prefer_post=True)

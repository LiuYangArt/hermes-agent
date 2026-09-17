"""Exercise Lark reply ownership through the real adapter and delivery pipeline."""
import asyncio
import json
import os
import queue
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, '/opt/hermes')

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType, ProcessingOutcome, SendResult
from gateway.session import Platform, SessionSource
from plugins.platforms.feishu.adapter import FeishuAdapter


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'HERMES_HOME': self.home.name, 'FEISHU_REACTIONS': 'false'})
        self.env.start()
        self.adapter = FeishuAdapter(PlatformConfig(typing_indicator=False, extra={'app_id': 'cli_test'}))
        self.sent = []
        self.edits = []
        self.fail_edit = False

        async def send(**kwargs):
            self.sent.append(kwargs)
            return NS(success=lambda: True, data=NS(message_id=f'om_{len(self.sent)}'))

        def update(request):
            self.edits.append(request)
            return NS(success=lambda: not self.fail_edit, code=999 if self.fail_edit else 0, msg='rejected', data=None)

        self.adapter._feishu_send_with_retry = send
        self.adapter._client = NS(im=NS(v1=NS(message=NS(update=update))))
        self.event = self.event_for('user_request', 'topic_a')
        await self.adapter.on_processing_start(self.event)

    async def asyncTearDown(self):
        await self.adapter.cancel_background_tasks()
        self.adapter._shutdown_sdk_executor()
        self.env.stop()
        self.home.cleanup()

    @staticmethod
    def event_for(message_id, thread):
        return MessageEvent(text='test', message_type=MessageType.TEXT, message_id=message_id,
                            source=SessionSource(platform=Platform.FEISHU, chat_id='chat',
                                                 chat_type='group', user_id=message_id, thread_id=thread))

    async def progress(self, text='Reading skill lark-task'):
        return await self.adapter.send('chat', text, reply_to=self.event.message_id,
                                       metadata={'progress': True, 'thread_id': self.event.source.thread_id})

    async def final(self, text='**已完成** [查看结果](https://example.com/result)'):
        return await self.adapter.send('chat', text, metadata={'notify': True})

    async def test_final_replaces_progress_preserving_id_and_rich_text(self):
        original = await self.progress()
        result = await self.final()
        self.assertTrue(result.success)
        self.assertEqual(result.message_id, original.message_id)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['msg_type'], 'post')
        self.assertEqual(self.edits[-1].message_id, original.message_id)
        self.assertIn('https://example.com/result', self.edits[-1].request_body.content)
        await self.adapter.on_processing_complete(self.event, ProcessingOutcome.SUCCESS)
        self.assertEqual(len(self.edits), 1)

    async def test_no_progress_sends_normal_reply(self):
        await self.final('hello')
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.edits)

    async def test_late_progress_cannot_replace_answer(self):
        first = await self.progress()
        await self.final('done')
        await self.adapter.edit_message('chat', first.message_id, 'late skill', metadata={'progress': True})
        await self.progress('late send')
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_long_answer_reuses_first_message_and_sends_continuation(self):
        self.adapter.MAX_MESSAGE_LENGTH = 100
        original = await self.progress()
        await self.final('word ' * 80)
        self.assertEqual(self.edits[0].message_id, original.message_id)
        self.assertGreater(len(self.sent), 1)
        self.assertTrue(all(len(item['payload']) < 1000 for item in self.sent))

    async def test_progress_overflow_keeps_one_message(self):
        self.adapter.MAX_MESSAGE_LENGTH = 100
        await self.progress('tool ' * 200)
        await self.progress('another ' * 200)
        await self.final('done')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.edits), 2)

    async def test_cancellation_closes_progress_and_ignores_late_updates(self):
        first = await self.progress()
        await self.adapter.on_processing_complete(self.event, ProcessingOutcome.CANCELLED)
        await self.adapter.edit_message('chat', first.message_id, 'late', metadata={'progress': True})
        self.assertEqual(len(self.edits), 1)
        self.assertIn('已停止', self.edits[0].request_body.content)

    async def test_failed_edit_reports_failure_without_duplicate_answer(self):
        await self.progress()
        self.fail_edit = True
        result = await self.final()
        self.assertFalse(result.success)
        self.assertEqual(len(self.sent), 1)

    async def test_cancelled_stream_preview_is_not_mistaken_for_final_answer(self):
        await self.progress()
        await self.adapter.send('chat', 'partial answer', metadata={'expect_edits': True})
        await self.adapter.on_processing_complete(self.event, ProcessingOutcome.CANCELLED)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('已停止', self.edits[-1].request_body.content)

    async def test_clarify_reuses_progress_until_answer(self):
        await self.progress()
        await self.adapter.send_clarify('chat', '请选择输出格式？', None, 'clarify_test', 'session')
        await self.progress('late tool')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('请选择', self.edits[-1].request_body.content)
        await self.adapter.send_clarify('chat', '请确认文件名？', None, 'clarify_second', 'session')
        self.assertIn('请确认文件名', self.edits[-1].request_body.content)
        await self.final('已选择 Markdown。')
        self.assertIn('Markdown', self.edits[-1].request_body.content)

    async def test_approval_wait_retains_card_and_updates_progress(self):
        await self.progress()
        result = await self.adapter.send_exec_approval('chat', 'echo test', 'session')
        self.assertTrue(result.success)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[-1]['msg_type'], 'interactive')
        self.assertIn('审批卡片', self.edits[-1].request_body.content)
        await self.progress('late tool')
        self.assertIn('审批卡片', self.edits[-1].request_body.content)
        await self.final('操作已完成。')
        self.assertEqual(len(self.sent), 2)
        self.assertIn('操作已完成', self.edits[-1].request_body.content)

    async def test_concurrent_topics_and_users_do_not_share_message(self):
        async def turn(number):
            event = self.event_for(f'request_{number}', f'topic_{number}')
            await self.adapter.on_processing_start(event)
            initial = await self.progress()
            await asyncio.sleep(0)
            await self.final(f'answer_{number}')
            await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
            return initial.message_id, f'answer_{number}'
        results = await asyncio.gather(turn(1), turn(2))
        self.assertEqual(len(set(mid for mid, _ in results)), 2)
        for mid, text in results:
            matches = [edit for edit in self.edits if edit.message_id == mid]
            self.assertEqual(len(matches), 1)
            self.assertIn(text, matches[0].request_body.content)

    async def test_cancelled_sdk_wait_settles_before_final_answer(self):
        original = self.adapter._send_message
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        self.adapter._send_message = delayed
        progress = asyncio.create_task(self.progress())
        await entered.wait()
        progress.cancel()
        final = asyncio.create_task(self.final())
        await asyncio.sleep(0)
        self.assertFalse(final.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await progress
        result = await final
        self.assertTrue(result.success)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(result.message_id, 'om_1')

    async def test_real_delivery_pipeline_and_progress_queue(self):
        from gateway.run import TurnRunner
        from gateway.turn_context import TurnContext
        progress_task = None

        async def handler(event):
            nonlocal progress_task
            ctx = TurnContext(source=event.source, _run_still_current=lambda: True,
                              progress_queue=queue.Queue(), _progress_metadata={'thread_id': 'topic_a'},
                              _progress_reply_to=event.message_id)
            ctx.progress_queue.put('Reading skill lark-task')
            runner = TurnRunner(NS(_adapter_for_source=lambda source: self.adapter), ctx)
            progress_task = asyncio.create_task(runner.send_progress_messages())
            while not self.sent:
                await asyncio.sleep(0.01)
            progress_task.cancel()
            return '**完成** [结果](https://example.com/result)'

        self.adapter.set_message_handler(handler)
        await self.adapter._process_message_background(self.event, 'test:topic_a')
        await progress_task
        self.assertEqual(len(self.sent), 1)
        self.assertIn('https://example.com/result', self.edits[-1].request_body.content)
        self.assertNotIn('Reading', self.edits[-1].request_body.content)

    async def test_exception_pipeline_replaces_progress_with_error(self):
        async def handler(event):
            await self.progress()
            raise ValueError('test failure')
        self.adapter.set_message_handler(handler)
        await self.adapter._process_message_background(self.event, 'test:failure')
        self.assertEqual(len(self.sent), 1)
        self.assertIn('test failure', self.edits[-1].request_body.content)

    async def test_media_delivery_keeps_text_replacement(self):
        received_images = []
        async def send_images(chat_id, images, **kwargs):
            received_images.extend(images)
        self.adapter.send_multiple_images = send_images
        async def handler(event):
            await self.progress()
            return '已生成图片。 ![图片](https://example.com/result.png)'
        self.adapter.set_message_handler(handler)
        await self.adapter._process_message_background(self.event, 'test:media')
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(received_images)
        self.assertIn('已生成图片', self.edits[-1].request_body.content)


if __name__ == '__main__':
    unittest.main()

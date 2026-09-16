import asyncio
import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, '/opt/hermes')
import plugins.platforms.feishu.adapter as mod
from gateway.config import PlatformConfig
from gateway.session import build_session_key

class ThreadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.adapter = mod.FeishuAdapter(PlatformConfig())
        self.adapter._extract_message_content = AsyncMock(return_value=('thread test', mod.MessageType.TEXT, [], [], []))
        self.adapter._fetch_message_text = AsyncMock(return_value='original question')
        self.adapter.get_chat_info = AsyncMock(return_value={'name':'test'})
        self.adapter._resolve_sender_profile = AsyncMock(return_value={'user_id':'ou_user', 'user_name':'Test', 'user_id_alt':None})
        self.adapter._dispatch_inbound_event = AsyncMock()
        self.requests = []
        def reply(request):
            self.requests.append(('reply', request))
            return NS(success=lambda:True, data=NS(message_id='om_answer'))
        def create(request):
            self.requests.append(('create', request))
            return NS(success=lambda:True, data=NS(message_id='om_answer'))
        self.adapter._client = NS(im=NS(v1=NS(message=NS(reply=reply, create=create))))
    async def inbound(self, id='om_question', root=None, thread=None, kind='group'):
        msg = NS(chat_id='oc_chat', root_id=root, thread_id=thread, parent_id=root)
        await self.adapter._process_inbound_message(data=msg,message=msg,sender_id=None,chat_type=kind,message_id=id)
        return self.adapter._dispatch_inbound_event.call_args.args[0]
    async def test_first_group_reply_creates_thread(self):
        event = await self.inbound()
        result = await self.adapter.send(event.source.chat_id,'answer',reply_to=event.message_id,metadata={'thread_id':event.source.thread_id})
        self.assertTrue(result.success)
        mode, req = self.requests[-1]
        self.assertEqual(mode,'reply')
        self.assertEqual(req.message_id,'om_question')
        self.assertTrue(req.request_body.reply_in_thread)
    async def test_followup_keeps_session_and_new_question_is_separate(self):
        first = await self.inbound()
        follow = await self.inbound('om_followup','om_question','omt_actual')
        other = await self.inbound('om_other')
        self.assertEqual(build_session_key(first.source),build_session_key(follow.source))
        self.assertNotEqual(build_session_key(first.source),build_session_key(other.source))
        self.assertEqual(follow.reply_to_text,'original question')
    async def test_progress_without_reply_anchor_stays_in_thread(self):
        await self.adapter.send('oc_chat','progress',metadata={'thread_id':'om_question'})
        mode, req = self.requests[-1]
        self.assertEqual(mode,'reply')
        self.assertEqual(req.message_id,'om_question')
        self.assertTrue(req.request_body.reply_in_thread)
    async def test_private_chat_remains_flat(self):
        event = await self.inbound(kind='p2p')
        self.assertIsNone(event.source.thread_id)
        await self.adapter.send('oc_chat','answer',reply_to=event.message_id)
        self.assertFalse(self.requests[-1][1].request_body.reply_in_thread)
    async def test_existing_topic_without_root_uses_topic_route(self):
        event = await self.inbound('om_followup',thread='omt_actual')
        self.assertEqual(event.source.thread_id,'omt_actual')
        await self.adapter.send('oc_chat','progress',metadata={'thread_id':event.source.thread_id})
        mode, req = self.requests[-1]
        self.assertEqual(mode,'create')
        self.assertEqual(req.receive_id_type,'thread_id')
        self.assertEqual(req.request_body.receive_id,'omt_actual')
    async def test_withdrawn_question_does_not_spam_group(self):
        self.adapter._client.im.v1.message.reply = Mock(return_value=NS(success=lambda:False,code=230011,msg='missing'))
        self.adapter._client.im.v1.message.create = Mock(side_effect=AssertionError('flat send forbidden'))
        result=await self.adapter.send('oc_chat','answer',reply_to='om_question',metadata={'thread_id':'om_question'})
        self.assertFalse(result.success)
        self.adapter._client.im.v1.message.create.assert_not_called()

unittest.main(verbosity=2)

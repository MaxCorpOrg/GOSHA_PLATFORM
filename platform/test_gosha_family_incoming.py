"""Read-only speech and coordinated inbox tests, without APIs or hardware."""
import asyncio
import base64
import json
import math
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from gosha_family_live_speech import render, rendering_config, words, profile_config
from gosha_family_audio import FamilySpeech, packetize, spoken_text
from gosha_family_store import FamilyStore
from gosha_family_voice import FamilyVoiceTools
from gosha_family_incoming import FamilyIncoming
from gosha_live_audio import PCM_BYTES

TONE = struct.pack('<960h', *[int(5000*math.sin(i*2*math.pi*440/16000)) for i in range(960)])


class FakeSession:
    transcript = 'Папа пишет: Я дома.'
    event = None
    last = None
    def __init__(self, config, key):
        self.config = config
        self.queue = asyncio.Queue()
        self.ws = SimpleNamespace(close=AsyncMock())
        self.closing = False
        self.finalized = asyncio.Event()
        self.calls = []
        type(self).last = self
    async def start(self):
        pass
    async def send_audio(self, pcm):
        pass
    async def send(self, kind, **fields):
        self.calls.append((kind, fields))
        if kind == 'session.instructions.append':
            self.queue.put_nowait({'type': 'session.instructions.appended'})
        if kind == 'session.commentary.append':
            if self.event:
                self.queue.put_nowait(self.event)
            else:
                self.queue.put_nowait({'type': 'session.output_audio.delta',
                                      'delta': base64.b64encode(TONE).decode()})
                self.queue.put_nowait({'type': 'session.output_transcript.delta', 'delta': self.transcript})
    async def events(self):
        while True:
            event = await self.queue.get()
            if event['type'] == 'session.closed':
                self.finalized.set()
                yield event
                return
            yield event
    async def close(self):
        self.closing = True
        self.queue.put_nowait({'type': 'session.closed'})
        await asyncio.wait_for(self.finalized.wait(), .5)
        await self.ws.close()


class RenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_voice_read_only_config_and_verified_pcm(self):
        pcm = await render('Папа пишет: Я дома.', 'meridian', 'fake',
                           session_factory=FakeSession, timeout=1, quiet_seconds=.01)
        self.assertEqual(pcm, TONE)
        session = FakeSession.last
        self.assertEqual(session.config['audio']['output']['voice'], 'meridian')
        self.assertEqual(session.config['delegation'], {'type': 'client'})
        self.assertFalse(session.config['store'])
        self.assertNotIn('tools', json.dumps(session.config['delegation']))
        self.assertTrue(session.finalized.is_set())
        self.assertEqual([call[0] for call in session.calls],
                         ['session.instructions.append','session.commentary.append'])

    async def test_wrong_text_is_never_served_and_session_finalizes(self):
        class Wrong(FakeSession):
            transcript = 'Папа пишет: Я не дома.'
        with self.assertRaises(TimeoutError):
            await render('Папа пишет: Я дома.', 'meridian', 'fake', session_factory=Wrong,
                         timeout=.13, quiet_seconds=.01)
        self.assertTrue(Wrong.last.finalized.is_set())

    async def test_delegation_and_invalid_audio_are_rejected(self):
        for event in ({'type': 'session.delegation.created'},
                      {'type': 'session.output_audio.delta','delta': base64.b64encode(b'x').decode()}):
            class Invalid(FakeSession):
                pass
            Invalid.event = event
            with self.assertRaises((RuntimeError, ValueError)):
                await render('Папа пишет: Я дома.', 'meridian', 'fake', session_factory=Invalid,
                             timeout=.2, quiet_seconds=.01)
            Invalid.last.ws.close.assert_awaited()

    def test_voice_and_input_validation(self):
        for voice, text in (('coral','hello'), ('meridian',''), ('meridian','x'*2401)):
            with self.assertRaises(ValueError):
                rendering_config(text, voice)
        self.assertEqual(words('«Ёлка!» — дома.'), 'елка дома')

    def test_selected_profile_and_provider_key_are_used(self):
        with patch.dict('os.environ', {'GOSHA_FAMILY_TTS_ENABLED': '1'}), \
             patch('gosha_assistant_store.effective_robot_assistant_config', return_value={
                 'assistant_profile': {'voice_engine':'openai_live','live_voice':'meridian'},
                 'provider_profile': {'profile_id':'chosen'}}), \
             patch('gosha_agent_store.get_agent_profile', return_value={
                 'enabled':True,'base_url':'https://api.openai.com/v1'}) as get, \
             patch('gosha_agent_store.resolve_api_key', return_value='fake-profile-key'):
            self.assertEqual(profile_config('any-robot'),
                             {'kind':'openai_live','voice':'meridian','key':'fake-profile-key'})
            get.assert_called_once_with('chosen')


class AudioPartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FamilyStore(Path(self.temp.name)/'messages.sqlite3')
        self.store.configure('robot', True, 'ask')
        parent = self.store.add_contact('robot','Папа','father')
        self.message = self.store.parent_send('robot',parent,'Секретное сообщение','request')

    def test_notice_is_not_body_and_does_not_acknowledge_reading(self):
        texts = []
        service = FamilySpeech(lambda text, config: texts.append(text) or b'audio', lambda: ())
        service.audio(self.store,'robot',self.message,'notice')
        self.assertEqual(texts, ['Папа прислал сообщение. Двойное нажатие — прочитать.'])
        self.assertEqual(self.store.mailbox('robot')['messages'][0]['state'],'queued')
        service.audio(self.store,'robot',self.message,'offer')
        self.assertEqual(texts[-1], 'Папа прислал сообщение. Прочитать?')
        self.assertNotIn('Секретное', texts[-1])
        service.audio(self.store,'robot',self.message,'reading')
        self.assertEqual(texts[-1], 'Ага. Папа говорит: Секретное сообщение')
        self.assertEqual(self.store.mailbox('robot')['messages'][0]['state'],'queued')
        with self.assertRaises(ValueError):
            service.audio(self.store,'robot',self.message,'arbitrary')

    def test_cache_changes_when_voice_profile_or_audio_part_changes(self):
        profile = {'kind':'test','voice':'one'}
        calls = []
        service = FamilySpeech(lambda text, config: calls.append((text,dict(config))) or b'audio',
                               robot_configuration=lambda robot: profile)
        service.audio(self.store,'robot',self.message)
        service.audio(self.store,'robot',self.message)
        service.audio(self.store,'robot',self.message,'notice')
        profile['voice']='two'
        service.audio(self.store,'robot',self.message)
        self.assertEqual(len(calls),3)
        self.assertEqual([call[1]['voice'] for call in calls], ['one','one','two'])


class InboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = FamilyStore(Path(self.temp.name)/'messages.sqlite3')
        store.configure('robot', True, 'ask')
        parent = store.add_contact('robot','Папа','father')
        self.message = store.parent_send('robot',parent,'private-inbox-body','request')
        self.bridge = SimpleNamespace(family_tools=FamilyVoiceTools(store,'robot'),
            family_playback_supported=True, listening=True, speaking=False,
            output=asyncio.Queue(), output_tail=bytearray(), input=asyncio.Queue(),
            device_tools=SimpleNamespace(pending={}), tool_runner=None, backend_responses=set(),
            idle_farewell=None, accept_output=True, restart=asyncio.Event(), stopped=asyncio.Event(),
            family_paused=False, last_activity=0, robot_listening_ready=asyncio.Event(),
            fresh_config=lambda: {'delegation':{'responses':{'instructions':'Базовые инструкции'}}},
            send_json=AsyncMock())
        self.inbox = FamilyIncoming(self.bridge, timeout=.1)

    async def test_pause_waits_for_matching_device_finish_and_no_text_enters_live(self):
        session = SimpleNamespace(send=AsyncMock())
        task = asyncio.create_task(self.inbox.read(session,self.message,False))
        await asyncio.sleep(0)
        self.assertTrue(self.bridge.family_paused)
        self.inbox.receive({'payload':{'request_id':'foreign','state':'finished'}})
        self.assertFalse(task.done())
        request = self.bridge.send_json.await_args.kwargs['payload']
        self.inbox.receive({'payload':{'request_id':request['request_id'],'state':'finished'}})
        self.assertEqual(await task,'finished')
        self.assertFalse(self.bridge.family_paused)
        calls = json.dumps(session.send.await_args_list, default=str)
        self.assertNotIn('private-inbox-body',calls)
        self.assertNotIn(self.message,calls)
        self.assertIn('session.input_audio.mute',calls)
        self.assertIn('session.input_audio.unmute',calls)
        # Only the device may acknowledge actual playback; the coordinator does not.
        self.assertEqual(self.bridge.family_tools.store.mailbox('robot')['messages'][0]['state'],'queued')

    async def test_timeout_closes_call_before_any_audio_resume(self):
        self.inbox.timeout=.01
        session = SimpleNamespace(send=AsyncMock())
        self.assertEqual(await self.inbox.read(session,self.message,False),'unavailable')
        self.assertTrue(self.bridge.stopped.is_set())
        self.assertNotIn('session.input_audio.unmute',[call.args[0] for call in session.send.await_args_list])

    async def test_manual_read_requires_capability_and_marks_explicit_request(self):
        self.bridge.family_playback_supported=False
        self.assertEqual((await self.inbox.request_read())['status'],'rejected')
        self.bridge.family_playback_supported=True
        self.assertEqual((await self.inbox.request_read())['reason'],'fresh_reading_request_required')
        self.bridge.family_tools.transcript('user','Прочитай сообщение')
        self.assertEqual((await self.inbox.request_read())['status'],'requested')
        self.assertTrue(self.inbox.explicit)
        self.bridge.family_tools.read_messages=self.inbox.request_read
        self.assertEqual((await self.bridge.family_tools.call('family_read_messages','{}'))['status'],'requested')

    async def finish(self, task):
        for _ in range(30):
            if self.inbox.pending and self.bridge.send_json.await_args:
                break
            await asyncio.sleep(.001)
        request = self.bridge.send_json.await_args.kwargs['payload']
        self.inbox.receive({'payload':{'request_id':request['request_id'],'state':'finished'}})
        return await task

    async def test_offer_requires_a_reply_after_its_audio_and_targets_that_message(self):
        self.bridge.family_tools.transcript('user','давай')  # Before the offer: no consent.
        session = SimpleNamespace(send=AsyncMock())
        task = asyncio.create_task(self.inbox.read(session,self.message,False,notice=True,author='Папа'))
        self.assertEqual(await self.finish(task),'finished')
        self.assertEqual(self.bridge.send_json.await_args.kwargs['payload']['action'],'notice')
        self.assertEqual((await self.inbox.request_read())['reason'],'fresh_reading_request_required')
        self.bridge.family_tools.interrupt()  # Generation alone must not constitute an answer.
        self.assertEqual((await self.inbox.request_read())['status'],'rejected')
        self.bridge.family_tools.transcript('user','Ну давай уже, читай')
        self.assertEqual((await self.inbox.request_read())['status'],'requested')
        self.assertEqual(self.inbox.requested_id,self.message)
        self.assertNotIn('private-inbox-body',str(session.send.await_args_list))
        update = next(call.kwargs['session'] for call in session.send.await_args_list if call.args[0]=='session.update')
        context=update['delegation']['responses']['instructions']
        self.assertIn('Прочитать?',context)
        self.assertIn('Папа',context)
        self.assertNotIn('private-inbox-body',context)
        self.assertEqual(self.bridge.family_tools.store.mailbox('robot')['messages'][0]['state'],'queued')

    async def test_refusal_keeps_unread_and_button_can_read_without_voice_reply(self):
        session=SimpleNamespace(send=AsyncMock())
        await self.finish(asyncio.create_task(self.inbox.read(session,self.message,False,notice=True,author='Папа')))
        self.bridge.family_tools.defer_reading=self.inbox.defer_reading
        self.assertEqual((await self.bridge.family_tools.call('family_defer_reading','{}'))['status'],'rejected')
        self.bridge.family_tools.transcript('user','Попозже, сейчас занят')
        self.assertEqual((await self.bridge.family_tools.call('family_defer_reading','{}'))['status'],'confirmed')
        self.assertIsNone(self.inbox.offered)
        self.assertIn(self.message,self.inbox.announced)
        self.assertEqual(self.bridge.family_tools.store.mailbox('robot')['messages'][0]['state'],'queued')
        self.inbox.receive({'payload':{'state':'read_requested'}})
        self.assertTrue(self.inbox.explicit)

    async def test_completed_reading_asks_reply_after_resume_without_repeating_body(self):
        session = SimpleNamespace(send=AsyncMock())
        task = asyncio.create_task(self.inbox.read(session,self.message,True,author='Папа'))
        self.assertEqual(await self.finish(task), 'finished')
        self.assertTrue(self.bridge.listening)
        self.assertFalse(self.bridge.stopped.is_set())
        self.assertFalse(self.bridge.family_paused)
        calls = session.send.await_args_list
        kinds = [call.args[0] for call in calls]
        self.assertLess(kinds.index('session.input_audio.unmute'), kinds.index('session.commentary.append'))
        self.assertIn('Хочешь ему ответить?', calls[-1].kwargs['content'])
        context = next(call.kwargs['session']['delegation']['responses']['instructions']
                       for call in calls if call.args[0] == 'session.update')
        self.assertIn('Папа',context)
        self.assertIn('отдельное подтверждение отправки',context)
        self.assertNotIn('private-inbox-body', str(calls))
        self.assertFalse(self.inbox.backend_offer_active)

    async def test_interrupted_reading_does_not_offer_a_reply(self):
        session = SimpleNamespace(send=AsyncMock())
        task = asyncio.create_task(self.inbox.read(session,self.message,True,author='Мама'))
        for _ in range(30):
            if self.inbox.pending and self.bridge.send_json.await_args: break
            await asyncio.sleep(.001)
        request = self.bridge.send_json.await_args.kwargs['payload']
        self.inbox.receive({'payload':{'request_id':request['request_id'],'state':'interrupted'}})
        self.assertEqual(await task,'interrupted')
        self.assertNotIn('session.commentary.append',[call.args[0] for call in session.send.await_args_list])
        self.assertTrue(self.bridge.listening)
        self.assertFalse(self.bridge.stopped.is_set())

    async def test_deferred_offer_clears_backend_context(self):
        session=SimpleNamespace(send=AsyncMock())
        self.inbox.backend_offer_active=True
        self.inbox.announced.add(self.message)
        self.bridge.robot_listening_ready.set()
        self.inbox.interval=.002
        task=asyncio.create_task(self.inbox.run(session))
        await asyncio.sleep(.02)
        self.bridge.stopped.set();await task
        self.assertFalse(self.inbox.backend_offer_active)
        update=session.send.await_args_list[0]
        self.assertEqual(update.args[0],'session.update')
        self.assertEqual(update.kwargs['session']['delegation']['responses']['instructions'], 'Базовые инструкции\n')

    async def test_ask_loop_offers_once_without_automatic_reading(self):
        self.bridge.robot_listening_ready.set()
        self.inbox.interval=.002
        self.inbox.read=AsyncMock()
        async def offer(session, message_id, explicit, **kwargs):
            self.inbox.announced.add(message_id)
            self.inbox.offered={'id':message_id,'generation':0,'at':time.monotonic()}
            return 'finished'
        self.inbox.read.side_effect=offer
        task=asyncio.create_task(self.inbox.run(None))
        await asyncio.sleep(.03)
        self.bridge.stopped.set()
        await task
        self.inbox.read.assert_awaited_once_with(None,self.message,False,notice=True,author='Папа')

    async def test_auto_reads_and_manual_request_uses_the_offered_id(self):
        store=self.bridge.family_tools.store
        parent=store.add_contact('robot','Мама','mother')
        other=store.parent_send('robot',parent,'second-private','second')
        self.inbox.offered={'id':other,'generation':0,'at':time.monotonic()}
        self.inbox.receive({'payload':{'state':'read_requested'}})
        self.assertEqual(self.inbox.requested_id,other)
        self.bridge.robot_listening_ready.set()
        self.inbox.interval=.002
        async def read(*args, **kwargs):
            self.bridge.stopped.set()
            return 'finished'
        self.inbox.read=AsyncMock(side_effect=read)
        await self.inbox.run(None)
        self.inbox.read.assert_awaited_once_with(None,other,True,notice=False,author='Мама')
        self.bridge.stopped.clear()
        self.inbox.explicit=False
        self.inbox.attempted.clear()
        store.configure('robot',True,'auto')
        self.inbox.read.reset_mock()
        await self.inbox.run(None)
        self.inbox.read.assert_awaited_once_with(None,self.message,False,notice=False,author='Папа')

    async def test_incoming_defers_speech_draft_and_pending_backend_actions(self):
        self.assertTrue(self.inbox.idle())
        self.bridge.output.put_nowait(bytes(PCM_BYTES))
        self.assertTrue(self.inbox.idle())  # Continuous Live silence must not starve notifications.
        self.bridge.output.put_nowait(TONE)
        self.assertFalse(self.inbox.idle())
        self.bridge.output.get_nowait(); self.bridge.output.get_nowait()
        self.bridge.speaking=True
        self.assertFalse(self.inbox.idle())
        self.bridge.speaking=False
        self.bridge.family_tools.draft={'expires':time.monotonic()+120}
        self.assertFalse(self.inbox.idle())
        self.bridge.family_tools.draft=None
        self.bridge.backend_responses.add('reasoning')
        self.assertFalse(self.inbox.idle())
        self.bridge.backend_responses.clear()
        self.bridge.tool_runner=SimpleNamespace(responses={'response':[{'call_id':'unfinished'}]},seen=set())
        self.assertFalse(self.inbox.idle())
        self.bridge.tool_runner.seen.add('unfinished')
        self.assertTrue(self.inbox.idle())


if __name__ == '__main__':
    unittest.main()

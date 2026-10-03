"""Offline voice-consent, speech and OTA tests; no paid API or physical robot."""
import asyncio
from concurrent.futures import Future
from email.message import Message
import io
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, AsyncMock

from gosha_family_store import FamilyStore
from gosha_family_voice import FamilyVoiceTools, ConversationTools
from gosha_family_audio import FamilySpeech, packetize, resample, synthesize, NoRedirect, MAX_WIRE
from gosha_live_audio import OpusCodec, PCM_BYTES, audible


class Fixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FamilyStore(Path(self.tmp.name)/'family/messages.sqlite3')
        self.store.configure('a', True, 'ask')
        self.mom = self.store.add_contact('a', 'Мама', 'mother')
        self.dad = self.store.add_contact('a', 'Папа', 'father')
        for n, contact in enumerate((self.mom, self.dad)):
            token = self.store.invite('a', contact)
            self.store.telegram_update({'update_id': n, 'message': {'text': '/start '+token,
                'chat': {'id': n+1, 'type': 'private'}, 'from': {'is_bot': False}}})
            binding = self.store.snapshot('a')['contacts'][n]['channels'][0]['id']
            self.store.binding('a', binding, 'approve')


class VoiceTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def prepare(self, ids=None, text='Я дома'):
        result = await self.tools.call('family_prepare_message', json.dumps({'recipient_ids': ids or [self.dad], 'text': text}))
        self.assertEqual(result['status'], 'needs_confirmation')
        return result['draft_id']

    def setUp(self):
        super().setUp()
        self.judge = AsyncMock(return_value={'readback_matches': True, 'decision': 'send'})
        self.tools = FamilyVoiceTools(self.store, 'a', quiet_seconds=0, consent=self.judge)

    async def send(self, draft):
        return await self.tools.call('family_send_message', json.dumps({'draft_id': draft}))

    def readback(self, text='Передать папе: Я дома. Отправить?'):
        self.tools.transcript('assistant', text)
        self.tools.output_drained()

    async def test_readback_new_confirmation_and_idempotency(self):
        draft = await self.prepare()
        self.tools.transcript('user', 'да, отправь')  # Before readback is insufficient.
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.readback()
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.tools.transcript('user', 'Да, ')
        self.tools.transcript('user', 'отправь!')
        result = await self.send(draft)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result, await self.send(draft))
        snapshot = self.store.snapshot('a')['messages']
        self.assertEqual(len(snapshot), 1)
        self.assertEqual([d['name'] for d in snapshot[0]['deliveries']], ['Папа'])

    async def test_correction_negation_interrupt_and_expiry(self):
        for phrase, decision in (('да, не отправляй', 'cancel'), ('да, отправь маме', 'revise'),
                                 ('нет', 'cancel'), ('да, отправь, хотя нет', 'cancel')):
            self.judge.return_value = {'readback_matches': True, 'decision': decision}
            draft = await self.prepare()
            self.readback()
            self.tools.transcript('user', phrase)
            self.assertEqual((await self.send(draft))['status'], 'cancelled' if decision == 'cancel' else 'rejected')
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'да отправь')
        self.tools.interrupt()
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'да отправь')
        self.tools.draft['expires'] = 0
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_natural_reply_is_passed_whole_without_phrase_filter(self):
        for reply in ('Да', 'Отправляй', 'Давай быстрее, отправь уже',
                      'Конечно', 'Ну сделай это', 'Передай как есть'):
            draft = await self.prepare()
            self.readback()
            self.tools.transcript('user', reply)
            self.assertEqual((await self.send(draft))['status'], 'queued')
            self.assertEqual(self.judge.await_args.args[2], reply)
        self.assertEqual(len(self.store.snapshot('a')['messages']), 6)

    async def test_late_readback_transcript_after_audio_is_accepted(self):
        draft = await self.prepare()
        self.tools.output_started()
        self.tools.output_drained()
        self.tools.transcript('assistant', 'Передать папе: Я дома. Отправить?')
        self.tools.transcript('user', 'Отправляй')
        self.assertEqual((await self.send(draft))['status'], 'queued')
        self.assertEqual(self.judge.await_args.args[1], 'Передать папе: Я дома. Отправить?')

    async def test_acknowledgement_audio_keeps_question_and_fresh_reply(self):
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'Да')
        self.tools.output_started()
        self.tools.transcript('assistant', 'Хорошо, сейчас передам.')
        self.tools.output_drained()
        self.assertEqual((await self.send(draft))['status'], 'queued')
        self.assertEqual(self.judge.await_args.args[1:], ('Передать папе: Я дома. Отправить?', 'Да'))

    async def test_new_audio_segment_closes_gate_until_drained(self):
        draft = await self.prepare()
        self.readback('Передать папе:')
        self.tools.output_started()
        self.tools.transcript('user', 'Да')
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.tools.transcript('assistant', ' Я дома. Отправить?')
        self.tools.output_drained()
        self.tools.transcript('user', 'Да')
        self.assertEqual((await self.send(draft))['status'], 'queued')

    async def test_correction_during_judgement_prevents_enqueue(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def judge(*args):
            started.set()
            await release.wait()
            return {'readback_matches': True, 'decision': 'send'}
        self.tools.consent = judge
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'Да')
        pending = asyncio.create_task(self.send(draft))
        await started.wait()
        self.tools.transcript('user', ', нет, не надо')
        release.set()
        self.assertEqual((await pending)['status'], 'rejected')
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_replaced_draft_during_judgement_prevents_old_send(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def judge(*args):
            started.set()
            await release.wait()
            return {'readback_matches': True, 'decision': 'send'}
        self.tools.consent = judge
        old_draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'Да')
        pending = asyncio.create_task(self.send(old_draft))
        await started.wait()
        new_draft = await self.prepare([self.mom], text='Приходи скорее')
        release.set()
        self.assertEqual((await pending)['status'], 'rejected')
        self.assertEqual(self.tools.draft['id'], new_draft)
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_unavailable_judge_never_enqueues_and_can_retry_naturally(self):
        from gosha_family_consent import ConsentError
        self.judge.side_effect = ConsentError('consent_provider_unavailable')
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'Да')
        result = await self.send(draft)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.store.snapshot('a')['messages'], [])
        self.judge.side_effect = None
        self.tools.transcript('user', '. Отправляй')
        self.assertEqual((await self.send(draft))['status'], 'queued')

    async def test_question_or_revision_does_not_enqueue(self):
        for decision in ('clarify', 'revise'):
            draft = await self.prepare()
            self.readback()
            self.tools.transcript('user', 'Новая реплика')
            self.judge.return_value = {'readback_matches': True, 'decision': decision}
            self.assertEqual((await self.send(draft))['status'], 'rejected')
            self.assertIsNone(self.tools.draft)
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_refusal_or_revision_is_honoured_even_for_bad_readback(self):
        for decision in ('cancel', 'revise'):
            draft = await self.prepare()
            self.readback('Передать маме: Я дома. Отправить?')
            self.tools.transcript('user', 'Отмена или исправление')
            self.judge.return_value = {'readback_matches': False, 'decision': decision}
            result = await self.send(draft)
            self.assertEqual(result['reason'], 'message_'+decision)
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_canonical_readback_preserves_contact_names_and_requires_new_consent(self):
        for ids in ([self.dad], [self.mom, self.dad]):
            result = await self.tools.call('family_prepare_message', json.dumps({
                'recipient_ids': ids, 'text': 'Я дома'}))
            draft = result['draft_id']
            # Consent given before the complete readback remains insufficient.
            self.tools.transcript('user', 'Да, отправь')
            self.assertEqual((await self.send(draft))['status'], 'rejected')
            self.readback(result['readback_text'])
            self.assertTrue(self.tools.armed)
            self.assertEqual((await self.send(draft))['status'], 'rejected')
            self.tools.transcript('user', 'Да, отправь')
            self.assertEqual((await self.send(draft))['status'], 'queued')
        messages = self.store.snapshot('a')['messages']
        self.assertEqual(sorted(len(m['deliveries']) for m in messages), [1, 2])

    async def test_parent_inflections_keep_exact_recipient_scope(self):
        for phrase in ('Передать папе', 'Сообщение для папы', 'Отправить маме', 'Папайя'):
            matches = phrase in ('Передать папе', 'Сообщение для папы')
            self.judge.return_value = {'readback_matches': matches, 'decision': 'send'}
            draft = await self.prepare()
            self.readback(phrase + '. Я дома. Отправить?')
            self.tools.transcript('user', 'Да, отправь')
            expected = 'queued' if phrase in ('Передать папе', 'Сообщение для папы') else 'rejected'
            self.assertEqual((await self.send(draft))['status'], expected)
        draft = await self.prepare([self.mom, self.dad])
        self.judge.return_value = {'readback_matches': False, 'decision': 'send'}
        self.readback('Передать папе. Я дома. Отправить?')
        self.tools.transcript('user', 'Да, отправь')
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        draft = await self.prepare([self.mom, self.dad])
        self.judge.return_value = {'readback_matches': True, 'decision': 'send'}
        self.readback('Передать маме и папе. Я дома. Отправить?')
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.tools.transcript('user', 'Да, отправь')
        self.assertEqual((await self.send(draft))['status'], 'queued')

    async def test_published_inventory_includes_family_after_device_discovery(self):
        from gosha_voice_router import RobotLiveBridge
        class Robot:
            async def send(self, text):
                pass
        bridge = RobotLiveBridge(Robot(), {'delegation': {'responses': {}}}, 'unused')
        bridge.family_tools = self.tools
        bridge.execution_tools = ConversationTools(bridge.device_tools, self.tools)
        bridge.mcp_supported = True
        bridge.device_tools.ready.set()
        bridge.stopped.set()
        sent = []
        class Session:
            async def send(self, kind, **payload):
                sent.append((kind, payload))
        class Runner:
            disabled = False
        await bridge.publish_tools(Session(), Runner())
        definitions = sent[0][1]['session']['delegation']['responses']['tools']
        instruction = sent[1][1]['content']
        for definition in definitions:
            self.assertIn(definition['name'], instruction)
        self.assertIn('family_prepare_message', instruction)
        self.assertIn('family_send_message', instruction)

    async def test_recipient_readback_scope_and_revocation(self):
        result = await self.tools.call('family_prepare_message', json.dumps({'recipient_ids': ['foreign'], 'text': 'test'}))
        self.assertEqual(result['status'], 'rejected')
        draft = await self.prepare([self.mom, self.dad])
        self.judge.return_value = {'readback_matches': False, 'decision': 'send'}
        self.readback()  # Omits mother.
        self.tools.transcript('user', 'да отправь')
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        draft = await self.prepare([self.mom, self.dad])
        self.judge.return_value = {'readback_matches': True, 'decision': 'send'}
        self.readback(' Мама и папа. Я дома. Отправить?')
        self.tools.transcript('user', 'да отправь')
        self.store.configure('a', False, 'ask')
        self.assertEqual((await self.send(draft))['status'], 'rejected')

    async def test_late_transcript_invalidates_inflight_confirmation(self):
        self.tools.quiet_seconds = 0.04
        self.judge.return_value = {'readback_matches': True, 'decision': 'cancel'}
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'да отправь')
        send = asyncio.create_task(self.send(draft))
        await asyncio.sleep(0.01)
        self.tools.transcript('user', ', нет, не надо')
        self.assertEqual((await send)['status'], 'cancelled')
        self.assertEqual(self.judge.await_args.args[2], 'да отправь, нет, не надо')
        self.assertEqual(self.store.snapshot('a')['messages'], [])

    async def test_voice_diagnostics_do_not_log_family_content_or_ids(self):
        private_text = 'Личное сообщение семьесекрет'
        self.judge.return_value = {'readback_matches': True, 'decision': 'cancel'}
        with self.assertLogs('gosha.family.voice', level='INFO') as captured:
            draft = await self.prepare(text=private_text)
            self.readback('Папа. '+private_text+'. Отправить?')
            self.tools.transcript('user', 'Нет, семьесекрет не отправляй')
            self.assertEqual((await self.send(draft))['status'], 'cancelled')
            await self.tools.call('invalid-семьесекрет', '{}')
        joined = '\n'.join(captured.output)
        self.assertIn('decision=cancel', joined)
        self.assertIn('operation=invalid', joined)
        for secret in ('семьесекрет', self.dad, draft, private_text, 'Папа'):
            self.assertNotIn(secret, joined)

    async def test_family_tools_do_not_wait_for_mcp(self):
        from gosha_live_tools import DeviceTools
        async def unused(*a, **kw):
            self.fail('Physical operation attempted')
        device = DeviceTools(unused)
        combined = ConversationTools(device, self.tools)
        self.assertFalse(device.ready.is_set())
        self.assertTrue(combined.ready.is_set())
        result = await asyncio.wait_for(combined.call('family_list_recipients', '{}'), 1)
        self.assertEqual(len(result['recipients']), 2)

    async def test_complete_offline_parent_roundtrip(self):
        from gosha_family_telegram import deliver_one
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'да отправь')
        outgoing = await self.send(draft)
        class Telegram:
            def call(inner, method, payload):
                self.assertEqual(method, 'sendMessage')
                self.assertEqual(payload['chat_id'], '2')
                self.assertIn('Я дома', payload['text'])
                return {'message_id': 777}
        self.assertTrue(deliver_one(self.store, Telegram()))
        self.assertFalse(deliver_one(self.store, Telegram()))
        update = {'update_id': 999, 'message': {'text': 'Скоро буду, жди дома',
            'chat': {'id': 2, 'type': 'private'}, 'reply_to_message': {'message_id': 777}}}
        self.assertEqual(self.store.telegram_update(update), 'queued')
        self.assertEqual(self.store.telegram_update(update), 'duplicate')
        incoming = self.store.mailbox('a')['messages'][0]
        self.assertEqual(incoming['author'], 'Папа')
        self.store.acknowledge('a', incoming['id'], 'received')
        spoken_text = []
        audio = FamilySpeech(lambda text, _: spoken_text.append(text) or packetize(bytes(1920)), lambda: ())
        self.assertTrue(audio.audio(self.store, 'a', incoming['id']).startswith(b'GFM1'))
        self.assertEqual(spoken_text, ['Папа говорит: Скоро буду, жди дома'])
        self.assertEqual(self.store.mailbox('a')['messages'][0]['state'], 'received')
        self.store.acknowledge('a', incoming['id'], 'spoken')
        self.store.acknowledge('a', incoming['id'], 'spoken')
        self.assertEqual(self.store.mailbox('a')['messages'], [])
        delivered = next(m for m in self.store.snapshot('a')['messages'] if m['id'] == outgoing['message_id'])
        self.assertEqual(delivered['deliveries'][0]['state'], 'sent')

    async def test_bridge_transcripts_and_stop_arm_readback(self):
        from gosha_voice_router import RobotLiveBridge
        class Robot:
            async def send(self, text):
                pass
        config = {'delegation': {'responses': {'instructions': 'base'}}}
        bridge = RobotLiveBridge(Robot(), config, 'unused', family_robot_id='a')
        bridge.family_tools = self.tools
        self.assertTrue(any(d['name'] == 'family_send_message' for d in bridge.fresh_config()['delegation']['responses']['tools']))
        draft = await self.prepare()
        class Session:
            async def events(self):
                yield {'type': 'session.output_transcript.delta', 'delta': 'Папа. Я дома. Отправить?'}
        await bridge.read_live(Session())
        bridge.speaking = True
        await bridge.stop_speech()
        self.tools.transcript('user', 'да отправь')
        self.assertEqual((await self.send(draft))['status'], 'queued')


class SpeechTests(Fixture, unittest.TestCase):
    def test_speech_rejects_redirect_and_html_success_response(self):
        with self.assertRaisesRegex(ValueError, 'redirect'):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test')
        class Response(io.BytesIO):
            status = 200
            headers = Message()
        response = Response(b'<html>not audio</html>')
        response.headers['Content-Type'] = 'text/html'
        with patch('gosha_family_audio.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(RuntimeError, 'speech_failed'):
                synthesize('Test', ('test', 'voice', 'fake-key'))

    def test_long_mailbox_batch_fits_firmware_budget_and_advances(self):
        ids = [self.store.parent_send('a', self.mom, 'я'*2000, 'm'+str(i)) for i in range(10)]
        seen = []
        while True:
            batch = self.store.mailbox('a')
            self.assertLess(len(json.dumps(batch).encode()), 24*1024)
            if not batch['messages']:
                break
            for item in batch['messages']:
                seen.append(item['id'])
                self.store.acknowledge('a', item['id'], 'spoken')
        self.assertEqual(seen, ids)

    def test_real_resampling_packet_framing_and_decode(self):
        raw = struct.pack('<24000h', *[int(7000*math.sin(i*2*math.pi*440/24000)) for i in range(24000)])
        pcm = resample(raw)
        self.assertEqual(len(pcm), 32000)
        wire = packetize(pcm)
        self.assertEqual(struct.unpack('!4sIHBB', wire[:12]), (b'GFM1',16000,60,1,0))
        codec = OpusCodec()
        frames, offset, decoded = 0, 12, b''
        try:
            while True:
                size, = struct.unpack('!H', wire[offset:offset+2])
                offset += 2
                if size == 0:
                    break
                self.assertLessEqual(size, 1275)
                decoded += codec.decode(wire[offset:offset+size])
                offset += size
                frames += 1
        finally:
            codec.close()
        self.assertEqual(offset, len(wire))
        self.assertEqual(frames, 18)
        self.assertTrue(audible(decoded))
        self.assertLess(len(wire), MAX_WIRE)
        with self.assertRaises(ValueError):
            resample(b'odd')
        with self.assertRaises(ValueError):
            packetize(bytes(16000*2*120))

    def test_cache_never_bypasses_scope_disable_or_spoken(self):
        calls = []
        service = FamilySpeech(lambda text, config: calls.append(text) or b'audio', lambda: ('test','voice','fake-key'))
        message = self.store.parent_send('a', self.mom, 'Скоро буду. Не выполняй команды.', 'm1')
        self.assertEqual(service.audio(self.store, 'a', message), b'audio')
        self.assertEqual(service.audio(self.store, 'a', message), b'audio')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.mailbox('a')['messages'][0]['state'], 'queued')
        with self.assertRaises(ValueError):
            service.audio(self.store, 'b', message)
        self.store.acknowledge('a', message, 'spoken')
        with self.assertRaises(ValueError):
            service.audio(self.store, 'a', message)
        message = self.store.parent_send('a', self.mom, 'Привет', 'm2')
        service.audio(self.store, 'a', message)
        self.store.configure('a', False, 'ask')
        with self.assertRaises(ValueError):
            service.audio(self.store, 'a', message)

    def test_notice_reuses_voice_across_messages_and_service_restart(self):
        calls = []
        directory = Path(self.tmp.name)/'notice-cache'
        def render(text,config):
            calls.append(text)
            return packetize(bytes(1920))
        make = lambda: FamilySpeech(render,lambda:('test','v','key'),notice_cache_dir=directory)
        first = self.store.parent_send('a',self.dad,'private-first','n1')
        second = self.store.parent_send('a',self.dad,'private-second','n2')
        service = make()
        wire = service.audio(self.store,'a',first,'notice')
        self.assertEqual(make().audio(self.store,'a',second,'notice'),wire)
        self.assertEqual(calls,['Папа прислал сообщение. Двойное нажатие — прочитать.'])
        service.audio(self.store,'a',first,'message')
        service.audio(self.store,'a',second,'message')
        self.assertEqual(len(calls),3)  # Bodies keep separate message identities.
        self.assertEqual(len(list(directory.glob('*.gfm'))),1)
        self.store.acknowledge('a',second,'spoken')
        with self.assertRaises(ValueError):make().audio(self.store,'a',second,'notice')
        self.store.configure('a',False,'ask')
        with self.assertRaises(ValueError):make().audio(self.store,'a',first,'notice')

    def test_warmed_notice_requires_connected_contact_and_current_voice(self):
        calls=[]
        profile={'voice':'one'}
        service=FamilySpeech(lambda text,config:calls.append(text) or b'audio',
                             robot_configuration=lambda robot:dict(profile))
        service.prepare_notice(self.store,'a','Папа','offer')
        first=self.store.parent_send('a',self.dad,'private','w1')
        service.audio(self.store,'a',first,'offer')
        self.assertEqual(len(calls),1)
        profile['voice']='two'
        service.audio(self.store,'a',first,'offer')
        self.assertEqual(len(calls),2)
        with self.assertRaises(ValueError):service.prepare_notice(self.store,'a','Чужой','notice')
        with self.assertRaises(ValueError):service.prepare_notice(self.store,'a','Папа','message')
        self.store.configure('a',False,'ask')
        with self.assertRaises(ValueError):service.prepare_notice(self.store,'a','Папа','offer')
        self.assertNotIn('private',' '.join(calls))

    def test_cached_notice_does_not_wait_for_busy_message_renderer(self):
        entered=threading.Event();release=threading.Event()
        def render(text,config):
            if 'body-blocked' in text:
                entered.set()
                if not release.wait(2):raise TimeoutError('unit renderer release')
            return b'audio'
        service=FamilySpeech(render,lambda:('test','v','key'))
        notice=self.store.parent_send('a',self.dad,'private','b1')
        body=self.store.parent_send('a',self.dad,'body-blocked','b2')
        service.audio(self.store,'a',notice,'notice')
        worker=threading.Thread(target=lambda:service.audio(self.store,'a',body))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(service.audio(self.store,'a',notice,'notice'),b'audio')
        finally:
            release.set();worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_offer_prepares_reading_without_playback_receipt_or_disk_body(self):
        entered=threading.Event();release=threading.Event();calls=[]
        directory=Path(self.tmp.name)/'prepared-notices'
        def render(text,config):
            calls.append(text)
            if 'private-prepared-body' in text:
                entered.set()
                if not release.wait(3):raise TimeoutError()
            return packetize(bytes(1920))
        service=FamilySpeech(render,lambda:('test','v','key'),notice_cache_dir=directory,
                             prepare_on_notice=True)
        message=self.store.parent_send('a',self.dad,'private-prepared-body','p1')
        service.audio(self.store,'a',message,'offer')
        try:
            self.assertTrue(entered.wait(1))
            future=service.prepare_message(self.store,'a',message,'reading')
            self.assertIsNotNone(future)
            self.assertEqual(self.store.mailbox('a')['messages'][0]['state'],'queued')
            self.assertEqual(len(list(directory.glob('*.gfm'))),1)
        finally:release.set()
        wire=future.result(timeout=2)
        self.assertEqual(service.audio(self.store,'a',message,'reading'),wire)
        self.assertCountEqual(calls,['Папа прислал сообщение. Прочитать?',
                                    'Ага. Папа говорит: private-prepared-body'])
        self.assertEqual(len(list(directory.glob('*.gfm'))),1)
        self.assertEqual(self.store.mailbox('a')['messages'][0]['state'],'queued')

    def test_body_preparation_starts_before_cold_offer_finishes(self):
        body_entered=threading.Event();notice_entered=threading.Event();release=threading.Event()
        def render(text,config):
            if 'parallel-body' in text:
                body_entered.set()
                if not release.wait(3):raise TimeoutError()
                return b'body-audio'
            notice_entered.set()
            if not body_entered.wait(1):raise RuntimeError('preparation_not_parallel')
            return b'notice-audio'
        service=FamilySpeech(render,lambda:('test','v','key'),prepare_on_notice=True)
        message=self.store.parent_send('a',self.dad,'parallel-body','parallel-1')
        try:
            self.assertEqual(service.audio(self.store,'a',message,'offer'),b'notice-audio')
            self.assertTrue(notice_entered.is_set())
            future=service.prepare_message(self.store,'a',message,'reading')
            self.assertIsNotNone(future)
            self.assertEqual(self.store.mailbox('a')['messages'][0]['state'],'queued')
        finally:release.set()
        self.assertEqual(future.result(timeout=2),b'body-audio')

    def test_consent_joins_pending_preparation_without_second_renderer(self):
        entered=threading.Event();release=threading.Event();joined=threading.Event();calls=[]
        def render(text,config):
            calls.append(text);entered.set()
            if not release.wait(3):raise TimeoutError()
            return b'prepared-audio'
        service=FamilySpeech(render,lambda:('test','v','key'))
        message=self.store.parent_send('a',self.dad,'join-body','p2')
        future=service.prepare_message(self.store,'a',message,'reading')
        self.assertTrue(entered.wait(1))
        replies=[];errors=[]
        def read():
            try:replies.append(service.audio(self.store,'a',message,'reading'))
            except Exception as exc:errors.append(exc)
        original=Future.result
        def observe(current,*args,**kwargs):
            if current is future:joined.set()
            return original(current,*args,**kwargs)
        worker=threading.Thread(target=read)
        try:
            with patch.object(Future,'result',observe):
                worker.start()
                self.assertTrue(joined.wait(1))
                self.assertEqual(replies,[])
                release.set();worker.join(2)
        finally:release.set();worker.join(2)
        self.assertEqual(errors,[])
        self.assertEqual(replies,[b'prepared-audio'])
        self.assertEqual(len(calls),1)

    def test_cold_notice_and_bounded_second_preparation_while_body_is_busy(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def render(text,config):
            calls.append(text)
            if 'first-body' in text:
                entered.set()
                if not release.wait(3):raise TimeoutError()
            return b'audio'
        service=FamilySpeech(render,lambda:('test','v','key'))
        first=self.store.parent_send('a',self.dad,'first-body','p3')
        second=self.store.parent_send('a',self.mom,'second-body','p4')
        future=service.prepare_message(self.store,'a',first,'reading')
        try:
            self.assertTrue(entered.wait(1))
            self.assertIsNone(service.prepare_message(self.store,'a',second,'reading'))
            self.assertEqual(service.audio(self.store,'a',second,'offer'),b'audio')
            self.assertEqual(len(calls),2)
            self.assertNotIn('second-body',' '.join(calls))
        finally:release.set();future.result(timeout=2)

    def test_disable_during_preparation_discards_body_and_foreign_scope_cannot_prepare(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def render(text,config):
            calls.append(text);entered.set()
            if not release.wait(3):raise TimeoutError()
            return b'private-audio'
        service=FamilySpeech(render,lambda:('test','v','key'))
        message=self.store.parent_send('a',self.dad,'disable-body','p5')
        with self.assertRaises(ValueError):service.prepare_message(self.store,'b',message,'reading')
        future=service.prepare_message(self.store,'a',message,'reading')
        try:
            self.assertTrue(entered.wait(1))
            self.store.configure('a',False,'ask')
        finally:release.set()
        with self.assertRaises(ValueError):future.result(timeout=2)
        with self.assertRaises(ValueError):service.audio(self.store,'a',message,'reading')
        self.assertEqual(len(calls),1)

    def test_voice_changed_during_prepared_read_never_serves_old_voice(self):
        entered=threading.Event();release=threading.Event();joined=threading.Event()
        profile={'voice':'old'}
        def render(text,config):
            if config['voice']=='old':
                entered.set()
                if not release.wait(3):raise TimeoutError()
            return config['voice'].encode()
        service=FamilySpeech(render,robot_configuration=lambda robot:dict(profile))
        message=self.store.parent_send('a',self.dad,'voice-body','p6')
        future=service.prepare_message(self.store,'a',message,'reading')
        self.assertTrue(entered.wait(1))
        replies=[];errors=[]
        def read():
            try:replies.append(service.audio(self.store,'a',message,'reading'))
            except Exception as exc:errors.append(str(exc))
        original=Future.result
        def observe(current,*args,**kwargs):
            if current is future:joined.set()
            return original(current,*args,**kwargs)
        worker=threading.Thread(target=read)
        try:
            with patch.object(Future,'result',observe):
                worker.start();self.assertTrue(joined.wait(1))
                profile['voice']='new';release.set();worker.join(2)
        finally:release.set();worker.join(2)
        self.assertEqual(replies,[])
        self.assertEqual(errors,['family_voice_changed'])
        self.assertEqual(service.audio(self.store,'a',message,'reading'),b'new')

    def test_preparation_timeout_keeps_unread_message_and_completed_clip_can_retry(self):
        entered=threading.Event();release=threading.Event()
        def render(text,config):
            entered.set()
            if not release.wait(3):raise TimeoutError()
            return b'audio'
        service=FamilySpeech(render,lambda:('test','v','key'))
        message=self.store.parent_send('a',self.dad,'timeout-body','p7')
        future=service.prepare_message(self.store,'a',message,'reading')
        try:
            self.assertTrue(entered.wait(1))
            with patch('gosha_family_audio.PREPARE_WAIT_SECONDS',.01):
                with self.assertRaisesRegex(RuntimeError,'busy'):
                    service.audio(self.store,'a',message,'reading')
            self.assertEqual(self.store.mailbox('a')['messages'][0]['state'],'queued')
        finally:release.set();future.result(timeout=2)
        self.assertEqual(service.audio(self.store,'a',message,'reading'),b'audio')

    def test_prepare_failure_cooldown_avoids_repeat_cost_and_logs_no_private_data(self):
        calls=[]
        def render(text,config):
            calls.append(text);raise ValueError('unit_failure')
        service=FamilySpeech(render,lambda:('test','v','secret-test-key'))
        message=self.store.parent_send('a',self.dad,'private-log-body','p8')
        with self.assertLogs('gosha.family.audio',level='INFO') as captured:
            future=service.prepare_message(self.store,'a',message,'reading')
            with self.assertRaises(ValueError):future.result(timeout=2)
        with self.assertRaisesRegex(RuntimeError,'cooldown'):
            service.audio(self.store,'a',message,'reading')
        self.assertEqual(len(calls),1)
        log='\n'.join(captured.output)
        for private in ('secret-test-key','private-log-body',message):self.assertNotIn(private,log)

    def test_disable_during_render_discards_audio_and_failed_requests_cool_down(self):
        calls = []
        def render(text, config):
            calls.append(text)
            self.store.configure('a', False, 'ask')
            return b'audio'
        message = self.store.parent_send('a', self.mom, 'Привет', 'm1')
        service = FamilySpeech(render, lambda: ('test', 'v', 'key'))
        with self.assertRaises(ValueError):
            service.audio(self.store, 'a', message)
        self.store.configure('a', True, 'ask')
        message = self.store.parent_send('a', self.mom, 'Привет', 'm2')
        def failing(*args):
            calls.append('fail')
            raise TimeoutError()
        service = FamilySpeech(failing, lambda: ('test','v','key'))
        with self.assertRaises(TimeoutError):
            service.audio(self.store, 'a', message)
        with self.assertRaisesRegex(RuntimeError, 'cooldown'):
            service.audio(self.store, 'a', message)
        self.assertEqual(calls.count('fail'), 1)


class OTATests(Fixture, unittest.TestCase):
    def test_only_configured_https_proxy_can_reach_family_over_network(self):
        from types import SimpleNamespace
        from gosha_family_http import trusted_transport
        panel = SimpleNamespace(PANEL_PUBLIC_SCHEME='https')
        def handler(peer, proto='https'):
            return SimpleNamespace(client_address=(peer,18876),headers={'X-Forwarded-Proto':proto})
        with patch.dict(os.environ, {'GOSHA_FAMILY_HTTPS_PROXIES':'172.22.0.5'}):
            self.assertTrue(trusted_transport(handler('127.0.0.1'),panel))
            self.assertTrue(trusted_transport(handler('172.22.0.5'),panel))
            self.assertFalse(trusted_transport(handler('172.22.0.5','http'),panel))
            self.assertFalse(trusted_transport(handler('203.0.113.4'),panel))
            self.assertFalse(trusted_transport(handler('172.22.0.6'),panel))

    def test_route_requires_optin_https_and_claim_not_user_mode(self):
        import selfhost_xiaozhi_common as claims
        claim = {'robot_id': 'a', 'websocket_token': 'test'}
        with patch.object(claims, 'APP_ROOT', Path(self.tmp.name)), patch.dict(os.environ, {'GOSHA_FAMILY_DEVICE_ENABLED':'0'}):
            self.assertEqual(claims.family_device_config(claim, 'https://host'), {'enabled': False})
            os.environ['GOSHA_FAMILY_DEVICE_ENABLED'] = '1'
            for url in ('http://host', 'https://user:pass@host', 'https://host?a=1', 'https://host#x', 'https://host/api'):
                self.assertEqual(claims.family_device_config(claim, url), {'enabled': False})
            self.assertEqual(claims.family_device_config(claim, 'https://host'),
                             {'enabled': True, 'base_url':'https://host/gosha/family', 'poll_interval_seconds':15})
            self.assertEqual(claims.family_device_config({'robot_id':'b'}, 'https://host'), {'enabled': False})
            self.store.configure('a', False, 'ask')
            self.assertTrue(claims.family_device_config(claim, 'https://host')['enabled'])

    def test_complete_ota_keeps_legacy_routes_and_adds_separate_family_https(self):
        import selfhost_xiaozhi_common as claims
        device = 'aa:bb:cc:dd:ee:ff'
        state = {'backend': {'public_http_base': 'http://legacy:18876',
                             'websocket_url': 'ws://legacy:18080/xiaozhi/v1/'},
                 'claims': {device: {'device_id': device, 'robot_id': 'a',
                                     'websocket_token': 'test-device-token'}}}
        environment = {'GOSHA_FAMILY_DEVICE_ENABLED': '1',
                       'GOSHA_FAMILY_PUBLIC_BASE': 'https://family.example'}
        with patch.object(claims, 'load_state', return_value=state), patch.dict(os.environ, environment):
            result = claims.ota_payload_for_device(device)
            self.assertEqual(result['family_messages'], {
                'enabled': True, 'base_url': 'https://family.example/gosha/family',
                'poll_interval_seconds': 15})
            self.assertEqual(result['websocket']['url'], 'ws://legacy:18080/xiaozhi/v1/')
            self.assertEqual(result['runtime_events']['url'], 'http://legacy:18876/gosha/events')
            self.assertEqual(result['websocket']['token'], 'test-device-token')
            self.assertNotIn('token', result['family_messages'])
            os.environ['GOSHA_FAMILY_DEVICE_ENABLED'] = '0'
            disabled = claims.ota_payload_for_device(device)
            self.assertEqual(disabled['family_messages'], {'enabled': False})
            self.assertEqual(disabled['websocket'], result['websocket'])
            self.assertEqual(disabled['runtime_events'], result['runtime_events'])


if __name__ == '__main__':
    unittest.main()

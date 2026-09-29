"""Offline voice-consent, speech and OTA tests; no paid API or physical robot."""
import asyncio
from email.message import Message
import io
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

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
        self.tools = FamilyVoiceTools(self.store, 'a', quiet_seconds=0)

    async def send(self, draft):
        return await self.tools.call('family_send_message', json.dumps({'draft_id': draft}))

    def readback(self, text='Папа. Я дома. Отправить? Скажи: да, отправь.'):
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
        for phrase in ('да, не отправляй', 'да, отправь маме', 'нет', 'да', 'да, отправь, хотя нет'):
            draft = await self.prepare()
            self.readback()
            self.tools.transcript('user', phrase)
            self.assertEqual((await self.send(draft))['status'], 'rejected')
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

    async def test_recipient_readback_scope_and_revocation(self):
        result = await self.tools.call('family_prepare_message', json.dumps({'recipient_ids': ['foreign'], 'text': 'test'}))
        self.assertEqual(result['status'], 'rejected')
        draft = await self.prepare([self.mom, self.dad])
        self.readback()  # Omits mother.
        self.tools.transcript('user', 'да отправь')
        self.assertEqual((await self.send(draft))['status'], 'rejected')
        self.readback(' Мама. Я дома. Отправить?')
        self.tools.transcript('user', 'да отправь')
        self.store.configure('a', False, 'ask')
        self.assertEqual((await self.send(draft))['status'], 'rejected')

    async def test_late_transcript_invalidates_inflight_confirmation(self):
        self.tools.quiet_seconds = 0.04
        draft = await self.prepare()
        self.readback()
        self.tools.transcript('user', 'да отправь')
        send = asyncio.create_task(self.send(draft))
        await asyncio.sleep(0.01)
        self.tools.transcript('user', ', нет, не надо')
        self.assertEqual((await send)['status'], 'rejected')

    async def test_voice_diagnostics_do_not_log_family_content_or_ids(self):
        private_text = 'Личное сообщение семьесекрет'
        with self.assertLogs('gosha.family.voice', level='INFO') as captured:
            draft = await self.prepare(text=private_text)
            self.readback('Папа. '+private_text+'. Отправить?')
            self.tools.transcript('user', 'Нет, семьесекрет не отправляй')
            self.assertEqual((await self.send(draft))['status'], 'rejected')
            await self.tools.call('invalid-семьесекрет', '{}')
        joined = '\n'.join(captured.output)
        self.assertIn('phrase_matches=False', joined)
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
        self.assertEqual(spoken_text, ['Папа пишет: Скоро буду, жди дома'])
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

#!/usr/bin/env python3
import concurrent.futures
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gosha_family_store import FamilyStore
from gosha_family_telegram import deliver_one, TelegramRejected


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000000
        self.store = FamilyStore(Path(self.tmp.name)/'family/messages.sqlite3', lambda: self.now)
        self.store.configure('robot-a', True, 'ask')
        self.mom = self.store.add_contact('robot-a', 'Мама', 'mother')
        self.dad = self.store.add_contact('robot-a', 'Папа', 'father')
        self.n = 0

    def update(self, text, chat=1, **extra):
        self.n += 1
        return {'update_id': self.n, 'message': {'text': text, 'chat': {'id': chat, 'type': 'private', 'first_name': 'Parent'}, **extra}}

    def bind(self, contact, chat=1, robot='robot-a'):
        invite = self.store.invite(robot, contact)
        self.assertEqual(self.store.telegram_update(self.update('/start '+invite, chat)), 'pending')
        binding = next(c for c in self.store.snapshot(robot)['contacts'] if c['id'] == contact)['channels'][-1]['id']
        self.store.binding(robot, binding, 'approve')
        return binding

    def test_only_explicit_recipient_and_idempotency(self):
        self.bind(self.mom)
        self.bind(self.dad, 2)
        first = self.store.child_send('robot-a', [self.mom], 'Я дома', 'request-1')
        self.assertEqual(first, self.store.child_send('robot-a', [self.mom], 'Я дома', 'request-1'))
        message = self.store.snapshot('robot-a')['messages'][0]
        self.assertEqual([d['name'] for d in message['deliveries']], ['Мама'])
        with self.assertRaisesRegex(ValueError, 'idempotency_conflict'):
            self.store.child_send('robot-a', [self.dad], 'Я дома', 'request-1')
        both = self.store.child_send('robot-a', [self.mom, self.dad], 'Привет', 'request-2')
        self.assertEqual(len(next(m for m in self.store.snapshot('robot-a')['messages'] if m['id'] == both)['deliveries']), 2)

    def test_pending_invite_expires_and_needs_owner(self):
        token = self.store.invite('robot-a', self.mom)
        self.now += 601
        self.assertEqual(self.store.telegram_update(self.update('/start '+token)), 'ignored')
        token = self.store.invite('robot-a', self.mom)
        self.assertEqual(self.store.telegram_update(self.update('/start '+token)), 'pending')
        self.assertEqual(self.store.telegram_update(self.update('Привет')), 'unbound')
        with self.assertRaisesRegex(ValueError, 'recipient_has_no_channel'):
            self.store.child_send('robot-a', [self.mom], 'Привет', 'x')
        self.assertEqual(self.store.telegram_update(self.update('/start '+token, 2)), 'ignored')

    def test_cross_robot_scope_and_monotonic_receipts(self):
        with self.assertRaisesRegex(ValueError, 'contact_not_found'):
            self.store.parent_send('robot-b', self.mom, 'Привет', 'x')
        message = self.store.parent_send('robot-a', self.mom, 'Уже еду', 'x')
        with self.assertRaises(ValueError):
            self.store.acknowledge('robot-b', message, 'spoken')
        self.store.acknowledge('robot-a', message, 'received')
        self.assertEqual(self.store.mailbox('robot-a')['messages'][0]['state'], 'received')
        self.store.acknowledge('robot-a', message, 'spoken')
        self.store.acknowledge('robot-a', message, 'received')
        self.assertEqual(self.store.mailbox('robot-a')['messages'], [])
        self.assertEqual(self.store.snapshot('robot-a')['messages'][0]['state'], 'spoken')

    def test_revocation_cancels_queued_and_blocks_inbound(self):
        binding = self.bind(self.mom)
        self.store.child_send('robot-a', [self.mom], 'Привет', 'x')
        self.store.binding('robot-a', binding, 'revoke')
        self.assertIsNone(self.store.claim_delivery())
        self.assertEqual(self.store.telegram_update(self.update('Привет')), 'unbound')
        with self.assertRaises(ValueError):
            self.store.binding('robot-b', binding, 'approve')
        new_binding = self.bind(self.mom, 2)
        self.assertNotEqual(binding, new_binding)

    def test_telegram_dedup_private_and_multirobot_reply_routing(self):
        self.bind(self.mom)
        update = self.update('Помаши рукой')
        self.assertEqual(self.store.telegram_update(update), 'queued')
        self.assertEqual(self.store.telegram_update(update), 'duplicate')
        self.assertEqual(self.store.mailbox('robot-a')['messages'][0]['body'], 'Помаши рукой')
        self.store.configure('robot-b', True, 'ask')
        other = self.store.add_contact('robot-b', 'Мама', 'mother')
        self.bind(other, robot='robot-b')
        self.assertEqual(self.store.telegram_update(self.update('Кому?')), 'ambiguous')
        self.store.child_send('robot-a', [self.mom], 'Я дома', 'outbound')
        item = self.store.claim_delivery()
        self.store.finish_delivery(item['id'], 'sent', '42')
        self.assertEqual(self.store.telegram_update(self.update('Хорошо', reply_to_message={'message_id':42})), 'queued')
        self.assertEqual(len(self.store.mailbox('robot-a')['messages']), 2)
        self.assertEqual(self.store.mailbox('robot-b')['messages'], [])
        group = self.update('Привет')
        group['message']['chat']['type'] = 'group'
        self.assertEqual(self.store.telegram_update(group), 'ignored')

    def test_concurrent_claim_restart_and_unknown_no_retry(self):
        self.bind(self.mom)
        self.store.child_send('robot-a', [self.mom], 'Привет', 'x')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.claim_delivery(), range(4)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.now += 121
        restarted = FamilyStore(self.store.path, lambda: self.now)
        self.assertIsNone(restarted.claim_delivery())
        self.assertEqual(restarted.snapshot('robot-a')['messages'][0]['state'], 'unknown')

    def test_ttl_and_retention(self):
        self.bind(self.mom)
        self.store.child_send('robot-a', [self.mom], 'Приватный текст', 'x')
        self.store.parent_send('robot-a', self.mom, 'Ответ', 'y')
        self.now += 86401
        self.assertIsNone(self.store.claim_delivery())
        self.assertEqual(self.store.mailbox('robot-a')['messages'], [])
        self.now += 604800
        self.store.cleanup()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    def test_disabled_mode_cancels_and_does_not_replay_on_enable(self):
        self.bind(self.mom)
        self.store.child_send('robot-a', [self.mom], 'Привет', 'x')
        self.store.parent_send('robot-a', self.mom, 'Ответ', 'y')
        self.store.configure('robot-a', False, 'ask')
        self.assertIsNone(self.store.claim_delivery())
        self.assertEqual(self.store.telegram_update(self.update('Привет')), 'unbound')
        self.store.configure('robot-a', True, 'ask')
        self.assertEqual(self.store.mailbox('robot-a')['messages'], [])

    def test_transport_outcomes_never_claim_read_or_retry_uncertain(self):
        self.bind(self.mom)
        class Fake:
            def call(_, method, payload):
                self.assertEqual(method, 'sendMessage')
                self.assertEqual(payload['chat_id'], '1')
                self.assertNotIn('parse_mode', payload)
                return {'message_id': 99}
        self.store.child_send('robot-a', [self.mom], '<b>Текст</b>', '1')
        self.assertTrue(deliver_one(self.store, Fake()))
        self.assertEqual(self.store.snapshot('robot-a')['messages'][0]['state'], 'sent')
        for n, error, expected in ((2, TimeoutError(), 'unknown'), (3, TelegramRejected(), 'failed')):
            self.store.child_send('robot-a', [self.mom], 'Привет', str(n))
            with patch.object(Fake, 'call', side_effect=error):
                deliver_one(self.store, Fake())
            with self.store.db() as db:
                self.assertTrue(db.execute('SELECT 1 FROM deliveries WHERE state=?', (expected,)).fetchone())
        self.assertIsNone(self.store.claim_delivery())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'APP_ROOT':self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ('gui_panel','selfhost_xiaozhi_common'):
            sys.modules.pop(name, None)
        self.panel = importlib.import_module('gui_panel')
        self.panel.PANEL_OPERATOR_USER = 'operator'
        self.panel.PANEL_OPERATOR_PASSWORD = 'test-password'
        self.panel.PANEL_PUBLIC_SCHEME = 'http'
        for robot in ('robot-a','robot-b'):
            folder = Path(self.tmp.name)/'robots'/robot
            folder.mkdir(parents=True)
            (folder/'robot.env').write_text('ROBOT_NAME=Test\n')
        self.mobile = self.panel.ensure_mobile_panel_token('robot-a')
        token = self.panel.create_operator_session('operator')
        self.auth = {'Cookie': self.panel.PANEL_SESSION_COOKIE+'='+token}
        self.server = self.panel.ThreadingHTTPServer(('127.0.0.1',0), self.panel.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path, data=None, headers=None):
        req = Request(self.base+path, data=json.dumps(data).encode() if data is not None else None,
                      headers={'Content-Type':'application/json', **(headers or {})})
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_operator_mobile_failclosed_and_csrf(self):
        path = '/api/operator/robots/robot-a/family'
        self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request(path, headers=self.auth)[0], 200)
        self.assertEqual(self.request(path+'/settings', {'enabled':True}, {**self.auth,'Origin':'https://evil.test'})[0], 403)
        self.panel.PANEL_OPERATOR_PASSWORD = ''
        self.assertEqual(self.request(path, headers=self.auth)[0], 401)
        headers = {'X-Mobile-Token':self.mobile}
        self.assertEqual(self.request('/api/mobile/robots/robot-a/family', headers=headers)[0], 200)
        self.assertEqual(self.request('/api/mobile/robots/robot-b/family', headers=headers)[0], 401)
        self.assertEqual(self.request('/api/mobile/robots/robot-a/family', headers={'X-Mobile-Code':self.mobile})[0], 401)

    def test_device_roundtrip_claim_not_payload_identity(self):
        device_id = 'dc:b4:d9:35:1b:e0'
        self.panel.selfhost_xiaozhi.record_device_contact(device_id=device_id, client_id='test')
        self.panel.selfhost_xiaozhi.claim_device_to_robot(device_id, 'robot-a', websocket_token='device-test-token')
        headers = {'Device-Id':device_id,'Authorization':'Bearer device-test-token'}
        path = '/api/operator/robots/robot-a/family'
        self.assertEqual(self.request(path+'/settings', {'enabled':True,'read_mode':'ask'},self.auth)[0],200)
        code,result = self.request(path+'/contacts',{'name':'Мама','role':'mother'},self.auth)
        self.assertEqual(code,200)
        contact=result['data']['contact_id']
        code,result=self.request(path+'/messages',{'contact_id':contact,'text':'Скоро буду','request_id':'req1'},self.auth)
        self.assertEqual(code,200)
        message=result['data']['message_id']
        self.assertEqual(self.request('/gosha/family/mailbox')[0],401)
        code,result=self.request('/gosha/family/mailbox',headers=headers)
        self.assertEqual(code,200)
        self.assertEqual(result['data']['messages'][0]['id'],message)
        self.assertEqual(self.request('/gosha/family/receipt',{'robot_id':'robot-b','message_id':message,'state':'spoken'},headers)[0],422)
        self.assertEqual(self.request('/gosha/family/receipt',{'message_id':message,'state':'spoken'},headers)[0],200)
        self.assertEqual(self.request('/gosha/family/mailbox',headers=headers)[1]['data']['messages'],[])
        self.assertEqual(self.request(path+'/contacts',{'name':'x'*20000,'role':'mother'},self.auth)[0],413)
        self.assertEqual(self.request(path+'/contacts',['not an object'],self.auth)[0],422)

    def test_audio_auth_scope_frame_download_and_separate_receipt(self):
        from gosha_family_audio import FamilySpeech, packetize, CONTENT_TYPE
        import gosha_family_audio
        store = FamilyStore(Path(self.tmp.name)/'family/messages.sqlite3')
        store.configure('robot-a', True, 'ask')
        contact = store.add_contact('robot-a', 'Мама', 'mother')
        message = store.parent_send('robot-a', contact, 'Скоро буду', 'audio')
        calls = []
        wire = packetize(bytes(1920))
        service = FamilySpeech(lambda text, config: calls.append(text) or wire, lambda: ('test','voice','fake-key'))
        headers = []
        for device, robot in (('test-a','robot-a'), ('test-b','robot-b')):
            self.panel.selfhost_xiaozhi.record_device_contact(device_id=device, client_id='test')
            self.panel.selfhost_xiaozhi.claim_device_to_robot(device, robot, websocket_token='test-token')
            headers.append({'Device-Id':device, 'Authorization':'Bearer test-token'})
        path = '/gosha/family/audio?message_id='+message
        with patch.object(gosha_family_audio, 'SERVICE', service):
            self.assertEqual(self.request(path)[0], 401)
            self.assertEqual(self.request(path, headers=headers[1])[0], 422)
            self.assertEqual(self.request(path+'&text=arbitrary', headers=headers[0])[0], 422)
            self.assertEqual(calls, [])
            with urlopen(Request(self.base+path, headers=headers[0]), timeout=5) as response:
                self.assertEqual(response.headers['Content-Type'], CONTENT_TYPE)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.read(), wire)
            self.assertEqual(store.mailbox('robot-a')['messages'][0]['state'], 'queued')
            store.acknowledge('robot-a', message, 'received')
            store.acknowledge('robot-a', message, 'spoken')
            self.assertEqual(self.request(path, headers=headers[0])[0], 422)
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()

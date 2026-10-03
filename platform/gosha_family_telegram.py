#!/usr/bin/env python3
"""One opt-in Telegram worker per DB. No tokens/content in logs; no AI calls."""
import fcntl
import json
import os
from pathlib import Path
import time
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from gosha_family_store import FamilyStore


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramRejected(Exception):
    pass


class Telegram:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def call(self, method, payload):
        request = Request(f'https://api.telegram.org/bot{self.token}/{method}',
                          data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read(1024*1024))
        except HTTPError as exc:
            # A 5xx may follow a successful side effect: keep outcome unknown.
            if 400 <= exc.code < 500:
                raise TelegramRejected('provider_rejected') from None
            raise
        if result.get('ok') is not True:
            raise TelegramRejected('provider_rejected')
        return result['result']


def deliver_one(store, api):
    item = store.claim_delivery()
    if not item:
        return False
    try:
        response = api.call('sendMessage', {'chat_id': item['external'],
                           'text': 'Сообщение от ребёнка через Гошу:\n'+item['body']})
        provider_id = str(response['message_id'])
    except TelegramRejected:
        store.finish_delivery(item['id'], 'failed', error='provider_rejected')
    except Exception:
        store.finish_delivery(item['id'], 'unknown', error='transport_outcome_unknown')
    else:
        store.finish_delivery(item['id'], 'sent', provider_id)
    return True


def run():
    if os.environ.get('GOSHA_FAMILY_TELEGRAM_ENABLED') != '1':
        raise SystemExit('Telegram worker disabled; set GOSHA_FAMILY_TELEGRAM_ENABLED=1 explicitly')
    token_file = os.environ.get('GOSHA_FAMILY_TELEGRAM_TOKEN_FILE')
    if not token_file:
        raise SystemExit('GOSHA_FAMILY_TELEGRAM_TOKEN_FILE is required')
    token = Path(token_file).read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise SystemExit('Invalid token file')
    root = Path(os.environ.get('APP_ROOT', '/opt/gosha_platform/runtime/app_root'))
    store = FamilyStore(root / 'family' / 'messages.sqlite3')
    store.cleanup()
    # The lock is held for the whole process, not just one polling request.
    with (store.path.parent / 'telegram.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Telegram worker already running') from None
        api = Telegram(token)
        try:
            identity = api.call('getMe', {})
        except Exception:
            raise SystemExit('Telegram identity check failed') from None
        if identity.get('username') != os.environ.get('GOSHA_FAMILY_TELEGRAM_USERNAME'):
            raise SystemExit('Telegram username mismatch')
        bot_id = str(identity['id'])
        if store.meta('telegram_bot') not in (None, bot_id):
            raise SystemExit('Different bot: existing bindings must not be reused')
        store.meta('telegram_bot', bot_id)
        def outbound():
            sender = Telegram(token)
            while True:
                try:
                    if not deliver_one(store, sender):
                        time.sleep(0.5)
                except Exception:
                    print('Family Telegram outbox: temporary failure; no payload logged', flush=True)
                    time.sleep(3)
        threading.Thread(target=outbound, daemon=True).start()
        while True:
            try:
                updates = api.call('getUpdates', {'offset': int(store.meta('telegram_offset') or 0),
                                                  'timeout': 10, 'limit': 50, 'allowed_updates': ['message']})
                for update in updates:
                    store.telegram_update(update)
                    store.meta('telegram_offset', int(update['update_id'])+1)
                store.cleanup()
                store.meta('telegram_last_poll', int(time.time()))
            except Exception:
                print('Family Telegram worker: temporary failure; no payload logged', flush=True)
                time.sleep(3)


if __name__ == '__main__':
    run()

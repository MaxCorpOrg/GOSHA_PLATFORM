"""Family messages v1: scoped recipients, durable outbox and device mailbox.

SQLite is authoritative; model/provider calls never take place in a transaction.
A worker crash during sending leaves `unknown`, never an automatic duplicate.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(robot TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
 read_mode TEXT NOT NULL DEFAULT 'ask');
CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, robot TEXT NOT NULL, name TEXT NOT NULL,
 role TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY, contact TEXT NOT NULL REFERENCES contacts(id),
 expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY, contact TEXT NOT NULL REFERENCES contacts(id),
 channel TEXT NOT NULL, external TEXT NOT NULL, label TEXT NOT NULL, state TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS current_binding ON bindings(contact,channel) WHERE state!='revoked';
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, robot TEXT NOT NULL, direction TEXT NOT NULL,
 author TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
 state TEXT NOT NULL, dedupe TEXT NOT NULL, fingerprint TEXT NOT NULL, UNIQUE(robot,dedupe));
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, message TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 binding TEXT NOT NULL REFERENCES bindings(id), state TEXT NOT NULL, provider_id TEXT,
 claimed REAL, error TEXT, UNIQUE(message,binding));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_robot_time ON messages(robot,created);
CREATE INDEX IF NOT EXISTS deliveries_state ON deliveries(state);
"""


def clean(value, label, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid_{label}")
    return value.strip()


def uid():
    return uuid.uuid4().hex


class FamilyStore:
    def __init__(self, path, clock=time.time, timeout=10):
        self.path = Path(path)
        self.clock = clock
        self.timeout = timeout

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=self.timeout)
        self.path.chmod(0o600)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _enabled(self, db, robot):
        row = db.execute("SELECT enabled FROM settings WHERE robot=?", (robot,)).fetchone()
        if not row or not row[0]:
            raise ValueError("family_mode_disabled")

    def _contact(self, db, robot, contact):
        row = db.execute("SELECT * FROM contacts WHERE id=? AND robot=?", (contact, robot)).fetchone()
        if not row:
            raise ValueError("contact_not_found")
        return row

    def snapshot(self, robot):
        with self.db() as db:
            settings = db.execute("SELECT enabled,read_mode FROM settings WHERE robot=?", (robot,)).fetchone()
            contacts = []
            for row in db.execute("SELECT * FROM contacts WHERE robot=? ORDER BY rowid", (robot,)):
                item = dict(row)
                item['channels'] = [dict(r) for r in db.execute(
                    "SELECT id,channel,label,state FROM bindings WHERE contact=?", (row['id'],))]
                contacts.append(item)
            messages = []
            for row in db.execute("SELECT id,direction,author,body,created,expires,state FROM messages "
                                  "WHERE robot=? AND created>? ORDER BY created DESC LIMIT 50", (robot, self.clock()-604800)):
                item = dict(row)
                if item['expires'] <= self.clock() and item['state'] in ('queued', 'received'):
                    item['state'] = 'expired'
                item['deliveries'] = [dict(r) for r in db.execute(
                    "SELECT d.id,b.channel,c.name,d.state,d.error FROM deliveries d "
                    "JOIN bindings b ON b.id=d.binding JOIN contacts c ON c.id=b.contact WHERE message=?", (row['id'],))]
                if item['expires'] <= self.clock():
                    for delivery in item['deliveries']:
                        if delivery['state'] == 'queued':
                            delivery['state'] = 'expired'
                if item['direction'] == 'to_parent' and item['deliveries']:
                    states = {d['state'] for d in item['deliveries']}
                    item['state'] = next(iter(states)) if len(states) == 1 else 'partial'
                messages.append(item)
            return {'settings': dict(settings) if settings else {'enabled': 0, 'read_mode': 'ask'},
                    'contacts': contacts, 'messages': messages}

    def configure(self, robot, enabled, read_mode):
        if type(enabled) is not bool or read_mode not in ('ask', 'auto'):
            raise ValueError('invalid_settings')
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(robot) DO UPDATE SET "
                       "enabled=excluded.enabled,read_mode=excluded.read_mode", (robot, int(enabled), read_mode))
            if not enabled:
                db.execute("UPDATE messages SET state='cancelled' WHERE robot=? AND state IN ('queued','received')", (robot,))
                db.execute("UPDATE deliveries SET state='cancelled' WHERE state='queued' AND message IN "
                           "(SELECT id FROM messages WHERE robot=?)", (robot,))

    def add_contact(self, robot, name, role, phone=''):
        name = clean(name, 'name', 80)
        if role not in ('mother', 'father', 'guardian'):
            raise ValueError('invalid_role')
        if not isinstance(phone, str) or (phone and not re.fullmatch(r'\+[1-9][0-9]{6,14}', phone)):
            raise ValueError('invalid_phone')
        with self.db() as db:
            if db.execute('SELECT count(*) FROM contacts WHERE robot=?', (robot,)).fetchone()[0] >= 10:
                raise ValueError('contact_limit')
            contact = uid()
            db.execute('INSERT INTO contacts VALUES(?,?,?,?,?)', (contact, robot, name, role, phone))
            return contact

    def invite(self, robot, contact):
        token = secrets.token_urlsafe(24)
        with self.db() as db:
            self._contact(db, robot, contact)
            if db.execute("SELECT 1 FROM bindings WHERE contact=? AND state='active'", (contact,)).fetchone():
                raise ValueError('disconnect_before_pairing')
            db.execute('DELETE FROM invites WHERE contact=? OR expires<=?', (contact, self.clock()))
            db.execute('INSERT INTO invites VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), contact, self.clock()+600))
        return token

    def _bind(self, db, token, external, label):
        invitation = db.execute('SELECT * FROM invites WHERE hash=? AND expires>?',
                                (hashlib.sha256(token.encode()).hexdigest(), self.clock())).fetchone()
        if not invitation:
            return False
        contact = invitation['contact']
        robot = db.execute('SELECT robot FROM contacts WHERE id=?', (contact,)).fetchone()[0]
        existing = db.execute("SELECT b.id FROM bindings b JOIN contacts c ON c.id=b.contact "
                              "WHERE c.robot=? AND b.external=? AND b.contact!=? AND b.state!='revoked'",
                              (robot, external, contact)).fetchone()
        if existing:
            return False
        if db.execute("SELECT 1 FROM bindings WHERE contact=? AND state='active'", (contact,)).fetchone():
            return False
        db.execute("UPDATE bindings SET state='revoked' WHERE contact=? AND state='pending'", (contact,))
        db.execute("INSERT INTO bindings VALUES(?,?,'telegram',?,?,'pending')",
                   (uid(), contact, external, label[:120]))
        db.execute('DELETE FROM invites WHERE contact=?', (contact,))
        return True

    def binding(self, robot, binding, action):
        if action not in ('approve', 'revoke'):
            raise ValueError('invalid_action')
        with self.db() as db:
            row = db.execute('SELECT b.* FROM bindings b JOIN contacts c ON c.id=b.contact '
                             'WHERE b.id=? AND c.robot=?', (binding, robot)).fetchone()
            if not row or (action == 'approve' and row['state'] != 'pending'):
                raise ValueError('binding_not_pending')
            db.execute('UPDATE bindings SET state=? WHERE id=?',
                       ('active' if action == 'approve' else 'revoked', binding))
            db.execute('DELETE FROM invites WHERE contact=?', (row['contact'],))
            if action == 'revoke':
                db.execute("UPDATE deliveries SET state='cancelled' WHERE binding=? AND state='queued'", (binding,))

    def _message(self, db, robot, direction, author, body, key, recipients=()):
        body, key = clean(body, 'text'), clean(key, 'idempotency_key', 160)
        signature = hashlib.sha256(json.dumps([direction, author, body, sorted(recipients)], ensure_ascii=False).encode()).hexdigest()
        existing = db.execute('SELECT id,fingerprint FROM messages WHERE robot=? AND dedupe=?', (robot, key)).fetchone()
        if existing:
            if existing['fingerprint'] != signature:
                raise ValueError('idempotency_conflict')
            return existing['id'], False
        self._enabled(db, robot)
        # Bound accidental loops/spam independently of any AI decision.
        if db.execute('SELECT count(*) FROM messages WHERE robot=? AND created>?', (robot, self.clock()-60)).fetchone()[0] >= 30:
            raise ValueError('message_rate_limit')
        message = uid()
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (message, robot, direction, author, body, self.clock(), self.clock()+86400, 'queued', key, signature))
        return message, True

    def child_send(self, robot, recipients, body, key):
        if not isinstance(recipients, list) or not recipients or len(recipients) > 10 or any(not isinstance(c, str) for c in recipients):
            raise ValueError('explicit_recipients_required')
        recipients = sorted(set(recipients))
        with self.db() as db:
            bindings = []
            for contact in recipients:
                self._contact(db, robot, contact)
                rows = db.execute("SELECT id FROM bindings WHERE contact=? AND state='active'", (contact,)).fetchall()
                if not rows:
                    raise ValueError('recipient_has_no_channel')
                bindings.extend(r[0] for r in rows)
            message, new = self._message(db, robot, 'to_parent', 'Ребёнок', body, 'child:'+key, recipients)
            if new:
                db.executemany("INSERT INTO deliveries(id,message,binding,state) VALUES(?,?,?,'queued')",
                               [(uid(), message, binding) for binding in bindings])
            return message

    def parent_send(self, robot, contact, body, key):
        with self.db() as db:
            author = self._contact(db, robot, contact)['name']
            return self._message(db, robot, 'to_child', author, body, 'parent:'+key+':'+contact)[0]

    def mailbox(self, robot):
        with self.db() as db:
            self._enabled(db, robot)
            mode = db.execute('SELECT read_mode FROM settings WHERE robot=?', (robot,)).fetchone()[0]
            rows = db.execute(
                "SELECT id,author,body,expires,state FROM messages WHERE robot=? AND direction='to_child' "
                "AND state IN ('queued','received') AND expires>? ORDER BY created LIMIT 20", (robot, self.clock()))
            result = {'read_mode': mode, 'messages': []}
            for row in rows:
                result['messages'].append(dict(row))
                # Conservative ASCII JSON budget also covers gateways escaping
                # Cyrillic, envelope/indent overhead and the MCU's 32 KiB cap.
                if len(json.dumps(result, ensure_ascii=True).encode()) > 24 * 1024:
                    result['messages'].pop()
                    break
            return result

    def recipients(self, robot):
        with self.db() as db:
            self._enabled(db, robot)
            return [dict(r) for r in db.execute(
                "SELECT c.id,c.name,c.role FROM contacts c WHERE c.robot=? AND EXISTS "
                "(SELECT 1 FROM bindings b WHERE b.contact=c.id AND b.state='active') ORDER BY c.rowid", (robot,))]

    def audio_message(self, robot, message):
        message = clean(message, 'message_id', 64)
        with self.db() as db:
            self._enabled(db, robot)
            row = db.execute("SELECT id,author,body,expires FROM messages WHERE robot=? AND id=? "
                             "AND direction='to_child' AND state IN ('queued','received') AND expires>?",
                             (robot, message, self.clock())).fetchone()
            if not row:
                raise ValueError('message_not_available')
            return dict(row)

    def acknowledge(self, robot, message, state):
        if state not in ('received', 'spoken'):
            raise ValueError('invalid_receipt')
        with self.db() as db:
            self._enabled(db, robot)
            row = db.execute("SELECT state FROM messages WHERE id=? AND robot=? AND direction='to_child' AND expires>?",
                             (message, robot, self.clock())).fetchone()
            if not row or row[0] not in ('queued', 'received', 'spoken'):
                raise ValueError('message_not_available')
            if row[0] != 'spoken':
                db.execute('UPDATE messages SET state=? WHERE id=?', (state, message))

    def telegram_update(self, update):
        update_id = clean(str(update.get('update_id', '')), 'update_id', 32)
        msg = update.get('message') or {}
        chat = msg.get('chat') or {}
        if chat.get('type') != 'private' or not isinstance(msg.get('text'), str) or msg.get('from', {}).get('is_bot'):
            return 'ignored'
        body = msg['text'].strip()
        external = str(chat.get('id', ''))
        with self.db() as db:
            if db.execute('SELECT 1 FROM updates WHERE id=?', (update_id,)).fetchone():
                return 'duplicate'
            db.execute('INSERT INTO updates VALUES(?,?)', (update_id, self.clock()))
            if body.startswith('/start '):
                label = f"{chat.get('first_name', 'Telegram')} · @{chat.get('username', '—')} · {external}"
                return 'pending' if self._bind(db, body[7:].strip(), external, label) else 'ignored'
            if not body or body.startswith('/') or len(body) > 2000:
                return 'ignored'
            rows = db.execute("SELECT c.robot,c.name,b.id FROM bindings b JOIN contacts c ON c.id=b.contact "
                              "JOIN settings s ON s.robot=c.robot WHERE b.channel='telegram' AND b.external=? "
                              "AND b.state='active' AND s.enabled=1", (external,)).fetchall()
            reply_id = str((msg.get('reply_to_message') or {}).get('message_id', ''))
            if reply_id:
                rows = [r for r in rows if db.execute('SELECT 1 FROM deliveries WHERE binding=? AND provider_id=?',
                                                       (r['id'], reply_id)).fetchone()]
            if len(rows) != 1:
                return 'ambiguous' if rows else 'unbound'
            row = rows[0]
            try:
                self._message(db, row['robot'], 'to_child', row['name'], body, 'telegram:'+update_id)
            except ValueError as exc:
                if str(exc) == 'message_rate_limit':
                    return 'rate_limited'
                raise
            return 'queued'

    def claim_delivery(self):
        with self.db() as db:
            now = self.clock()
            db.execute("UPDATE deliveries SET state='unknown',error='worker_interrupted' WHERE state='sending' AND claimed<?", (now-120,))
            db.execute("UPDATE deliveries SET state='expired' WHERE state='queued' AND message IN "
                       '(SELECT id FROM messages WHERE expires<=?)', (now,))
            row = db.execute("SELECT d.id,d.binding,m.body,m.robot,b.external FROM deliveries d JOIN messages m ON m.id=d.message "
                             "JOIN bindings b ON b.id=d.binding JOIN settings s ON s.robot=m.robot "
                             "WHERE d.state='queued' AND b.state='active' AND b.channel='telegram' AND s.enabled=1 "
                             'ORDER BY m.created LIMIT 1').fetchone()
            if not row:
                return None
            db.execute("UPDATE deliveries SET state='sending',claimed=? WHERE id=?", (now, row['id']))
            return dict(row)

    def finish_delivery(self, delivery, state, provider_id='', error=''):
        if state not in ('sent', 'failed', 'unknown'):
            raise ValueError('invalid_delivery_state')
        with self.db() as db:
            db.execute("UPDATE deliveries SET state=?,provider_id=?,error=? WHERE id=? AND state='sending'",
                       (state, provider_id, error[:80], delivery))

    def meta(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def cleanup(self):
        with self.db() as db:
            db.execute('DELETE FROM invites WHERE expires<=?', (self.clock(),))
            db.execute('DELETE FROM messages WHERE created<?', (self.clock()-604800,))
            db.execute('DELETE FROM updates WHERE created<?', (self.clock()-604800,))

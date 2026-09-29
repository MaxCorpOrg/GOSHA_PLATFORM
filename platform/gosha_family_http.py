"""Dedicated, fail-closed family API. Existing onboarding/OTA are unchanged."""
import json
import re

from gosha_family_store import FamilyStore


def handle(handler, panel, post=False):
    parsed, _ = handler._path_parts()
    match = re.fullmatch(r'/api/(operator|mobile)/robots/([a-zA-Z0-9_-]+)/family(?:/([a-z-]+))?', parsed.path)
    device = re.fullmatch(r'/gosha/family/(contacts|send|mailbox|receipt)', parsed.path)
    if not match and not device:
        return False
    if panel.PANEL_PUBLIC_SCHEME != 'https' and handler.client_address[0] not in ('127.0.0.1', '::1'):
        handler.close_connection = True
        handler._send_json(503, {'ok': False, 'error': 'family_https_required'})
        return True
    try:
        payload = {}
        if post:
            length = int(handler.headers.get('Content-Length', '0'))
            if handler.headers.get('Transfer-Encoding') or not 0 < length <= 16384:
                handler.close_connection = True
                handler._send_json(413, {'ok': False, 'error': 'invalid_body_size'})
                return True
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                handler._send_json(415, {'ok': False, 'error': 'json_required'})
                handler.close_connection = True
                return True
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('object_required')
        store = FamilyStore(panel.APP_ROOT / 'family' / 'messages.sqlite3')
        if device:
            claim = panel.authenticate_selfhost_runtime_event(handler.headers, {})
            if not claim:
                handler._send_json(401, {'ok': False, 'error': 'device_auth_required'})
                return True
            robot, action = claim['robot_id'], device[1]
            if 'robot_id' in payload and payload['robot_id'] != robot:
                raise ValueError('robot_scope_mismatch')
            if not post and action == 'mailbox':
                result = store.mailbox(robot)
            elif not post and action == 'contacts':
                snapshot = store.snapshot(robot)
                result = {'enabled': bool(snapshot['settings']['enabled']), 'contacts': [
                    {'id': c['id'], 'name': c['name'], 'role': c['role'],
                     'available': any(b['state'] == 'active' for b in c['channels'])}
                    for c in snapshot['contacts']]}
            elif post and action == 'send':
                result = {'message_id': store.child_send(robot, payload.get('recipients'), payload.get('text'), payload.get('request_id', ''))}
            elif post and action == 'receipt':
                store.acknowledge(robot, payload.get('message_id'), payload.get('state'))
                result = {}
            else:
                handler._send_json(405, {'ok': False, 'error': 'method_not_allowed'})
                return True
        else:
            scope, robot, action = match.groups()
            panel.require_robot_dir(robot)
            if scope == 'operator':
                if not panel.operator_auth_enabled() or not handler._operator_session():
                    handler._send_json(401, {'ok': False, 'error': 'operator_auth_required'})
                    return True
                if post:
                    origin = handler.headers.get('Origin')
                    expected = panel.PANEL_PUBLIC_SCHEME + '://' + handler.headers.get('Host', '')
                    if (origin and origin != expected) or handler.headers.get('Sec-Fetch-Site') == 'cross-site':
                        handler._send_json(403, {'ok': False, 'error': 'origin_denied'})
                        return True
            elif not panel.validate_mobile_panel_token(robot, handler.headers.get('X-Mobile-Token', '')):
                # Activation codes do not grant access to private correspondence.
                handler._send_json(401, {'ok': False, 'error': 'mobile_token_required'})
                return True
            if not post and action is None:
                result = store.snapshot(robot)
                username = panel.os.environ.get('GOSHA_FAMILY_TELEGRAM_USERNAME', '')
                result['providers'] = {'telegram': {'configured': bool(re.fullmatch(r'[A-Za-z0-9_]{5,32}', username))},
                                       'max': {'configured': False}, 'whatsapp': {'configured': False}}
            elif post and action == 'settings':
                store.configure(robot, payload.get('enabled'), payload.get('read_mode', 'ask'))
                result = {}
            elif post and action == 'contacts':
                result = {'contact_id': store.add_contact(robot, payload.get('name'), payload.get('role'), payload.get('phone', ''))}
            elif post and action == 'invite':
                username = panel.os.environ.get('GOSHA_FAMILY_TELEGRAM_USERNAME', '')
                if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', username):
                    raise ValueError('telegram_not_configured')
                token = store.invite(robot, payload.get('contact_id'))
                result = {'url': f'https://t.me/{username}?start={token}', 'expires_in': 600}
            elif post and action == 'binding':
                store.binding(robot, payload.get('binding_id'), payload.get('action'))
                result = {}
            elif post and action == 'messages':
                result = {'message_id': store.parent_send(robot, payload.get('contact_id'), payload.get('text'), payload.get('request_id', ''))}
            else:
                handler._send_json(405, {'ok': False, 'error': 'method_not_allowed'})
                return True
        handler._send_json(200, {'ok': True, 'data': result})
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        code = str(exc)
        if not re.fullmatch(r'[a-z_]+', code):
            code = 'invalid_request'
        handler._send_json(422, {'ok': False, 'error': code})
    except Exception:
        # Never echo raw messages, DB paths, bot tokens or transport exceptions.
        handler._send_json(503, {'ok': False, 'error': 'family_service_unavailable'})
    return True

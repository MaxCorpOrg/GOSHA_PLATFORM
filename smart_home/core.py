"""Opt-in standalone prototype. Never imported by the installed voice router."""
import asyncio
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class Rejected(ValueError):
    pass


class TransportError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HomeAssistant:
    """Fixed operator-configured origin, no proxy/redirect/retry, bounded JSON."""
    def __init__(self, url, token, *, allow_loopback_http=False, timeout=5):
        p = urlsplit(url)
        try:
            loopback = ipaddress.ip_address(p.hostname or '').is_loopback
        except ValueError:
            loopback = p.hostname == 'localhost'
        if (p.scheme != 'https' and not (p.scheme == 'http' and loopback and allow_loopback_http)
                or not p.hostname or p.username or p.password or p.query or p.fragment
                or p.path not in ('', '/') or not token or '\n' in token or '\r' in token
                or not 0 < timeout <= 30):
            raise Rejected('invalid_connection_config')
        self.url, self._token, self.timeout = url.rstrip('/'), token, timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        # Only our narrow service and state paths are reachable.
        if not re.fullmatch(r'/api/(states/(light|cover|climate|sensor|binary_sensor)\.[a-z0-9_]+|services/(light/(turn_on|turn_off)|cover/(open_cover|close_cover|stop_cover|set_cover_position)|climate/set_temperature))', path):
            raise Rejected('invalid_path')
        if (method == 'GET') != path.startswith('/api/states/') or method not in ('GET', 'POST'):
            raise Rejected('invalid_method')
        req = Request(self.url + path,
                      data=None if body is None else json.dumps(body, allow_nan=False).encode(),
                      headers={'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'},
                      method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise TransportError('invalid_response')
                return json.loads(data)
        except HTTPError as exc:
            # No server body, URL or credentials escape into the voice result.
            code = exc.code
            exc.close()
            raise TransportError('http_' + str(code)) from None
        except (URLError, OSError, TimeoutError):
            raise TransportError('connection_lost') from None
        except (ValueError, UnicodeError):
            raise TransportError('invalid_response') from None


class Provider(Protocol):
    def request(self, method: str, path: str, body=None): ...


@dataclass(frozen=True)
class Device:
    entity: str
    aliases: tuple[str, ...]
    # Climate bounds are commissioned per device, in degrees Celsius.
    min_temperature: float = 16
    max_temperature: float = 28


def normalize(text):
    return ' '.join(text.lower().replace('ё', 'е').strip(' .,!?').split())


class SmartHome:
    def __init__(self, provider: Provider, devices, *, writes_enabled=False):
        self.provider, self.writes_enabled = provider, writes_enabled
        self.devices, self.aliases = {}, {}
        self._seen = {}
        self._lock = asyncio.Lock()
        for device in devices:
            if (not re.fullmatch(r'(light|cover|climate|sensor|binary_sensor)\.[a-z0-9_]+', device.entity)
                    or device.entity in self.devices or not device.aliases
                    or not math.isfinite(device.min_temperature) or not math.isfinite(device.max_temperature)
                    or not 5 <= device.min_temperature <= device.max_temperature <= 35):
                raise Rejected('invalid_device_config')
            self.devices[device.entity] = device
            for alias in device.aliases:
                key = normalize(alias)
                if not key or key in self.aliases:
                    raise Rejected('ambiguous_alias')
                self.aliases[key] = device.entity

    def parse(self, text):
        if not isinstance(text, str) or len(text) > 300:
            raise Rejected('invalid_command')
        text = normalize(text).replace('гоша, ', 'гоша ', 1)
        if text.startswith('гоша '):
            text = text[5:]
        patterns = (
            (r'включи (.+)', 'on'), (r'выключи (.+)', 'off'),
            (r'открой (.+)', 'open'), (r'закрой (.+)', 'close'),
            (r'останови (.+)', 'stop'), (r'статус (.+)', 'read'),
            (r'установи яркость (.+) на (\d{1,3}) процентов', 'brightness'),
            (r'открой (.+) на (\d{1,3}) процентов', 'position'),
            (r'установи температуру (.+) на (\d{1,2}(?:[.,]\d)?) градус(?:а|ов)?', 'temperature'),
        )
        # Specific numeric patterns first; never infer a device or execute a batch.
        for pattern, action in reversed(patterns):
            match = re.fullmatch(pattern, text)
            if match and match[1] in self.aliases:
                command = {'entity': self.aliases[match[1]], 'action': action}
                if match.lastindex == 2:
                    command['value'] = float(match[2].replace(',', '.'))
                return command
        raise Rejected('clarification_required')

    def plan(self, command):
        if not isinstance(command, dict) or set(command) not in ({'entity', 'action'}, {'entity', 'action', 'value'}):
            raise Rejected('invalid_arguments')
        entity, action = command['entity'], command['action']
        if not isinstance(entity, str) or entity not in self.devices or not isinstance(action, str):
            raise Rejected('device_not_allowed')
        domain = entity.split('.')[0]
        mapping = {'light': {'on': 'turn_on', 'off': 'turn_off', 'brightness': 'turn_on'},
                   'cover': {'open': 'open_cover', 'close': 'close_cover', 'stop': 'stop_cover', 'position': 'set_cover_position'},
                   'climate': {'temperature': 'set_temperature'}}
        numeric = action in ('brightness', 'position', 'temperature')
        if ('value' in command) != numeric:
            raise Rejected('invalid_value')
        if action == 'read':
            return 'GET', '/api/states/' + entity, None
        service = mapping.get(domain, {}).get(action)
        if not service:
            raise Rejected('unsupported_action')
        body = {'entity_id': entity}
        if numeric:
            value = command['value']
            if type(value) not in (int, float) or not math.isfinite(value):
                raise Rejected('invalid_value')
            device = self.devices[entity]
            lo, hi = (device.min_temperature, device.max_temperature) if action == 'temperature' else (0, 100)
            if not lo <= value <= hi or (action != 'temperature' and value != int(value)):
                raise Rejected('out_of_range')
            body[{'brightness': 'brightness_pct', 'position': 'position', 'temperature': 'temperature'}[action]] = value
        return 'POST', '/api/services/' + domain + '/' + service, body

    def _execute(self, command):
        method, path, body = self.plan(command)
        if method == 'POST' and not self.writes_enabled:
            return {'status': 'planned', 'sent': False, 'command': command}
        try:
            if method == 'POST':
                current = self.provider.request('GET', '/api/states/' + command['entity'])
                self._state(current, command['entity'])
                attrs = current.get('attributes', {})
                if command['entity'].startswith('cover.'):
                    if attrs.get('device_class') not in ('curtain', 'shade', 'blind'):
                        raise Rejected('only_window_coverings_allowed')
                    if command['action'] == 'position' and not int(attrs.get('supported_features', 0)) & 4:
                        raise Rejected('position_unsupported')
                if command['action'] == 'brightness':
                    modes = attrs.get('supported_color_modes', [])
                    if not isinstance(modes, list) or not set(modes) - {'onoff', 'unknown'}:
                        raise Rejected('brightness_unsupported')
                if command['action'] == 'temperature':
                    value = body['temperature']
                    if attrs.get('temperature_unit') != '°C':
                        raise Rejected('celsius_required')
                    if not attrs.get('min_temp', 5) <= value <= attrs.get('max_temp', 35):
                        raise Rejected('device_temperature_range')
                response = self.provider.request(method, path, body)
                if not isinstance(response, list):
                    raise TransportError('invalid_response')
                # HTTP success does NOT establish that a physical actuator moved.
                return {'status': 'accepted', 'physical_confirmation': False, 'retry': False}
            state = self.provider.request(method, path)
            return {'status': 'observed', 'state': self._state(state, command['entity'])}
        except TransportError as exc:
            return {'status': 'unknown' if method == 'POST' else 'unavailable',
                    'reason': exc.reason, 'retry': False}

    @staticmethod
    def _state(state, entity):
        if (not isinstance(state, dict) or state.get('entity_id') != entity
                or not isinstance(state.get('state'), str)
                or not isinstance(state.get('attributes', {}), dict)):
            raise Rejected('invalid_state')
        if state['state'] in ('unknown', 'unavailable'):
            raise Rejected('device_unavailable')
        # Data, never instructions. Keep only the state, units and timestamp.
        return {k: v for k, v in {'value': state['state'][:128],
                'unit': str(state.get('attributes', {}).get('unit_of_measurement', ''))[:24],
                'last_updated': str(state.get('last_updated', ''))[:64]}.items()}

    async def call(self, name, encoded):
        """LiveToolRunner-compatible candidate; not registered in runtime."""
        try:
            if name != 'smart_home_command' or not isinstance(encoded, str) or len(encoded) > 1024:
                raise Rejected('invalid_arguments')
            return await asyncio.to_thread(self._execute, json.loads(encoded))
        except (Rejected, ValueError, TypeError):
            return {'status': 'rejected', 'reason': 'invalid_or_unavailable_command'}

    async def voice(self, text, request_id):
        # Session-local deduplication; caller must not replay after process restart.
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return {'status': 'rejected', 'reason': 'invalid_request_id'}
        async with self._lock:
            if request_id in self._seen:
                return self._seen[request_id]
            if len(self._seen) >= 256:
                return {'status': 'rejected', 'reason': 'session_limit'}
            try:
                command = self.parse(text)
            except Rejected as exc:
                return {'status': 'rejected', 'reason': str(exc)}
            # Reserve before awaiting: cancellation cannot cause a duplicate write.
            self._seen[request_id] = {'status': 'unknown', 'retry': False}
            result = await self.call('smart_home_command', json.dumps(command))
            self._seen[request_id] = result
            return result

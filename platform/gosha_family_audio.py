"""Bounded speech of a stored mailbox item; never a conversation/model tool call."""
from array import array
from collections import OrderedDict
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler

from gosha_live_audio import OpusCodec, PCM_BYTES

MAX_SECONDS = 120
MAX_WIRE = 384 * 1024
CONTENT_TYPE = 'application/vnd.gosha.opus'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('speech_redirect_denied')


class SRCData(C.Structure):
    _fields_ = [('data_in', C.POINTER(C.c_float)), ('data_out', C.POINTER(C.c_float)),
                ('input_frames', C.c_long), ('output_frames', C.c_long),
                ('input_frames_used', C.c_long), ('output_frames_gen', C.c_long),
                ('end_of_input', C.c_int), ('src_ratio', C.c_double)]


def resample(pcm):
    """Official Speech PCM: signed little-endian mono 24 kHz -> robot 16 kHz."""
    if not pcm or len(pcm) % 2 or len(pcm) > 24000 * 2 * MAX_SECONDS:
        raise ValueError('invalid_speech_audio')
    library = ctypes.util.find_library('samplerate')
    if not library:
        raise RuntimeError('libsamplerate_missing')
    lib = C.CDLL(library)
    lib.src_simple.argtypes = [C.POINTER(SRCData), C.c_int, C.c_int]
    lib.src_simple.restype = C.c_int
    lib.src_short_to_float_array.argtypes = [C.POINTER(C.c_short), C.POINTER(C.c_float), C.c_int]
    lib.src_float_to_short_array.argtypes = [C.POINTER(C.c_float), C.POINTER(C.c_short), C.c_int]
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    count = len(samples)
    source = (C.c_short * count).from_buffer(samples)
    floats = (C.c_float * count)()
    lib.src_short_to_float_array(source, floats, count)
    capacity = (count * 2 + 2) // 3 + 1
    output = (C.c_float * capacity)()
    data = SRCData(floats, output, count, capacity, 0, 0, 1, 2 / 3)
    if lib.src_simple(C.byref(data), 2, 1) or data.input_frames_used != count or not data.output_frames_gen:
        raise ValueError('speech_resample_failed')
    result = (C.c_short * data.output_frames_gen)()
    lib.src_float_to_short_array(output, result, len(result))
    raw = array('h', result)
    if sys.byteorder != 'little':
        raw.byteswap()
    return raw.tobytes()


def packetize(pcm16):
    if not pcm16 or len(pcm16) % 2 or len(pcm16) > 16000 * 2 * MAX_SECONDS:
        raise ValueError('invalid_speech_audio')
    # Drain encoder lookahead so the final syllable is not cut off.
    pcm16 += bytes(PCM_BYTES)
    if len(pcm16) > 16000 * 2 * MAX_SECONDS:
        raise ValueError('speech_audio_too_long')
    codec = OpusCodec(bitrate=16000)
    wire = bytearray(struct.pack('!4sIHBB', b'GFM1', 16000, 60, 1, 0))
    try:
        for offset in range(0, len(pcm16), PCM_BYTES):
            frame = codec.encode(pcm16[offset:offset+PCM_BYTES].ljust(PCM_BYTES, b'\0'))
            if not 0 < len(frame) <= 1275:
                raise ValueError('invalid_speech_frame')
            wire.extend(struct.pack('!H', len(frame)))
            wire.extend(frame)
            if len(wire) + 2 > MAX_WIRE:
                raise ValueError('speech_audio_too_large')
        wire.extend(b'\0\0')
        return bytes(wire)
    finally:
        codec.close()


def speech_config():
    if os.environ.get('GOSHA_FAMILY_TTS_ENABLED') != '1':
        raise RuntimeError('family_speech_disabled')
    key_path = os.environ.get('GOSHA_FAMILY_TTS_KEY_FILE', '')
    if not key_path:
        raise RuntimeError('family_speech_key_missing')
    key = Path(key_path).read_text().strip()
    if not key or len(key) > 1024 or any(c.isspace() for c in key):
        raise RuntimeError('family_speech_key_invalid')
    model = os.environ.get('GOSHA_FAMILY_TTS_MODEL', 'gpt-4o-mini-tts')
    voice = os.environ.get('GOSHA_FAMILY_TTS_VOICE', 'coral')
    return model, voice, key


def synthesize(text, config):
    model, voice, key = config
    payload = {'model': model, 'voice': voice, 'input': text, 'response_format': 'pcm'}
    request = Request('https://api.openai.com/v1/audio/speech',
                      data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
    limit = 24000 * 2 * MAX_SECONDS
    deadline = time.monotonic() + 45
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        if (response.status != 200 or response.headers.get_content_type() not in
                ('application/octet-stream', 'audio/pcm')):
            raise RuntimeError('family_speech_failed')
        pcm = bytearray()
        while len(pcm) <= limit:
            chunk = response.read(min(65536, limit + 1 - len(pcm)))
            if not chunk:
                break
            pcm.extend(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError('family_speech_timeout')
    return packetize(resample(bytes(pcm)))


class FamilySpeech:
    def __init__(self, render=synthesize, configuration=speech_config, clock=time.monotonic):
        self.render, self.configuration, self.clock = render, configuration, clock
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.failed = OrderedDict()

    def audio(self, store, robot, message_id):
        message = store.audio_message(robot, message_id)  # Scope/mode/expiry checked even on a cache hit.
        config = self.configuration()
        signature = hashlib.sha256(json.dumps(config).encode()).digest()
        key = (str(store.path.resolve()), robot, message_id, signature)
        # Single in-flight renderer avoids duplicate billing and bounds RAM. A
        # contending request can retry later; never enqueue unlimited TTS jobs.
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('family_speech_busy')
        try:
            now = self.clock()
            for k in list(self.cache):
                if self.cache[k][0] <= now:
                    del self.cache[k]
            if key in self.cache:
                return self.cache[key][1]
            if self.failed.get(key, 0) > now:
                raise RuntimeError('family_speech_cooldown')
            try:
                audio = self.render(message['author']+' пишет: '+message['body'], config)
                store.audio_message(robot, message_id)  # Disable/expiry during generation: no audio served.
            except Exception:
                self.failed[key] = self.clock() + 60
                while len(self.failed) > 64:
                    self.failed.popitem(last=False)
                raise
            self.cache[key] = (self.clock() + 600, audio)
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
            return audio
        finally:
            self.lock.release()


SERVICE = FamilySpeech()

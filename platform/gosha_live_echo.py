"""Bounded, in-memory server echo cancellation using robot playback timestamps."""
import ctypes
import ctypes.util
from collections import OrderedDict

from gosha_live_audio import PCM_BYTES, RATE, SILENCE


class EchoCanceller:
    """SpeexDSP AEC + residual suppression; one independent state per robot.

    The reference is the decoded Opus actually sent to the loudspeaker. Firmware
    returns that packet's timestamp after playback. Network round-trip delay is
    therefore outside the adaptive filter's acoustic tail.
    """
    def __init__(self):
        self.echo = self.preprocess = None
        path = ctypes.util.find_library("speexdsp")
        if not path:
            raise RuntimeError("libspeexdsp_missing")
        self.lib = ctypes.CDLL(path)
        pointer, integer = ctypes.c_void_p, ctypes.c_int
        for name, args, result in (
            ("speex_echo_state_init", [integer, integer], pointer),
            ("speex_echo_state_destroy", [pointer], None),
            ("speex_echo_ctl", [pointer, integer, pointer], integer),
            ("speex_echo_cancellation", [pointer, pointer, pointer, pointer], None),
            ("speex_preprocess_state_init", [integer, integer], pointer),
            ("speex_preprocess_state_destroy", [pointer], None),
            ("speex_preprocess_ctl", [pointer, integer, pointer], integer),
            ("speex_preprocess_run", [pointer, pointer], integer),
        ):
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        self.frame_bytes = RATE * 20 // 1000 * 2
        self.echo = self.lib.speex_echo_state_init(self.frame_bytes // 2, RATE // 5)
        self.preprocess = self.lib.speex_preprocess_state_init(self.frame_bytes // 2, RATE)
        if not self.echo or not self.preprocess:
            self.close()
            raise RuntimeError("echo_init_failed")
        rate = integer(RATE)
        self.lib.speex_echo_ctl(self.echo, 24, ctypes.byref(rate))
        self.lib.speex_preprocess_ctl(self.preprocess, 24, self.echo)
        for setting, value in ((0, 1), (2, 0), (18, -10), (20, -45), (22, -12)):
            argument = integer(value)
            self.lib.speex_preprocess_ctl(self.preprocess, setting, ctypes.byref(argument))
        self.references = OrderedDict()
        self.last_timestamp = 0
        self.counts = dict.fromkeys(("processed", "aligned", "reference_missing"), 0)

    def remember_playback(self, timestamp, pcm):
        if type(timestamp) is not int or timestamp <= 0 or len(pcm) != PCM_BYTES:
            raise ValueError("invalid_echo_reference")
        self.references[timestamp] = pcm
        while len(self.references) > 100:
            self.references.popitem(last=False)

    def process(self, microphone, timestamp):
        if len(microphone) != PCM_BYTES:
            raise ValueError("invalid_echo_microphone")
        self.counts["processed"] += 1
        if timestamp:
            reference = self.references.get(timestamp)
            if reference is None or timestamp <= self.last_timestamp:
                self.counts["reference_missing"] += 1
                # Do not feed unaligned loudspeaker echo into Live. Never persist
                # audio; a later valid timestamp recovers without replaying frames.
                return SILENCE
            self.last_timestamp = timestamp
            self.counts["aligned"] += 1
        else:
            reference = SILENCE
        output = bytearray()
        for offset in range(0, PCM_BYTES, self.frame_bytes):
            near = ctypes.create_string_buffer(microphone[offset:offset + self.frame_bytes])
            far = ctypes.create_string_buffer(reference[offset:offset + self.frame_bytes])
            cleaned = ctypes.create_string_buffer(self.frame_bytes)
            self.lib.speex_echo_cancellation(self.echo, near, far, cleaned)
            self.lib.speex_preprocess_run(self.preprocess, cleaned)
            output.extend(cleaned.raw)
        return bytes(output)

    def close(self):
        if self.preprocess:
            self.lib.speex_preprocess_state_destroy(self.preprocess)
            self.preprocess = None
        if self.echo:
            self.lib.speex_echo_state_destroy(self.echo)
            self.echo = None

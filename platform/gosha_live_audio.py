"""Opus/PCM bridge for the existing robot wire protocol (no audio persistence)."""
import ctypes
import ctypes.util
import struct

RATE = 16000
FRAME_MS = 60
SAMPLES = RATE * FRAME_MS // 1000
PCM_BYTES = SAMPLES * 2
SILENCE = bytes(PCM_BYTES)
AUDIO_PARAMS = {"format": "opus", "sample_rate": RATE, "channels": 1, "frame_duration": FRAME_MS}


def unpack_audio(packet, version):
    if version == 2:
        if len(packet) < 16:
            raise ValueError("invalid_audio_header")
        wire_version, kind, reserved, timestamp, size = struct.unpack("!HHIII", packet[:16])
        if wire_version != 2 or kind != 0 or reserved != 0 or size != len(packet) - 16:
            raise ValueError("invalid_audio_header")
        packet = packet[16:]
    elif version == 3:
        if len(packet) < 4:
            raise ValueError("invalid_audio_header")
        kind, reserved, size = struct.unpack("!BBH", packet[:4])
        if kind != 0 or reserved != 0 or size != len(packet) - 4:
            raise ValueError("invalid_audio_header")
        packet = packet[4:]
    elif version != 1:
        raise ValueError("unsupported_protocol")
    if not 0 < len(packet) <= 4000:
        raise ValueError("invalid_opus_packet")
    return packet


def audio_timestamp(packet, version):
    # Validate the entire header before inspecting the optional timestamp.
    unpack_audio(packet, version)
    return struct.unpack("!I", packet[8:12])[0] if version == 2 else 0


def pack_audio(packet, version, timestamp=0):
    if version == 1:
        return packet
    if version == 2:
        return struct.pack("!HHIII", 2, 0, 0, timestamp, len(packet)) + packet
    if version == 3:
        return struct.pack("!BBH", 0, 0, len(packet)) + packet
    raise ValueError("unsupported_protocol")


def audible(pcm):
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    return bool(samples) and sum(s * s for s in samples) > len(samples) * 40 * 40


class OpusCodec:
    """Own one encoder/decoder per connection; use the system libopus."""

    def __init__(self):
        self.encoder = self.decoder = None
        library = ctypes.util.find_library("opus")
        if not library:
            raise RuntimeError("libopus_missing")
        self.lib = ctypes.CDLL(library)
        p = ctypes.c_void_p
        i = ctypes.c_int
        self.lib.opus_encoder_create.argtypes = [i, i, i, ctypes.POINTER(i)]
        self.lib.opus_encoder_create.restype = p
        self.lib.opus_decoder_create.argtypes = [i, i, ctypes.POINTER(i)]
        self.lib.opus_decoder_create.restype = p
        self.lib.opus_encode.argtypes = [p, p, i, p, i]
        self.lib.opus_encode.restype = i
        self.lib.opus_decode.argtypes = [p, p, i, p, i, i]
        self.lib.opus_decode.restype = i
        self.lib.opus_encoder_destroy.argtypes = [p]
        self.lib.opus_decoder_destroy.argtypes = [p]
        error = i()
        self.encoder = self.lib.opus_encoder_create(RATE, 1, 2048, ctypes.byref(error))
        if error.value or not self.encoder:
            raise RuntimeError("opus_encoder_failed")
        self.decoder = self.lib.opus_decoder_create(RATE, 1, ctypes.byref(error))
        if error.value or not self.decoder:
            self.close()
            raise RuntimeError("opus_decoder_failed")

    def encode(self, pcm):
        if len(pcm) != PCM_BYTES or not self.encoder:
            raise ValueError("invalid_pcm_frame")
        source = ctypes.create_string_buffer(pcm)
        output = ctypes.create_string_buffer(4000)
        size = self.lib.opus_encode(self.encoder, source, SAMPLES, output, len(output))
        if size < 0:
            raise ValueError("opus_encode_failed")
        return output.raw[:size]

    def decode(self, opus):
        if not opus or len(opus) > 4000 or not self.decoder:
            raise ValueError("invalid_opus_packet")
        source = ctypes.create_string_buffer(opus)
        output = ctypes.create_string_buffer(PCM_BYTES)
        size = self.lib.opus_decode(self.decoder, source, len(opus), output, SAMPLES, 0)
        if size != SAMPLES:
            raise ValueError("unsupported_opus_duration")
        return output.raw

    def close(self):
        if self.encoder:
            self.lib.opus_encoder_destroy(self.encoder)
            self.encoder = None
        if self.decoder:
            self.lib.opus_decoder_destroy(self.decoder)
            self.decoder = None

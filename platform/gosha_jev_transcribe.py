"""Model transcription of the robot's live PCM stream for the opt-in JEV route.

Only final transcripts leave this adapter. Audio is never logged or stored.
"""
import asyncio
import base64
import json
import struct

from websockets.legacy.client import connect

from gosha_live_audio import PCM_BYTES, RATE

TRANSCRIBE_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
TRANSCRIBE_MODEL = "gpt-transcribe"
OUTPUT_RATE = 24000
INPUT_SAMPLES = PCM_BYTES // 2
OUTPUT_SAMPLES = INPUT_SAMPLES * OUTPUT_RATE // RATE


class TranscriptionError(RuntimeError):
    """Fixed local reason only, without provider payload or transcript."""


def pcm_16k_to_24k(pcm):
    if not isinstance(pcm, bytes) or len(pcm) != PCM_BYTES:
        raise TranscriptionError("invalid_pcm_frame")
    samples = struct.unpack("<%dh" % INPUT_SAMPLES, pcm)
    output = bytearray(OUTPUT_SAMPLES * 2)
    for target in range(OUTPUT_SAMPLES):
        source_thirds = target * 2
        index, fraction = divmod(source_thirds, 3)
        left = samples[index]
        right = samples[min(index + 1, INPUT_SAMPLES - 1)]
        value = (left * (3 - fraction) + right * fraction) // 3
        struct.pack_into("<h", output, target * 2, value)
    return bytes(output)


class ModelTranscriber:
    def __init__(self, api_key, *, connector=connect):
        if not api_key:
            raise TranscriptionError("api_key_missing")
        self.api_key = api_key
        self.connector = connector
        self.ws = None

    async def start(self):
        try:
            self.ws = await self.connector(
                TRANSCRIBE_URL,
                extra_headers={"Authorization": "Bearer " + self.api_key},
                open_timeout=8, close_timeout=2, max_size=2**20, max_queue=16,
            )
            async with asyncio.timeout(8):
                created = json.loads(await self.ws.recv())
                if created.get("type") != "session.created":
                    raise TranscriptionError("session_not_created")
                await self.ws.send(json.dumps({"type": "session.update", "session": {
                    "type": "transcription", "audio": {"input": {
                        "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                        "transcription": {"model": TRANSCRIBE_MODEL, "languages": ["ru"]},
                        "turn_detection": {"type": "server_vad", "threshold": 0.5,
                                           "prefix_padding_ms": 300, "silence_duration_ms": 1000},
                    }},
                }}))
                while True:
                    event = json.loads(await self.ws.recv())
                    if event.get("type") == "session.updated":
                        return
                    if event.get("type") == "error":
                        raise TranscriptionError("session_update_rejected")
        except TranscriptionError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise TranscriptionError("session_unavailable") from None

    async def send_pcm(self, pcm):
        encoded = base64.b64encode(pcm_16k_to_24k(pcm)).decode("ascii")
        try:
            await self.ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": encoded}))
        except Exception:
            raise TranscriptionError("audio_send_failed") from None

    async def events(self):
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                if event.get("type") == "error":
                    raise TranscriptionError("transcription_rejected")
                yield event
        except TranscriptionError:
            raise
        except Exception:
            raise TranscriptionError("transcription_connection_lost") from None

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

"""The robot's 16 kHz frames feed a dedicated 24 kHz transcription session."""
import asyncio
import base64
import json
import unittest

from gosha_jev_transcribe import ModelTranscriber, TranscriptionError, pcm_16k_to_24k
from gosha_live_audio import PCM_BYTES, SILENCE


class FakeSocket:
    def __init__(self):
        self.received = asyncio.Queue()
        self.received.put_nowait(json.dumps({"type": "session.created"}))
        self.received.put_nowait(json.dumps({"type": "session.updated"}))
        self.sent = []
        self.closed = False

    async def recv(self):
        return await self.received.get()

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def close(self):
        self.closed = True


class TranscriberTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_uses_model_and_server_turn_detection(self):
        socket = FakeSocket()
        async def connector(url, **kwargs):
            self.assertIn("intent=transcription", url)
            self.assertTrue(kwargs["extra_headers"]["Authorization"].startswith("Bearer "))
            return socket
        transcriber = ModelTranscriber("unit-key", connector=connector)
        await transcriber.start()
        setting = socket.sent[0]["session"]["audio"]["input"]
        self.assertEqual(setting["transcription"]["model"], "gpt-transcribe")
        self.assertEqual(setting["turn_detection"]["type"], "server_vad")
        await transcriber.send_pcm(SILENCE)
        self.assertEqual(len(base64.b64decode(socket.sent[1]["audio"])), PCM_BYTES * 3 // 2)
        await transcriber.close()
        self.assertTrue(socket.closed)

    async def test_rejected_session_never_sends_audio(self):
        socket = FakeSocket()
        socket.received = asyncio.Queue()
        socket.received.put_nowait(json.dumps({"type": "session.created"}))
        socket.received.put_nowait(json.dumps({"type": "error", "error": {"message": "secret"}}))
        async def connector(url, **kwargs):
            return socket
        transcriber = ModelTranscriber("unit-key", connector=connector)
        with self.assertRaises(TranscriptionError):
            await transcriber.start()
        self.assertTrue(socket.closed)

    async def test_invalid_pcm_frame_is_rejected(self):
        with self.assertRaises(TranscriptionError):
            pcm_16k_to_24k(b"short")


if __name__ == "__main__":
    unittest.main()

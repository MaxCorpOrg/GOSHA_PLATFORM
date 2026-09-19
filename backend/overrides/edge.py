import os
import uuid
from datetime import datetime

import edge_tts

from core.providers.tts.base import TTSProviderBase


def _clamp_float(value, default, min_value, max_value):
    try:
        result = float(value)
    except Exception:
        result = float(default)
    if result < min_value:
        return min_value
    if result > max_value:
        return max_value
    return result


def _rate_to_edge(rate_multiplier):
    percent = round((rate_multiplier - 1.0) * 100)
    return f"{percent:+d}%"


def _pitch_to_edge(pitch_multiplier):
    hz = round((pitch_multiplier - 1.0) * 50)
    return f"{hz:+d}Hz"


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice")
        self.audio_file_type = config.get("format", "mp3")
        self.rate_multiplier = _clamp_float(config.get("speech_rate", config.get("rate_multiplier", 1.0)), 1.0, 0.5, 2.0)
        self.pitch_multiplier = _clamp_float(config.get("pitch", config.get("pitch_multiplier", 1.0)), 1.0, 0.5, 2.0)
        self.rate = str(config.get("rate", "") or "").strip() or _rate_to_edge(self.rate_multiplier)
        self.pitch = str(config.get("pitch_hz", "") or "").strip() or _pitch_to_edge(self.pitch_multiplier)

    def generate_filename(self, extension=".mp3"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    async def text_to_speak(self, text, output_file):
        try:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch,
            )
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as f:
                    pass

                with open(output_file, "ab") as f:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            f.write(chunk["data"])
            else:
                audio_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_bytes += chunk["data"]
                return audio_bytes
        except Exception as e:
            error_msg = f"Edge TTS request failed: {e}"
            raise Exception(error_msg)

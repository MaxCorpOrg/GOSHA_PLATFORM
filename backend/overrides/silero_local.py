import asyncio
import io
import os
import uuid
import wave
from array import array
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

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


def _coerce_bool(value, default):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_int(value, default, min_value):
    try:
        result = int(value)
    except Exception:
        result = int(default)
    return max(min_value, result)


def _prosody_percent(multiplier):
    percent = round((multiplier - 1.0) * 100)
    return f"{percent:+d}%"


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.audio_file_type = str(config.get("format", "wav") or "wav").strip() or "wav"
        self.model_id = str(config.get("model_id", "v5_5_ru") or "v5_5_ru").strip() or "v5_5_ru"
        self.language = str(config.get("language", "ru") or "ru").strip() or "ru"
        self.speaker = str(
            config.get("voice_name")
            or config.get("private_voice")
            or config.get("speaker")
            or "xenia"
        ).strip() or "xenia"
        self.sample_rate = _coerce_int(config.get("sample_rate", 24000), 24000, 8000)
        self.device = str(config.get("device", "cpu") or "cpu").strip() or "cpu"
        self.cache_dir = str(
            config.get("cache_dir", "/opt/xiaozhi-esp32-server/models/silero") or "/opt/xiaozhi-esp32-server/models/silero"
        ).strip() or "/opt/xiaozhi-esp32-server/models/silero"
        self.use_ssml = _coerce_bool(config.get("use_ssml", True), True)
        self.put_accent = _coerce_bool(config.get("put_accent", True), True)
        self.put_yo = _coerce_bool(config.get("put_yo", True), True)
        self.num_threads = _coerce_int(config.get("num_threads", 2), 2, 1)
        self.rate_multiplier = _clamp_float(config.get("speech_rate", 1.0), 1.0, 0.5, 2.0)
        self.pitch_multiplier = _clamp_float(config.get("pitch", 1.0), 1.0, 0.5, 2.0)
        self._model = None
        self._torch = None

    def generate_filename(self, extension=".wav"):
        return os.path.join(
            self.output_file,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            import torch
        except Exception as exc:
            raise Exception(
                "SileroTTS недоступен: в серверном окружении не найден torch. "
                "Оставьте EdgeTTS резервом или установите зависимости для Silero."
            ) from exc

        cache_root = Path(self.cache_dir)
        cache_root.mkdir(parents=True, exist_ok=True)
        hub_dir = cache_root / "torch_hub"
        hub_dir.mkdir(parents=True, exist_ok=True)
        torch.hub.set_dir(str(hub_dir))
        try:
            torch.set_num_threads(self.num_threads)
        except Exception:
            pass

        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language=self.language,
            speaker=self.model_id,
        )
        try:
            model.to(self.device)
        except Exception:
            self.device = "cpu"
            model.to(self.device)

        self._torch = torch
        self._model = model
        return model

    def _prepare_request(self, text):
        clean_text = str(text or "").strip()
        if not clean_text:
            return {"text": ""}
        if "<speak" in clean_text:
            return {"ssml_text": clean_text}
        if not self.use_ssml:
            return {"text": clean_text}
        if self.rate_multiplier == 1.0 and self.pitch_multiplier == 1.0:
            return {"text": clean_text}
        ssml_text = (
            f'<speak><prosody rate="{_prosody_percent(self.rate_multiplier)}" '
            f'pitch="{_prosody_percent(self.pitch_multiplier)}">{escape(clean_text)}</prosody></speak>'
        )
        return {"ssml_text": ssml_text}

    def _render_wav_bytes(self, audio_tensor):
        torch = self._torch
        normalized = torch.clamp(audio_tensor.detach().cpu().float(), -1.0, 1.0)
        samples = normalized.mul(32767).to(torch.int16).tolist()
        pcm = array("h", samples)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return buffer.getvalue()

    async def text_to_speak(self, text, output_file):
        try:
            model = self._ensure_model()
            request = self._prepare_request(text)
            request["speaker"] = self.speaker
            request["sample_rate"] = self.sample_rate
            request["put_accent"] = self.put_accent
            request["put_yo"] = self.put_yo
            audio_tensor = await asyncio.to_thread(model.apply_tts, **request)
            wav_bytes = self._render_wav_bytes(audio_tensor)
            if output_file:
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                with open(output_file, "wb") as handle:
                    handle.write(wav_bytes)
                return None
            return wav_bytes
        except Exception as exc:
            raise Exception(f"Silero TTS request failed: {exc}") from exc

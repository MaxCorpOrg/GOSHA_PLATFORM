"""Local completed-utterance capture and Russian ASR for the JEV trial.

Never classify a partial transcript. Audio stays in memory for offline Vosk;
TypeSafe sees only the resulting English translation.
"""
import json
from collections import deque

from gosha_live_audio import PCM_BYTES, RATE


class SpeechError(RuntimeError):
    pass


class CompletedUtterances:
    """End a phrase after sustained silence; discard truncated long speech."""

    FRAME_MS = 60

    def __init__(self, *, vad=None, end_silence_ms=1500, max_speech_ms=8000):
        if vad is None:
            import webrtcvad
            vad = webrtcvad.Vad(2)
        self.vad = vad
        self.end_frames = end_silence_ms // self.FRAME_MS
        self.max_frames = max_speech_ms // self.FRAME_MS
        self.preroll = deque(maxlen=5)
        self.frames = []
        self.start_frames = 0
        self.voiced_frames = 0
        self.silent_frames = 0
        self.discarding = False

    def reset(self):
        self.preroll.clear()
        self.frames = []
        self.start_frames = 0
        self.voiced_frames = 0
        self.silent_frames = 0
        self.discarding = False

    def push(self, pcm):
        if not isinstance(pcm, bytes) or len(pcm) != PCM_BYTES:
            raise SpeechError("invalid_pcm_frame")
        # webrtcvad accepts 10/20/30 ms at 16 kHz, so split the wire's 60 ms.
        half = PCM_BYTES // 2
        voiced = any(self.vad.is_speech(part, RATE) for part in (pcm[:half], pcm[half:]))
        self.preroll.append(pcm)
        if self.discarding:
            self.silent_frames = 0 if voiced else self.silent_frames + 1
            if self.silent_frames >= self.end_frames:
                self.reset()
            return None
        if not self.frames:
            self.start_frames = self.start_frames + 1 if voiced else 0
            if self.start_frames >= 2:
                self.frames = list(self.preroll)
                self.voiced_frames = 2
            return None
        self.frames.append(pcm)
        if voiced:
            self.voiced_frames += 1
            self.silent_frames = 0
        else:
            self.silent_frames += 1
        if len(self.frames) > self.max_frames:
            self.frames = []
            self.discarding = True
            self.silent_frames = 0
            return None
        if self.silent_frames < self.end_frames:
            return None
        result = b"".join(self.frames[:-self.silent_frames + 3]) if self.voiced_frames >= 5 else None
        self.reset()
        return result


class VoskLocalTranscriber:
    """Load one small Russian model and decode one completed utterance at a time."""

    def __init__(self, model_dir):
        from pathlib import Path
        if not (Path(model_dir) / "am/final.mdl").is_file():
            raise SpeechError("asr_model_missing")
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        self.model = Model(str(model_dir))
        self.recognizer = KaldiRecognizer

    def transcribe(self, pcm):
        if not isinstance(pcm, bytes) or not PCM_BYTES <= len(pcm) <= RATE * 2 * 8:
            raise SpeechError("invalid_utterance_audio")
        recognizer = self.recognizer(self.model, RATE)
        parts = []
        for offset in range(0, len(pcm), PCM_BYTES):
            if recognizer.AcceptWaveform(pcm[offset:offset + PCM_BYTES]):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        source = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
        if not source or len(source) > 2000:
            raise SpeechError("asr_invalid_text")
        return source

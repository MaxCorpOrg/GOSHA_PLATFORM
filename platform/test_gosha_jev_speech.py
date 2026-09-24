"""Speech boundaries must precede any physical JEV decision."""
import unittest

from gosha_jev_speech import CompletedUtterances, SpeechError, VoskLocalTranscriber
from gosha_live_audio import PCM_BYTES


QUIET = bytes(PCM_BYTES)
VOICE = b"\x01\x00" * (PCM_BYTES // 2)


class FakeVAD:
    def is_speech(self, frame, rate):
        assert rate == 16000
        return frame != bytes(len(frame))


class SpeechTests(unittest.TestCase):
    def test_correction_after_short_pause_stays_in_same_utterance(self):
        segmenter = CompletedUtterances(vad=FakeVAD())
        frames = [QUIET] * 6 + [VOICE] * 12 + [QUIET] * 10 + [VOICE] * 6 + [QUIET] * 26
        completed = [phrase for frame in frames if (phrase := segmenter.push(frame))]
        self.assertEqual(len(completed), 1)
        self.assertGreaterEqual(completed[0].count(VOICE), 18)

    def test_very_long_speech_is_discarded_instead_of_partial_dispatch(self):
        segmenter = CompletedUtterances(vad=FakeVAD(), max_speech_ms=600)
        completed = [phrase for frame in [VOICE] * 30 + [QUIET] * 30
                     if (phrase := segmenter.push(frame))]
        self.assertEqual(completed, [])

    def test_short_noise_and_invalid_frames_are_rejected(self):
        segmenter = CompletedUtterances(vad=FakeVAD())
        completed = [phrase for frame in [VOICE] * 2 + [QUIET] * 30
                     if (phrase := segmenter.push(frame))]
        self.assertEqual(completed, [])
        with self.assertRaises(SpeechError):
            segmenter.push(b"short")

    def test_vosk_combines_internal_segments_before_any_decision(self):
        class Recognizer:
            calls = 0
            def AcceptWaveform(self, data):
                self.calls += 1
                return self.calls == 2
            def Result(self):
                return '{"text":"гоша помаши рукой"}'
            def FinalResult(self):
                return '{"text":"нет не надо"}'
        transcriber = VoskLocalTranscriber.__new__(VoskLocalTranscriber)
        transcriber.model = object()
        transcriber.recognizer = lambda model, rate: Recognizer()
        self.assertEqual(transcriber.transcribe(VOICE * 6),
                         "гоша помаши рукой нет не надо")
        with self.assertRaises(SpeechError):
            transcriber.transcribe(b"short")


if __name__ == "__main__":
    unittest.main()

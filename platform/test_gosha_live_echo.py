"""Synthetic acoustic checks with the actual SpeexDSP library; no recordings."""
import math
import random
import struct
import unittest
from gosha_live_audio import SAMPLES, SILENCE, audio_timestamp, pack_audio, unpack_audio
from gosha_live_echo import EchoCanceller


def pcm(samples):
    return struct.pack("<%dh" % len(samples), *samples)


def samples(data):
    return struct.unpack("<%dh" % (len(data) // 2), data)


class EchoTests(unittest.TestCase):
    def test_timestamp_wire_contract(self):
        packet = pack_audio(b"opus-fixture", 2, 12345)
        self.assertEqual(audio_timestamp(packet, 2), 12345)
        self.assertEqual(unpack_audio(packet, 2), b"opus-fixture")
        for version in (1, 3):
            self.assertEqual(audio_timestamp(pack_audio(b"fixture", version), version), 0)

    def test_real_canceller_attenuates_echo_and_preserves_overlap(self):
        rng = random.Random(7)
        echo = EchoCanceller()
        previous = [0] * SAMPLES
        echo_energy = residual = overlap_energy = 0
        try:
            for index in range(160):
                far = [rng.randint(-8000, 8000) for _ in range(SAMPLES)]
                delayed = previous[-160:] + far[:-160]  # 10 ms acoustic path.
                near = [int(0.5 * x) for x in delayed]
                if index >= 120:
                    near = [n + int(2500 * math.sin((index * SAMPLES + i) * 2 * math.pi * 311 / 16000)) for i, n in enumerate(near)]
                echo.remember_playback(index + 1, pcm(far))
                cleaned = samples(echo.process(pcm(near), index + 1))
                if 80 <= index < 120:
                    echo_energy += sum(n*n for n in near)
                    residual += sum(n*n for n in cleaned)
                if index >= 140:
                    overlap_energy += sum(n*n for n in cleaned)
                previous = far
            self.assertGreater(10 * math.log10(echo_energy / max(1, residual)), 15)
            self.assertGreater(math.sqrt(overlap_energy / (20 * SAMPLES)), 500)
            self.assertLessEqual(len(echo.references), 100)
        finally:
            echo.close()
            echo.close()

    def test_missing_or_replayed_reference_does_not_feed_echo(self):
        echo = EchoCanceller()
        try:
            self.assertEqual(echo.process(pcm([1000] * SAMPLES), 99), SILENCE)
            echo.remember_playback(1, SILENCE)
            echo.process(SILENCE, 1)
            self.assertEqual(echo.process(pcm([1000] * SAMPLES), 1), SILENCE)
            self.assertEqual(echo.counts["reference_missing"], 2)
        finally:
            echo.close()


if __name__ == "__main__":
    unittest.main()

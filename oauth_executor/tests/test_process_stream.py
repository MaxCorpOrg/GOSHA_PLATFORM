from __future__ import annotations

import subprocess
import sys
import unittest

from oauth_shared.process_stream import collect_process_output


class CollectProcessOutputTests(unittest.TestCase):
    def test_collects_lines_and_forwards_callback(self) -> None:
        callback_lines: list[str] = []
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "print('first'); print('second')",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        lines = collect_process_output(
            process,
            timeout_seconds=5,
            on_line=callback_lines.append,
        )

        self.assertEqual(lines, ["first", "second"])
        self.assertEqual(callback_lines, ["first", "second"])
        self.assertEqual(process.returncode, 0)

    def test_kills_silent_process_after_timeout(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        with self.assertRaises(subprocess.TimeoutExpired):
            collect_process_output(process, timeout_seconds=0.2)

        process.wait(timeout=5)
        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(process.returncode, 0)

    def test_timeout_covers_blocked_stdin_write(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        with self.assertRaises(subprocess.TimeoutExpired):
            collect_process_output(
                process,
                timeout_seconds=0.2,
                stdin_text="x" * (1024 * 1024),
            )

        process.wait(timeout=5)
        self.assertIsNotNone(process.returncode)
        self.assertNotEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()

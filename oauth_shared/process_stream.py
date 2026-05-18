from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable


def collect_process_output(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
    on_line: Callable[[str], None] | None = None,
) -> list[str]:
    if process.stdout is None:
        raise ValueError("У процесса не настроен stdout для чтения.")

    output_lines: list[str] = []

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                output_lines.append(line)
                if on_line is not None:
                    on_line(line)
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    reader = threading.Thread(
        target=_reader,
        name="oauth-codex-process-reader",
        daemon=True,
    )
    reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        finally:
            reader.join(timeout=5)
        raise

    reader.join(timeout=5)
    if reader.is_alive():
        try:
            process.stdout.close()
        except Exception:
            pass
        reader.join(timeout=1)
    return output_lines

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable


def _close_stream(stream: object | None) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except Exception:
        pass


def collect_process_output(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float,
    on_line: Callable[[str], None] | None = None,
    stdin_text: str | None = None,
) -> list[str]:
    if process.stdout is None:
        raise ValueError("У процесса не настроен stdout для чтения.")

    output_lines: list[str] = []
    stdin_errors: list[BaseException] = []

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                output_lines.append(line)
                if on_line is not None:
                    on_line(line)
        finally:
            _close_stream(process.stdout)

    def _writer() -> None:
        if process.stdin is None:
            return
        try:
            if stdin_text is not None:
                process.stdin.write(stdin_text)
        except (BrokenPipeError, ValueError) as exc:
            if process.poll() is None:
                stdin_errors.append(exc)
        finally:
            _close_stream(process.stdin)

    reader = threading.Thread(
        target=_reader,
        name="oauth-codex-process-reader",
        daemon=True,
    )
    reader.start()
    writer = threading.Thread(
        target=_writer,
        name="oauth-codex-process-writer",
        daemon=True,
    )
    writer.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        finally:
            writer.join(timeout=5)
            reader.join(timeout=5)
        raise

    writer.join(timeout=5)
    if writer.is_alive():
        _close_stream(process.stdin)
        writer.join(timeout=1)
    if writer.is_alive():
        raise RuntimeError("Не удалось завершить запись в stdin дочернего процесса.")
    if stdin_errors:
        raise stdin_errors[0]

    reader.join(timeout=5)
    if reader.is_alive():
        _close_stream(process.stdout)
        reader.join(timeout=1)
    return output_lines

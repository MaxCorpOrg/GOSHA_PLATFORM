from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from oauth_shared.process_stream import collect_process_output


class CodexReviewError(RuntimeError):
    pass


def _normalize_codex_log_line(line: str) -> str:
    text = str(line or "").rstrip()
    if not text:
        return ""
    ignored_fragments = (
        "WARN codex_core_plugins::manifest: ignoring interface.defaultPrompt",
        "WARN codex_core_skills::loader: ignoring interface.icon_small",
        "WARN codex_core_skills::loader: ignoring interface.icon_large",
    )
    if any(fragment in text for fragment in ignored_fragments):
        return ""
    if "stream disconnected - retrying sampling request" in text:
        return "Временный обрыв потока ответа модели. Codex повторяет запрос."
    return text


def _resolve_codex_executable(codex_command: str) -> str:
    executable = codex_command
    if os.path.sep not in codex_command:
        resolved = shutil.which(codex_command)
        if not resolved:
            raise CodexReviewError(f"Не найден локальный исполняемый файл Codex: {codex_command}")
        executable = resolved
    elif not Path(codex_command).exists():
        raise CodexReviewError(f"Не найден локальный исполняемый файл Codex: {codex_command}")
    return executable


def codex_login_status(codex_command: str) -> dict[str, str | bool]:
    try:
        executable = _resolve_codex_executable(codex_command)
    except CodexReviewError:
        return {
            "available": False,
            "logged_in": False,
            "status_text": "Codex CLI не найден",
        }

    try:
        result = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return {
            "available": True,
            "logged_in": False,
            "status_text": f"Не удалось проверить вход Codex CLI: {exc}",
        }

    status_text = (result.stdout or result.stderr or "").strip()
    logged_in = result.returncode == 0 and "Logged in" in status_text
    return {
        "available": True,
        "logged_in": logged_in,
        "status_text": status_text or "Статус входа Codex CLI не получен",
    }


def generate_review_markdown_via_codex(
    *,
    codex_command: str,
    repo_path: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    profile: str,
    timeout_seconds: int,
    log_cb=None,
) -> str:
    executable = _resolve_codex_executable(codex_command)
    if not repo_path.exists():
        raise CodexReviewError(f"Не найден локальный репозиторий reviewer: {repo_path}")

    with tempfile.TemporaryDirectory(prefix="oauth-reviewer-codex-") as temp_dir:
        last_message_path = Path(temp_dir) / "last_message.txt"
        command = [
            executable,
            "exec",
            "--cd",
            str(repo_path),
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--output-last-message",
            str(last_message_path),
            "-",
        ]
        if model:
            command.extend(["--model", model])
        if reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        if profile:
            command.extend(["--profile", profile])

        if log_cb is not None:
            log_cb("$ " + shlex.join(command))

        process = subprocess.Popen(
            command,
            cwd=str(repo_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        output_lines: list[str] = []

        def _on_line(line: str) -> None:
            text = _normalize_codex_log_line(line)
            if not text:
                return
            output_lines.append(text)
            if log_cb is not None:
                log_cb(text)

        try:
            collect_process_output(
                process,
                timeout_seconds=timeout_seconds,
                on_line=_on_line,
                stdin_text=prompt,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexReviewError(
                f"Локальный Codex reviewer не уложился в лимит {timeout_seconds} секунд."
            ) from exc

        if process.returncode != 0:
            tail = "\n".join(output_lines[-30:]).strip()
            suffix = f"\nПоследние строки вывода:\n{tail}" if tail else ""
            raise CodexReviewError(
                f"Локальный Codex reviewer завершился с кодом {process.returncode}.{suffix}"
            )

        if not last_message_path.exists():
            raise CodexReviewError("Codex reviewer не записал итоговое сообщение review.")

        review_markdown = last_message_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not review_markdown:
            raise CodexReviewError("Codex reviewer вернул пустой итоговый review.")
        return review_markdown

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class CodexReviewError(RuntimeError):
    pass


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


def _normalize_process_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="ignore")
    return output


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
    profile: str,
    timeout_seconds: int,
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
        if profile:
            command.extend(["--profile", profile])

        try:
            result = subprocess.run(
                command,
                cwd=str(repo_path),
                input=prompt,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output_text = _normalize_process_output(getattr(exc, "stdout", None) or getattr(exc, "output", None))
            tail = "\n".join(output_text.splitlines()[-30:]).strip()
            suffix = f"\nПоследние строки вывода:\n{tail}" if tail else ""
            raise CodexReviewError(
                f"Локальный Codex reviewer не уложился в лимит {timeout_seconds} секунд.{suffix}"
            ) from exc

        output_text = _normalize_process_output(result.stdout)
        output_lines = [line.rstrip() for line in output_text.splitlines()]

        if result.returncode != 0:
            tail = "\n".join(output_lines[-30:]).strip()
            suffix = f"\nПоследние строки вывода:\n{tail}" if tail else ""
            raise CodexReviewError(
                f"Локальный Codex reviewer завершился с кодом {result.returncode}.{suffix}"
            )

        if not last_message_path.exists():
            raise CodexReviewError("Codex reviewer не записал итоговое сообщение review.")

        review_markdown = last_message_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not review_markdown:
            raise CodexReviewError("Codex reviewer вернул пустой итоговый review.")
        return review_markdown

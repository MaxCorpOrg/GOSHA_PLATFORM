from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from oauth_shared.process_stream import collect_process_output


class CodexExecutionError(RuntimeError):
    pass


COMMIT_MESSAGE_FILE = ".codex_executor_commit_message.txt"
SUMMARY_FILE = ".codex_executor_summary.md"
LAST_MESSAGE_FILE = ".codex_executor_last_message.txt"


def build_executor_prompt(
    *,
    repo_full_name: str,
    pr_payload: dict,
    agents_sections: list[tuple[str, str]],
    reviews: list[dict],
    review_comments: list[dict],
    review_scope_note: str,
    validate_command: str,
) -> str:
    guidance_blocks = []
    for relative_path, text in agents_sections:
        snippet = text.strip()
        if len(snippet) > 10000:
            snippet = snippet[:10000] + "\n...[AGENTS truncated]..."
        guidance_blocks.append(f"Файл правил: {relative_path}\n{snippet}")

    def _render_comments(items: list[dict], *, body_key: str = "body", location_keys: tuple[str, ...] = ()) -> str:
        blocks = []
        for item in items[-25:]:
            user = ((item.get("user") or {}).get("login") or "").strip()
            body = str(item.get(body_key, "") or "").strip()
            if not body:
                continue
            location = " ".join(str(item.get(key, "") or "").strip() for key in location_keys).strip()
            header = f"Автор: {user or 'unknown'}"
            if location:
                header += f" | {location}"
            blocks.append(f"{header}\n{body}")
        return "\n\n---\n\n".join(blocks) if blocks else "[пусто]"

    return (
        "Ты локальный исполнительный агент Codex.\n"
        "Нужно автоматически исправить замечания по Pull Request в текущей ветке рабочей копии.\n"
        "Пиши и думай по-русски.\n"
        "Не задавай вопросов. Работай до конца.\n"
        "Не делай merge. Не создавай новую ветку. Не делай push сам — это сделает внешний сервис после твоей работы.\n"
        "Не трогай защищённую базовую ветку.\n"
        "Изменения должны быть узкими и только по замечаниям.\n"
        "После правок обязательно проверь релевантные команды в рабочей копии. Минимально доступная команда проверки:\n"
        f"{validate_command}\n"
        "Когда закончишь:\n"
        f"1. Запиши короткое сообщение коммита в файл `{COMMIT_MESSAGE_FILE}`.\n"
        f"2. Запиши краткое резюме выполненных правок и оставшихся рисков в файл `{SUMMARY_FILE}`.\n"
        f"3. Не коммить и не отправляй изменения сам.\n\n"
        f"Репозиторий: {repo_full_name}\n"
        f"PR: #{pr_payload.get('number')}\n"
        f"Заголовок: {pr_payload.get('title', '')}\n"
        f"Описание:\n{str(pr_payload.get('body', '') or '[пусто]').strip()}\n\n"
        f"Область замечаний для этого прогона:\n{review_scope_note}\n\n"
        "Правила проекта:\n"
        + ("\n\n".join(guidance_blocks) if guidance_blocks else "[AGENTS.md не найден]") +
        "\n\nReview-сводки:\n"
        + _render_comments(reviews) +
        "\n\nInline-комментарии по строкам:\n"
        + _render_comments(review_comments, location_keys=("path", "line", "original_line"))
    )


def run_codex_exec(
    *,
    codex_command: str,
    worktree_path: Path,
    prompt: str,
    model: str,
    profile: str,
    timeout_seconds: int,
    log_cb,
) -> str:
    executable = codex_command
    if os.path.sep not in codex_command:
        resolved = shutil.which(codex_command)
        if not resolved:
            raise CodexExecutionError(f"Не найден локальный исполняемый файл Codex: {codex_command}")
        executable = resolved
    elif not Path(codex_command).exists():
        raise CodexExecutionError(f"Не найден локальный исполняемый файл Codex: {codex_command}")

    last_message_path = worktree_path / LAST_MESSAGE_FILE
    command = [
        executable,
        "exec",
        "--cd",
        str(worktree_path),
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "--output-last-message",
        str(last_message_path),
    ]
    if model:
        command.extend(["--model", model])
    if profile:
        command.extend(["--profile", profile])
    command.append("-")

    process = subprocess.Popen(
        command,
        cwd=str(worktree_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(prompt)
    process.stdin.close()

    try:
        collect_process_output(
            process,
            timeout_seconds=timeout_seconds,
            on_line=log_cb,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodexExecutionError(
            f"Локальный Codex не уложился в лимит {timeout_seconds} секунд."
        ) from exc

    if process.returncode != 0:
        raise CodexExecutionError(f"Локальный Codex завершился с кодом {process.returncode}.")

    if last_message_path.exists():
        last_message = last_message_path.read_text(encoding="utf-8", errors="ignore").strip()
        last_message_path.unlink(missing_ok=True)
        return last_message
    return ""

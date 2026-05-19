from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OpenAIReviewError(RuntimeError):
    pass


def _extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
    raise OpenAIReviewError("Не удалось извлечь текст review из ответа OpenAI.")


def _truncate_patch(text: str, limit: int) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "\n...[patch truncated]..."


def build_review_prompt(
    *,
    repo_full_name: str,
    pr_payload: dict[str, Any],
    file_payloads: list[dict[str, Any]],
    agents_sections: list[tuple[str, str]],
    max_patch_chars_per_file: int,
    max_total_patch_chars: int,
) -> str:
    title = str(pr_payload.get("title", "") or "").strip()
    body = str(pr_payload.get("body", "") or "").strip()
    base_branch = str((pr_payload.get("base") or {}).get("ref", "") or "").strip()
    head_branch = str((pr_payload.get("head") or {}).get("ref", "") or "").strip()

    remaining = max(1000, max_total_patch_chars)
    file_blocks: list[str] = []
    changed_files: list[str] = []
    for item in file_payloads:
        filename = str(item.get("filename", "") or "").strip()
        if not filename:
            continue
        changed_files.append(filename)
        patch = _truncate_patch(str(item.get("patch", "") or ""), max_patch_chars_per_file)
        if len(patch) > remaining:
            patch = _truncate_patch(patch, max(0, remaining))
        block = (
            f"Файл: {filename}\n"
            f"Статус: {item.get('status', 'modified')}\n"
            f"Добавлено строк: {item.get('additions', 0)}\n"
            f"Удалено строк: {item.get('deletions', 0)}\n"
            f"Patch:\n{patch or '[patch отсутствует или не был передан GitHub API]'}"
        )
        file_blocks.append(block)
        remaining -= len(patch)
        if remaining <= 0:
            file_blocks.append("Остальные patch-блоки опущены из-за лимита контекста.")
            break

    guidance_blocks = []
    for relative_path, text in agents_sections:
        snippet = text.strip()
        if len(snippet) > 12000:
            snippet = snippet[:12000] + "\n...[AGENTS truncated]..."
        guidance_blocks.append(f"Файл правил: {relative_path}\n{snippet}")

    return (
        "Проанализируй Pull Request как строгий reviewer проекта GOSHA_PLATFORM.\n"
        "Пиши только по-русски.\n"
        "Фокусируйся на серьёзных рисках: безопасность, OAuth, утечки секретов, регрессии API, поломка CI/архитектуры,\n"
        "нарушение review-правил из AGENTS.md, отсутствие обязательного обновления документации.\n"
        "Не засоряй ответ мелочами. Если серьёзных замечаний нет, прямо напиши, что критичных P0/P1 замечаний не найдено.\n\n"
        "Формат ответа:\n"
        "## Итог\n"
        "короткий вывод\n\n"
        "## Замечания\n"
        "1. [P0|P1] Заголовок\n"
        "Файл: path[:line] или не привязано\n"
        "Почему это риск: ...\n"
        "Что исправить: ...\n\n"
        f"Репозиторий: {repo_full_name}\n"
        f"PR: #{pr_payload.get('number')}\n"
        f"Base branch: {base_branch}\n"
        f"Head branch: {head_branch}\n"
        f"Заголовок: {title}\n"
        f"Описание PR:\n{body or '[пусто]'}\n\n"
        "Правила репозитория из AGENTS.md:\n"
        + ("\n\n".join(guidance_blocks) if guidance_blocks else "[AGENTS.md не найден]") +
        "\n\nИзменённые файлы и patch:\n" +
        ("\n\n---\n\n".join(file_blocks) if file_blocks else "[GitHub не вернул список файлов PR]")
    )


def generate_review_markdown(
    *,
    openai_api_key: str,
    openai_base_url: str,
    openai_model: str,
    prompt: str,
) -> str:
    body = {
        "model": openai_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты опытный code reviewer. Смотри только на реальные риски и нарушения правил репозитория. "
                    "Пиши кратко, предметно и только по-русски."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
    }
    request = Request(
        f"{openai_base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
        except Exception:
            raw = exc.reason or ""
        raise OpenAIReviewError(f"OpenAI API вернул HTTP {exc.code}: {raw}") from exc
    except URLError as exc:
        raise OpenAIReviewError(f"Не удалось обратиться к OpenAI API: {exc.reason}") from exc

    return _extract_message_text(payload)

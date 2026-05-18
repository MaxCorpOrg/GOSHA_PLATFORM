from __future__ import annotations

from pathlib import Path


def ensure_repo_allowed(repo_full_name: str, allowed_repos: tuple[str, ...]) -> None:
    if not allowed_repos:
        raise ValueError(
            "Разрешённый список репозиториев пуст. "
            "Это ошибка конфигурации: в allow-list должен быть указан хотя бы один репозиторий."
        )
    if allowed_repos and repo_full_name not in allowed_repos:
        allowed = ", ".join(allowed_repos)
        raise ValueError(f"Репозиторий {repo_full_name} не входит в разрешённый список. Разрешены: {allowed}")


def collect_relevant_agents(repo_root: Path, changed_files: list[str]) -> list[tuple[str, str]]:
    if not repo_root.exists():
        return []
    seen: set[Path] = set()
    collected: list[tuple[str, str]] = []

    root_agents = repo_root / "AGENTS.md"
    if root_agents.exists():
        seen.add(root_agents)
        collected.append((str(root_agents.relative_to(repo_root)), root_agents.read_text(encoding="utf-8", errors="ignore")))

    for relative_name in changed_files:
        relative_path = Path(relative_name)
        current = (repo_root / relative_path).parent
        while True:
            candidate = current / "AGENTS.md"
            if candidate.exists() and candidate not in seen:
                seen.add(candidate)
                collected.append((str(candidate.relative_to(repo_root)), candidate.read_text(encoding="utf-8", errors="ignore")))
            if current == repo_root:
                break
            current = current.parent
            if repo_root not in current.parents and current != repo_root:
                break
    return collected

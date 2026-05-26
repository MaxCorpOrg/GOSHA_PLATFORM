from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path


TERMINAL_CANDIDATES = (
    "gnome-terminal",
    "x-terminal-emulator",
    "konsole",
    "xfce4-terminal",
    "kitty",
    "alacritty",
    "xterm",
)


class LocalTerminalMonitor:
    def __init__(
        self,
        *,
        title: str,
        enabled: bool,
        runtime_root: Path,
        preferred_terminal_command: str = "",
    ) -> None:
        self.title = title.strip() or "GOSHA Codex Task"
        self.enabled = enabled
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        slug = "".join(ch if ch.isalnum() else "-" for ch in self.title.lower()).strip("-") or "task"
        self.runtime_dir = Path(tempfile.mkdtemp(prefix=f"{timestamp}-{slug}-", dir=str(self.runtime_root)))
        self.log_path = self.runtime_dir / "live.log"
        self.done_path = self.runtime_dir / "done.flag"
        self.monitor_script_path = self.runtime_dir / "monitor.sh"
        self.terminal_command = self._resolve_terminal(preferred_terminal_command)
        self.started = False
        self.launched = False

    def start(self) -> str:
        if self.started:
            return "Терминал-наблюдатель уже подготовлен."
        self.started = True
        self.log_path.touch()
        if not self.enabled:
            return f"Локальный терминал-наблюдатель отключён в настройках. Журнал: {self.log_path}"
        if not self.terminal_command:
            return f"Не найден поддерживаемый терминал для локального наблюдения. Журнал: {self.log_path}"
        launch_env = self._launch_environment()
        if not (launch_env.get("DISPLAY") or launch_env.get("WAYLAND_DISPLAY")):
            return f"Нет графической сессии для открытия локального терминала. Журнал: {self.log_path}"
        self._write_monitor_script()
        self._launch_terminal(launch_env=launch_env)
        self.launched = True
        return f"Открыт локальный терминал-наблюдатель: {self.title}. Журнал: {self.log_path}"

    def append(self, text: str) -> None:
        line = str(text or "").rstrip()
        if not line:
            return
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def finish(self, status_text: str) -> None:
        self.append(status_text)
        self.done_path.write_text(str(status_text or "").strip(), encoding="utf-8")

    def _resolve_terminal(self, preferred_terminal_command: str) -> str:
        candidates: list[str] = []
        preferred = str(preferred_terminal_command or "").strip()
        if preferred:
            candidates.append(preferred)
        candidates.extend(TERMINAL_CANDIDATES)
        for candidate in candidates:
            path = shutil_which(candidate)
            if path:
                return path
        return ""

    def _write_monitor_script(self) -> None:
        script = f"""#!/usr/bin/env bash
set -euo pipefail
LOG_FILE={shlex.quote(str(self.log_path))}
DONE_FILE={shlex.quote(str(self.done_path))}
printf 'Наблюдение за задачей: {self.title}\\n\\n'
touch "$LOG_FILE"
tail -n +1 -F "$LOG_FILE" &
TAIL_PID=$!
while [[ ! -f "$DONE_FILE" ]]; do
  sleep 1
done
sleep 0.5
kill "$TAIL_PID" 2>/dev/null || true
wait "$TAIL_PID" 2>/dev/null || true
printf '\\n[monitor] Задача завершена. Окно можно закрыть.\\n'
exec bash
"""
        self.monitor_script_path.write_text(script, encoding="utf-8")
        os.chmod(self.monitor_script_path, 0o755)

    def _launch_terminal(self, *, launch_env: dict[str, str]) -> None:
        command = self._terminal_invocation()
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=launch_env,
            start_new_session=True,
        )

    def _terminal_invocation(self) -> list[str]:
        title = self.title
        script = str(self.monitor_script_path)
        terminal = self.terminal_command
        if terminal.endswith("gnome-terminal") or terminal.endswith("x-terminal-emulator"):
            return [terminal, "--title", title, "--", "bash", "-lc", script]
        if terminal.endswith("konsole"):
            return [terminal, "--noclose", "-p", f"tabtitle={title}", "-e", "bash", "-lc", script]
        if terminal.endswith("xfce4-terminal"):
            return [terminal, "--title", title, "--hold", "-e", f"bash -lc {shlex.quote(script)}"]
        if terminal.endswith("kitty"):
            return [terminal, "--title", title, "bash", "-lc", script]
        if terminal.endswith("alacritty"):
            return [terminal, "--title", title, "-e", "bash", "-lc", script]
        if terminal.endswith("xterm"):
            return [terminal, "-T", title, "-e", "bash", "-lc", script]
        return [terminal, "bash", "-lc", script]

    def _launch_environment(self) -> dict[str, str]:
        env = dict(os.environ)
        wanted_keys = (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
        )
        if all(env.get(key) for key in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS")):
            return env
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show-environment"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                for raw_line in result.stdout.splitlines():
                    if "=" not in raw_line:
                        continue
                    key, value = raw_line.split("=", 1)
                    if key in wanted_keys and value and not env.get(key):
                        env[key] = value
        except Exception:
            pass
        return env


def shutil_which(command: str) -> str:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        path = Path(directory) / command
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return ""

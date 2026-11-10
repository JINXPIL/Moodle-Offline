"""Android (Termux): определение платформы, открытие страниц и файлов, уведомления, «не засыпать», автозапуск при включении
телефона (Termux:Boot) и папка для материалов. Только стандартная библиотека.

Программа на телефоне — это та же Python-программа, запущенная в Termux: сервер на 127.0.0.1, а окно — это браузер телефона.
Все вызовы внешних команд безопасны: нет команды (нет Termux:API) — функция просто возвращает False.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

BOOT_NAME = "moodle-offline-watch.sh"           # скрипт для Termux:Boot (запускается при включении телефона)
TERMUX_PREFIX = "/data/data/com.termux/files/usr"


def is_android(env=None, platform: str | None = None) -> bool:
    """Запущено ли на Android (Termux или другой Python под Android)."""
    env = os.environ if env is None else env
    plat = sys.platform if platform is None else platform
    return bool(plat == "android" or env.get("TERMUX_VERSION") or "com.termux" in (env.get("PREFIX") or "")
                or (env.get("ANDROID_ROOT") and env.get("ANDROID_DATA")))


def _run(cmd: list[str], timeout: float = 10) -> bool:
    """Запустить внешнюю команду; нет команды, ошибка или зависание (нет приложения Termux:API) → False."""
    if not shutil.which(cmd[0]):
        return False
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def open_url(url: str) -> bool:
    """Открыть адрес в браузере телефона."""
    if not url.startswith(("http://", "https://")):
        return False
    return _run(["termux-open-url", url]) or _run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url])


def open_file(path) -> bool:
    """Открыть файл в подходящем приложении телефона."""
    return _run(["termux-open", str(path)])


def notify(title: str, text: str) -> bool:
    """Уведомление Android (нужны приложение Termux:API и пакет termux-api)."""
    return _run(["termux-notification", "--title", title, "--content", text, "--group", "moodle-offline"], timeout=15)


def wake_lock(on: bool) -> bool:
    """Не давать телефону «усыпить» Termux (иначе проверки и автообновление ночью не идут)."""
    return _run(["termux-wake-lock" if on else "termux-wake-unlock"], timeout=5)


# ── папки ────────────────────────────────────────────────────────────────────
def shared_storage(home: Path) -> Path | None:
    """~/storage/shared — общая память телефона (появляется после `termux-setup-storage`)."""
    p = Path(home) / "storage" / "shared"
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def default_materials(home: Path, name: str) -> Path:
    """Куда класть материалы по умолчанию: в общую память (видно в приложении «Файлы»), а если доступа нет — в папку Termux."""
    sh = shared_storage(home)
    return (sh / "Documents" / name) if sh else (Path(home) / name)


def in_private_storage(path: Path, home: Path) -> bool:
    """Лежит ли папка внутри закрытой области Termux (другие приложения её не видят)."""
    try:
        Path(path).resolve().relative_to(Path(home).resolve())
        return True
    except (ValueError, OSError):
        return False


# ── автозапуск при включении телефона ────────────────────────────────────────
def boot_dir(home: Path) -> Path:
    return Path(home) / ".termux" / "boot"


def boot_path(home: Path) -> Path:
    return boot_dir(home) / BOOT_NAME


def boot_script(cmd: list[str], prefix: str | None = None) -> str:
    """Скрипт для Termux:Boot: не засыпать и запустить слежение."""
    prefix = prefix or os.environ.get("PREFIX") or TERMUX_PREFIX
    return (f"#!{prefix}/bin/sh\n"
            "# Moodle Offline: слежение за Moodle и автообновление после включения телефона. Удалите файл, чтобы отключить.\n"
            "termux-wake-lock\n"
            "exec " + " ".join(shlex.quote(str(c)) for c in cmd) + "\n")

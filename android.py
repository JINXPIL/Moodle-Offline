"""Android (Termux): определение платформы, открытие страниц и файлов, уведомления, «не засыпать», автозапуск при включении
телефона (Termux:Boot) и папка для материалов. Только стандартная библиотека.

Программа на телефоне — это та же Python-программа, а окно — браузер телефона или (в .apk) встроенный WebView. Два способа запуска:
  • Termux: сервер на 127.0.0.1, внешние команды termux-*;
  • приложение .apk (Chaquopy): Python внутри приложения, вместо команд — Java-класс Bridge (флаг MOODLE_OFFLINE_APK=1 ставит apk_main.py).
Все вызовы безопасны: нет команды или моста — функция просто возвращает False.
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


BRIDGE_CLASS = "ru.sevsu.moodleoffline.Bridge"   # Java-класс из android-app (см. Bridge.java)


def is_apk(env=None) -> bool:
    """Запущено ли внутри приложения .apk (Python встроен в приложение, есть мост Bridge)."""
    env = os.environ if env is None else env
    return env.get("MOODLE_OFFLINE_APK") == "1"


def is_android(env=None, platform: str | None = None) -> bool:
    """Запущено ли на Android (приложение .apk, Termux или другой Python под Android)."""
    env = os.environ if env is None else env
    plat = sys.platform if platform is None else platform
    return bool(is_apk(env) or plat == "android" or env.get("TERMUX_VERSION") or "com.termux" in (env.get("PREFIX") or "")
                or (env.get("ANDROID_ROOT") and env.get("ANDROID_DATA")))


def _bridge():
    from java import jclass          # есть только внутри приложения (Chaquopy)
    return jclass(BRIDGE_CLASS)


def _call(name: str, *args, default=False):
    """Вызвать метод моста Bridge; любая ошибка (нет моста, нет разрешения) → значение по умолчанию."""
    try:
        return getattr(_bridge(), name)(*args)
    except Exception:
        return default


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
    if is_apk():
        return bool(_call("openUrl", url))
    return _run(["termux-open-url", url]) or _run(["am", "start", "-a", "android.intent.action.VIEW", "-d", url])


def open_file(path) -> bool:
    """Открыть файл в подходящем приложении телефона."""
    if is_apk():
        return bool(_call("openFile", str(path)))
    return _run(["termux-open", str(path)])


def notify(title: str, text: str) -> bool:
    """Уведомление Android (в .apk — своё; в Termux нужны приложение Termux:API и пакет termux-api)."""
    if is_apk():
        return bool(_call("notify", title, text))
    return _run(["termux-notification", "--title", title, "--content", text, "--group", "moodle-offline"], timeout=15)


def wake_lock(on: bool) -> bool:
    """Не давать телефону «усыпить» программу (иначе проверки и автообновление ночью не идут)."""
    if is_apk():
        return bool(_call("setWakeLock", bool(on)))
    return _run(["termux-wake-lock" if on else "termux-wake-unlock"], timeout=5)


# ── папки ────────────────────────────────────────────────────────────────────
def shared_storage(home: Path) -> Path | None:
    """Общая память телефона. Termux: ~/storage/shared (после `termux-setup-storage`); .apk: корень памяти, если выдан доступ ко всем файлам."""
    if is_apk():
        if not _call("hasAllFilesAccess"):
            return None
        root = str(_call("sharedRoot", default="") or "")
        return Path(root) if root else None
    p = Path(home) / "storage" / "shared"
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def default_materials(home: Path, name: str) -> Path:
    """Куда класть материалы по умолчанию: в общую память (видно в приложении «Файлы»), а если доступа нет — в папку программы."""
    if is_apk():
        d = str(_call("materialsDir", name, default="") or "")
        if d:
            return Path(d)
    sh = shared_storage(home)
    return (sh / "Documents" / name) if sh else (Path(home) / name)


def in_private_storage(path: Path, home: Path) -> bool:
    """Лежит ли папка внутри закрытой области программы (другие приложения её не видят)."""
    if is_apk():
        root = shared_storage(home)
        if root is None:
            return True
        try:
            rel = Path(path).resolve().relative_to(Path(root).resolve())
        except (ValueError, OSError):
            return True
        return rel.parts[:2] == ("Android", "data")      # папка приложения внутри общей памяти «Файлы» не показывают
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


# ── только в приложении .apk: разрешения и автозапуск ────────────────────────
def request_all_files() -> bool:
    """Открыть системный экран «доступ ко всем файлам» (нужен, чтобы материалы лежали в «Документах»)."""
    return bool(is_apk() and _call("requestAllFilesAccess"))


def request_battery() -> bool:
    """Открыть системное окно «не ограничивать работу в фоне» (иначе Android усыпляет слежение ночью)."""
    return bool(is_apk() and _call("requestBatteryExemption"))


def battery_exempt() -> bool:
    return bool(is_apk() and _call("isBatteryExempt"))


def apk_autostart_get() -> bool:
    return bool(is_apk() and _call("getAutostart"))


def apk_autostart_set(on: bool) -> bool:
    return bool(is_apk() and _call("setAutostart", bool(on)))

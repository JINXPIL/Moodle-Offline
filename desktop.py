"""Linux и macOS (раздел 7г README): то, что в Windows делают DPAPI, планировщик, всплывающие окна и проводник, —
для остальных компьютерных систем.

Что здесь есть:
  • уведомления: macOS — `osascript`, Linux — `notify-send`;
  • выбор папки: macOS — `osascript` (tkinter там нельзя: он падает в любом потоке, кроме главного), Linux — `kdialog`
    или `zenity`; нет ни того ни другого — `None`, и вызывающий берёт запасной способ;
  • автозапуск слежения при входе: Linux — файл в `~/.config/autostart`, macOS — LaunchAgent в `~/Library/LaunchAgents`;
  • ночная синхронизация: Linux — таймер `systemd --user`, macOS — LaunchAgent с `StartCalendarInterval`;
  • защита от «снапового» Chromium: он не умеет работать с профилем в скрытой папке, и окно-приложение не открывается.

Всё, что получает текст от пользователя или из сети, передаётся аргументами команды (никакой склейки в shell и в скрипт
AppleScript). Команды системы запускаются списком аргументов. Без Moodle-кода и сторонних библиотек.
Проверено на подставных запусках (tests/test_desktop_unit.py); настоящие macOS и systemd здесь не пробовались."""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Moodle Offline"
LABEL_WATCH = "org.moodleoffline.watch"
LABEL_SYNC = "org.moodleoffline.sync"
UNIT_SYNC = "moodle-offline-sync"
DESKTOP_FILE = "moodle-offline-watch.desktop"
RUN_TIMEOUT = 20


def system(platform: str | None = None) -> str:
    """windows | macos | linux (всё остальное «Unix» считаем Linux). Принимает и sys.platform, и уже нормализованный ответ этой же
    функции (`system(system())` даёт то же самое). Android проверяют отдельно, до вызова."""
    p = sys.platform if platform is None else platform
    if p.startswith("win"):                 # «win32» и уже нормализованное «windows»
        return "windows"
    return "macos" if p in ("darwin", "macos") else "linux"     # «darwin» (sys.platform) и уже нормализованное «macos»


def _arg(s, cap: int) -> str:
    """Текст → один аргумент команды: без управляющих символов, не длиннее cap, не начинается с «-» (чтобы не стал параметром)."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s))[:cap]
    return " " + s if s.startswith("-") else s


# ══════════════════════ уведомления ═══════════════════════════
def notify_command(title: str, message: str, plat: str | None = None, which=None) -> list | None:
    which = which or shutil.which
    s = system(plat)
    if s == "macos" and which("osascript"):
        # текст идёт аргументами (`on run argv`), а не внутри скрипта: кавычки и обратные слэши в названии курса ничего не ломают
        return ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", _arg(title, 120), _arg(message, 400)]
    if s == "linux" and which("notify-send"):
        return ["notify-send", "-a", APP_NAME, "--", _arg(title, 120), _arg(message, 400)]
    return None


# ══════════════════════ выбор папки ═══════════════════════════
def folder_dialog_commands(title: str, initial: str, plat: str | None = None, which=None, isdir=None,
                           env=None) -> list[list]:
    """Подходящие команды по порядку предпочтения (в KDE сначала kdialog, в остальных средах — zenity)."""
    which, isdir = which or shutil.which, isdir or os.path.isdir
    s = system(plat)
    init = initial if initial and isdir(initial) else ""
    title = _arg(title, 120)
    out = []
    if s == "macos" and which("osascript"):
        lines = ["on run argv",
                 "set p to choose folder with prompt (item 1 of argv) default location (POSIX file (item 2 of argv))" if init
                 else "set p to choose folder with prompt (item 1 of argv)",
                 "return POSIX path of p", "end run"]
        out.append(["osascript"] + [x for ln in lines for x in ("-e", ln)] + [title] + ([init] if init else []))
    elif s == "linux":
        zen = ["zenity", "--file-selection", "--directory", "--title", title] + (["--filename", init.rstrip("/") + "/"] if init else [])
        kde = ["kdialog", "--title", title, "--getexistingdirectory"] + ([init] if init else [])
        desk = ((os.environ if env is None else env).get("XDG_CURRENT_DESKTOP") or "").upper()
        order = [("kdialog", kde), ("zenity", zen)] if "KDE" in desk else [("zenity", zen), ("kdialog", kde)]
        out += [c for name, c in order if which(name)]
    return out


def pick_folder(title: str, initial: str, plat: str | None = None, which=None, run=None, env=None):
    """Окно выбора папки. → путь; "" — человек отказался; None — подходящей программы нет (тогда берут запасной способ)."""
    which, run = which or shutil.which, run or subprocess.run
    cmds = folder_dialog_commands(title, initial, plat, which, env=env)
    if not cmds:
        return None
    for cmd in cmds:
        try:
            r = run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and (r.stdout or "").strip():
            p = (r.stdout or "").strip().splitlines()[0].strip()
            return p.rstrip("/") or "/"
        err = (r.stderr or "")
        if r.returncode in (1, 255) and ("-128" in err or not err.strip() or "cancel" in err.lower()):
            return ""                                    # «Отмена» (osascript: «User canceled (-128)»)
    return ""


# ══════════════════════ команды запуска ═══════════════════════
def program_command(frozen: bool, exe: str, python: str, script: str, *args: str) -> list[str]:
    """Как запустить программу из автозапуска и планировщика: собранный файл — сам, исходники — Python и moodle_offline.py."""
    return ([exe] if frozen else [python, script]) + list(args)


def parse_hhmm(s) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("время нужно в виде ЧЧ:ММ")
    return int(m.group(1)), int(m.group(2))


def cron_line(hhmm: str, cmd: list[str]) -> str:
    """Строка для crontab — подсказка там, где нет systemd."""
    h, m = parse_hhmm(hhmm)
    return f"{m} {h} * * * " + " ".join(_sh_quote(c) for c in cmd)


def _sh_quote(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9_./:=+@%-]+", s) else "'" + s.replace("'", "'\\''") + "'"


# ══════════════════════ Linux: .desktop и systemd ═════════════
def desktop_exec(cmd: list[str]) -> str:
    """Поле Exec файла .desktop: аргументы с пробелами и спецсимволами — в кавычках, «%» удваивается (спецификация Desktop Entry)."""
    out = []
    for c in cmd:
        c = re.sub(r"[\x00-\x1f\x7f]", " ", str(c)).replace("%", "%%")
        if re.fullmatch(r"[A-Za-z0-9_./:=+@%-]+", c):
            out.append(c)
        else:
            c = c.replace("\\", "\\" * 4)                       # буквальный «\» в кавычках — четыре обратных слэша (спецификация)
            out.append('"' + re.sub(r'(["`$])', r"\\\1", c) + '"')
    return " ".join(out)


def desktop_entry(cmd: list[str]) -> str:
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={APP_NAME}: слежение\nName[ru]={APP_NAME}: слежение\n"
            "Comment=Слежение за Moodle и автообновление без окна. Удалите этот файл, чтобы отключить.\n"
            f"Exec={desktop_exec(cmd)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")


def systemd_quote(arg: str) -> str:
    """Аргумент в ExecStart: пробелы — в кавычках, «%» и «$» удваиваются (иначе systemd подставит свои значения)."""
    a = re.sub(r"[\x00-\x1f\x7f]", " ", str(arg)).replace("%", "%%").replace("$", "$$")
    if re.fullmatch(r"[A-Za-z0-9_./:=+@%$-]+", a):
        return a
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_service(cmd: list[str]) -> str:
    return ("[Unit]\nDescription=Moodle Offline: ночная синхронизация\nWants=network-online.target\nAfter=network-online.target\n\n"
            "[Service]\nType=oneshot\nExecStart=" + " ".join(systemd_quote(c) for c in cmd) + "\n")


def systemd_timer(hhmm: str) -> str:
    h, m = parse_hhmm(hhmm)
    return ("[Unit]\nDescription=Moodle Offline: ночная синхронизация (по времени)\n\n"
            f"[Timer]\nOnCalendar=*-*-* {h:02d}:{m:02d}:00\nPersistent=true\nUnit={UNIT_SYNC}.service\n\n"
            "[Install]\nWantedBy=timers.target\n")


# ══════════════════════ macOS: LaunchAgent ════════════════════
def launch_agent(label: str, cmd: list[str], hhmm: str | None = None, run_at_load: bool = False) -> bytes:
    d = {"Label": label, "ProgramArguments": [str(c) for c in cmd], "ProcessType": "Background", "RunAtLoad": bool(run_at_load)}
    if hhmm:
        h, m = parse_hhmm(hhmm)
        d["StartCalendarInterval"] = {"Hour": h, "Minute": m}
    return plistlib.dumps(d)


# ══════════════════════ пути ══════════════════════════════════
def _config_home(home: Path, env) -> Path:
    x = (env or {}).get("XDG_CONFIG_HOME")
    return Path(x) if x and os.path.isabs(x) else home / ".config"


def autostart_path(plat: str | None, home: Path, env=None) -> Path | None:
    s = system(plat)
    if s == "linux":
        return _config_home(home, env) / "autostart" / DESKTOP_FILE
    if s == "macos":
        return home / "Library" / "LaunchAgents" / (LABEL_WATCH + ".plist")
    return None


def sync_paths(plat: str | None, home: Path, env=None) -> list[Path]:
    s = system(plat)
    if s == "linux":
        d = _config_home(home, env) / "systemd" / "user"
        return [d / (UNIT_SYNC + ".service"), d / (UNIT_SYNC + ".timer")]
    if s == "macos":
        return [home / "Library" / "LaunchAgents" / (LABEL_SYNC + ".plist")]
    return []


def kind_of(plat: str | None = None) -> str:
    return {"linux": "xdg", "macos": "launchd"}.get(system(plat), "windows")


# ══════════════════════ включение и выключение ════════════════
def _write(p: Path, data: str | bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, p)


def _run(run, cmd: list[str]):
    run = run or subprocess.run
    try:
        r = run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        return r.returncode, ((r.stderr or "") + (r.stdout or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(getattr(e, "strerror", "") or e)


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 0


def autostart_set(enabled: bool, cmd: list[str], plat: str | None = None, home: Path | None = None, env=None,
                  run=None) -> dict:
    """Автозапуск слежения при входе в систему. Заработает при следующем входе (сразу не запускаем: окно уже ведёт таймер)."""
    run = run or subprocess.run
    p = autostart_path(plat, Path(home) if home else Path.home(), env)
    if p is None:
        return {"ok": False, "error": "для этой системы автозапуск из окна не предусмотрен"}
    try:
        if enabled:
            if system(plat) == "linux":
                _write(p, desktop_entry(cmd))
            else:
                _write(p, launch_agent(LABEL_WATCH, cmd, run_at_load=True))
        else:
            if system(plat) == "macos":
                _run(run, ["launchctl", "bootout", f"gui/{_uid()}/{LABEL_WATCH}"])      # если уже загружен; нет — не страшно
            if p.exists():
                p.unlink()
    except OSError as e:
        return {"ok": False, "error": str(e.strerror or e)}
    return {"ok": True, "path": str(p)}


def schedule_set(enabled: bool, hhmm: str, cmd: list[str], plat: str | None = None, home: Path | None = None, env=None,
                 run=None, which=None) -> dict:
    """Ночная синхронизация: Linux — таймер systemd --user, macOS — LaunchAgent. Возвращает {"ok", "error"?}."""
    run, which = run or subprocess.run, which or shutil.which
    s = system(plat)
    home = Path(home) if home else Path.home()
    paths = sync_paths(plat, home, env)
    if not paths:
        return {"ok": False, "error": "для этой системы расписание из окна не предусмотрено"}
    try:
        if enabled:
            parse_hhmm(hhmm)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        if s == "linux":
            if not which("systemctl"):
                return {"ok": False, "error": "systemd не найден. Добавьте в crontab строку: " + cron_line(hhmm or "03:10", cmd)}
            if not enabled:
                _run(run, ["systemctl", "--user", "disable", "--now", UNIT_SYNC + ".timer"])
                for p in paths:
                    if p.exists():
                        p.unlink()
                _run(run, ["systemctl", "--user", "daemon-reload"])
                return {"ok": True}
            _write(paths[0], systemd_service(cmd))
            _write(paths[1], systemd_timer(hhmm))
            for step in (["daemon-reload"], ["enable", "--now", UNIT_SYNC + ".timer"]):
                rc, msg = _run(run, ["systemctl", "--user"] + step)
                if rc != 0:
                    return {"ok": False, "error": "systemctl не смог включить таймер: " + (msg or f"код {rc}") +
                            ". Запасной путь — crontab: " + cron_line(hhmm, cmd)}
            return {"ok": True}
        target = f"gui/{_uid()}"
        _run(run, ["launchctl", "bootout", f"{target}/{LABEL_SYNC}"])                    # перезагружаем: время могло смениться
        if not enabled:
            if paths[0].exists():
                paths[0].unlink()
            return {"ok": True}
        _write(paths[0], launch_agent(LABEL_SYNC, cmd, hhmm=hhmm))
        rc, msg = _run(run, ["launchctl", "bootstrap", target, str(paths[0])])
        if rc != 0:
            return {"ok": False, "error": "launchctl не смог загрузить задачу: " + (msg or f"код {rc}")}
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e.strerror or e)}


# ══════════════════════ браузер ═══════════════════════════════
def is_snap_browser(path, realpath=os.path.realpath) -> bool:
    """Chromium из snap (в Ubuntu `chromium-browser` — оболочка над ним) не умеет брать профиль из скрытой папки, и окно-приложение
    не открывается. Такой браузер для окна не берём: откроется обычная вкладка браузера по умолчанию."""
    try:
        p = realpath(str(path))
    except OSError:
        return False
    return p.startswith("/snap/") or os.path.basename(p) == "snap"

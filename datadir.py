"""Где лежат данные программы и как они переезжают при смене версии. Только стандартная библиотека.

Правила для собранного .exe:
  1. переменная MOODLE_OFFLINE_DATA — своя папка данных (всё лежит в ней);
  2. файл portable.txt рядом с .exe — «флешка»: настройки и материалы рядом с программой, как раньше;
  3. иначе — папка пользователя, она не зависит от того, куда вы положили .exe:
       Windows:        %LOCALAPPDATA%\\MoodleOffline
       Linux/Android:  ~/.local/share/moodle-offline
При запуске из исходников всё остаётся рядом с кодом (как раньше), если не задана MOODLE_OFFLINE_DATA.
На Android (Termux) программа всегда работает из исходников, поэтому данные лежат в папке пользователя (~/.local/share/moodle-offline),
а не рядом с кодом: так вход и настройки переживают переустановку папки с программой. Материалы по умолчанию — в общей памяти телефона
(~/storage/shared/Documents/Материалы Moodle, если выдан доступ к памяти), иначе в домашней папке Termux.

При первом запуске новой версии программа сама ищет данные прошлой версии (папку `.moodle_offline`) и КОПИРУЕТ их:
вход, настройки, список курсов для офлайна, расписание, ленту «Что нового». Старая папка не меняется и не удаляется.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import android

LEGACY_DIR = ".moodle_offline"          # так папка данных называлась раньше (и называется в portable-режиме)
LEGACY_OUT = "Материалы Moodle"         # где по умолчанию лежали материалы рядом со старым .exe
PORTABLE_FLAG = "portable.txt"
ENV_DATA = "MOODLE_OFFLINE_DATA"
MARKER = "migrated.json"
HAS_DATA = ("settings.json", "session.bin", "offline.json")   # по этим файлам узнаём «здесь есть данные»
SKIP_DIRS = {"window-profile"}                                  # профиль окна: восстанавливается сам
SKIP_FILES = {"gui.json"}                                       # порт и ключ запущенного окна: чужие и временные
SKIP_PREFIX = ("login_page_",)                                  # диагностика страниц входа
SCAN_DEPTH = 3                                                  # как глубоко искать старую папку в «Загрузках» и т. п.
SCAN_LIMIT = 4000                                               # максимум просмотренных папок (чтобы не зависнуть)


@dataclass
class Plan:
    mode: str            # dev | portable | user | custom
    app_dir: Path
    data_dir: Path
    default_out: Path


# ── куда класть ─────────────────────────────────────────────────────────────
def user_data_root(env=None, home: Path | None = None, os_name: str | None = None) -> Path:
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    if (os_name or os.name) == "nt":
        return Path(env.get("LOCALAPPDATA") or (home / "AppData" / "Local")) / "MoodleOffline"
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / "moodle-offline"


def default_materials(home: Path, on_android: bool = False) -> Path:
    if on_android:
        return android.default_materials(home, LEGACY_OUT)
    docs = home / "Documents"
    return (docs if docs.is_dir() else home) / LEGACY_OUT


def make_plan(app_dir: Path, frozen: bool, env=None, home: Path | None = None, os_name: str | None = None,
              on_android: bool | None = None) -> Plan:
    env = os.environ if env is None else env
    app_dir = Path(app_dir)
    home = Path(home) if home else Path.home()
    on_android = android.is_android(env) if on_android is None else on_android
    custom = (env.get(ENV_DATA) or "").strip()
    if custom:
        d = Path(custom).expanduser()
        return Plan("custom", app_dir, d, d / LEGACY_OUT)
    if on_android and not (app_dir / PORTABLE_FLAG).exists():
        return Plan("user", app_dir, user_data_root(env, home, "posix"), default_materials(home, True))
    if not frozen:
        return Plan("dev", app_dir, app_dir / LEGACY_DIR, app_dir / LEGACY_OUT)
    if (app_dir / PORTABLE_FLAG).exists():
        return Plan("portable", app_dir, app_dir / LEGACY_DIR, app_dir / LEGACY_OUT)
    return Plan("user", app_dir, user_data_root(env, home, os_name), default_materials(home))


# ── поиск старых данных ─────────────────────────────────────────────────────
def has_data(d: Path) -> bool:
    try:
        return d.is_dir() and any((d / n).is_file() for n in HAS_DATA)
    except OSError:
        return False


def _stamp(d: Path) -> float:
    best = 0.0
    for n in HAS_DATA:
        try:
            best = max(best, (d / n).stat().st_mtime)
        except OSError:
            pass
    return best


def _subdirs(p: Path):
    try:
        with os.scandir(p) as it:
            return [Path(e.path) for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def find_legacy(app_dir: Path, home: Path, exclude: Path | None = None, env=None) -> list[Path]:
    """Папки `.moodle_offline` с данными прошлых версий; самая свежая — первой."""
    env = os.environ if env is None else env
    app_dir, home = Path(app_dir), Path(home)
    exclude_r = _resolve(exclude) if exclude else None
    found: dict[Path, Path] = {}

    def consider(d: Path):
        r = _resolve(d)
        if r != exclude_r and r not in found and has_data(d):
            found[r] = d

    consider(app_dir / LEGACY_DIR)                      # новый .exe положили на место старого
    consider(app_dir.parent / LEGACY_DIR)
    for sib in _subdirs(app_dir.parent)[:300]:          # распаковали рядом со старой версией
        consider(sib / LEGACY_DIR)

    roots = [home / n for n in ("Desktop", "Downloads", "Documents")]
    one = (env.get("OneDrive") or "").strip()
    one_roots = [Path(one)] if one else []
    one_roots.append(home / "OneDrive")
    for o in one_roots:
        roots += [o / n for n in ("Desktop", "Downloads", "Documents")]
    roots += [home / "Рабочий стол", home / "Загрузки", home / "Документы"]
    seen_roots: set[Path] = set()
    budget = SCAN_LIMIT
    for root in roots:
        rr = _resolve(root)
        if rr in seen_roots or not root.is_dir():
            continue
        seen_roots.add(rr)
        level = [root]
        for depth in range(SCAN_DEPTH + 1):
            nxt = []
            for d in level:
                budget -= 1
                if budget <= 0:
                    break
                consider(d / LEGACY_DIR)
                if depth == SCAN_DEPTH:
                    continue
                if (d / ".moodle_state.json").exists():      # это папка с материалами: внутри только курсы
                    continue
                nxt += [s for s in _subdirs(d) if not s.name.startswith(".") and s.name not in ("node_modules",)]
            level = nxt
            if budget <= 0 or not level:
                break
    return sorted(found.values(), key=_stamp, reverse=True)


def _resolve(p: Path) -> Path:
    try:
        return Path(p).resolve()
    except OSError:
        return Path(p)


def locate_data_dir(path: Path) -> Path | None:
    """Пользователь указал папку старой версии: это может быть сама `.moodle_offline` или папка с .exe."""
    p = Path(path)
    for cand in (p, p / LEGACY_DIR):
        if has_data(cand):
            return cand
    return None


# ── копирование ─────────────────────────────────────────────────────────────
def copy_data(src: Path, dst: Path, overwrite: bool = False) -> int:
    """Копирует файлы данных (без временных и чужих). Существующие в dst не трогает, если не overwrite."""
    n = 0
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            if f in SKIP_FILES or f.endswith(".tmp") or f.startswith(SKIP_PREFIX):
                continue
            target = dst / rel / f
            if target.exists() and not overwrite:
                continue
            try:
                shutil.copy2(Path(root) / f, target)
                n += 1
            except OSError:
                pass
    return n


def _write_json(path: Path, data: dict):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _keep_materials_in_place(dst: Path, old_app_dir: Path) -> str:
    """Раньше материалы по умолчанию лежали рядом со старым .exe. В настройках пути нет —
    запишем старую папку явно, чтобы новая версия видела уже скачанное и не качала заново."""
    sp = dst / "settings.json"
    try:
        s = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(s, dict) or s.get("out_dir"):
        return ""
    old_out = old_app_dir / LEGACY_OUT
    if old_out.is_dir():
        s["out_dir"] = str(old_out)
        _write_json(sp, s)
        return str(old_out)
    return ""


def import_from(src: Path, plan: Plan, overwrite: bool = False) -> dict:
    """Перенос из указанной папки. → {"from", "files", "out_dir"}; исключение ValueError, если данных нет."""
    s = locate_data_dir(src)
    if s is None:
        raise ValueError("В этой папке нет данных Moodle Offline (ищу `.moodle_offline` с settings.json или session.bin).")
    if _resolve(s) == _resolve(plan.data_dir):
        raise ValueError("Это текущая папка данных — переносить нечего.")
    n = copy_data(s, plan.data_dir, overwrite=overwrite)
    kept = _keep_materials_in_place(plan.data_dir, s.parent) if overwrite or n else ""
    info = {"from": str(s), "files": n, "out_dir": kept}
    _write_json(plan.data_dir / MARKER, {**info, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "seen": False})
    return info


def migrate_if_needed(plan: Plan, home: Path | None = None, env=None) -> dict | None:
    """Первый запуск в режиме «папка пользователя»: если данных ещё нет, а старые есть — копируем.
    → {"from", "files", "out_dir", "others"} или None (ничего не делали)."""
    if plan.mode != "user":
        return None
    if (plan.data_dir / MARKER).exists() or has_data(plan.data_dir):
        return None
    home = Path(home) if home else Path.home()
    cands = find_legacy(plan.app_dir, home, exclude=plan.data_dir, env=env)
    if not cands:
        return None
    src = cands[0]
    n = copy_data(src, plan.data_dir)
    kept = _keep_materials_in_place(plan.data_dir, src.parent)
    info = {"from": str(src), "files": n, "out_dir": kept, "others": [str(c) for c in cands[1:4]]}
    _write_json(plan.data_dir / MARKER, {**info, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "seen": False})
    return info


# ── для окна: показать сообщение один раз ───────────────────────────────────
def pending_notice(data_dir: Path) -> dict | None:
    try:
        m = json.loads((Path(data_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and not m.get("seen") else None


def mark_seen(data_dir: Path):
    p = Path(data_dir) / MARKER
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        m["seen"] = True
        _write_json(p, m)
    except (OSError, ValueError):
        pass

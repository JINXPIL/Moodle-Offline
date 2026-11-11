# -*- coding: utf-8 -*-
"""
Резерв файлов: прежние версии, которые преподаватель заменил на сайте.

Зачем: преподаватель загружает в тот же ресурс новый файл — а то и «заглушку» (крошечный PDF вместо лекции).
Moodle при этом меняет адрес файла, и программа качала новый файл поверх старого — старая лекция пропадала.
Теперь перед заменой старый файл уходит в папку «_Резерв» внутри курса, рядом лежит список (.versions.json).

Без Moodle-кода и без сторонних библиотек: только файлы на диске.

  <папка курса>/_Резерв/<та же подпапка>/<имя> (заменён 2026-11-11 18-30).<расширение>
  <папка курса>/_Резерв/.versions.json   — что, когда и почему сохранено
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

RESERVE_DIR = "_Резерв"
MANIFEST = ".versions.json"
KEEP = 3                    # сколько прежних версий одного файла хранить (старше — удаляем)
STUB_RATIO = 5              # новый файл в столько раз меньше прежнего и меньше — похоже на заглушку
STUB_MIN_OLD = 20 * 1024    # у совсем маленьких файлов сравнивать размер смысла нет


def _fsp(p) -> str:
    s = str(p)
    if os.name == "nt":
        s = os.path.abspath(s)
        if len(s) >= 240 and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
    return s


def hsize(n) -> str:
    n = float(n or 0)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or u == "ГБ":
            return f"{int(n)} {u}" if u == "Б" else f"{n:.1f}".rstrip("0").rstrip(".").replace(".", ",") + f" {u}"
        n /= 1024
    return f"{n:.1f} ГБ"


def stub_reason(old_size: int, new_size: int) -> str:
    """Почему новая версия похожа на заглушку; пустая строка — не похожа."""
    old_size, new_size = int(old_size or 0), int(new_size or 0)
    if old_size > 0 and new_size == 0:
        return "новый файл пустой"
    if old_size >= STUB_MIN_OLD and new_size * STUB_RATIO <= old_size:
        times = old_size / max(new_size, 1)
        return f"новый файл намного меньше прежнего ({hsize(old_size)} → {hsize(new_size)}, в {times:.0f} раз)"
    return ""


# ── список версий ───────────────────────────────────────────────────────────
def _manifest_path(course_dir) -> Path:
    return Path(course_dir) / RESERVE_DIR / MANIFEST


def load(course_dir) -> dict:
    try:
        with open(_fsp(_manifest_path(course_dir)), "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict) or not isinstance(d.get("files"), dict):
        d = {"v": 1, "files": {}}
    return d


def _save(course_dir, d: dict) -> None:
    p = _manifest_path(course_dir)
    os.makedirs(_fsp(p.parent), exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(_fsp(tmp), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(_fsp(tmp), _fsp(p))


def _stamp(now: float | None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y-%m-%d %H-%M")


def _unique(target: Path) -> Path:
    if not os.path.exists(_fsp(target)):
        return target
    for n in range(2, 100):
        cand = target.with_name(f"{target.stem} {n}{target.suffix}")
        if not os.path.exists(_fsp(cand)):
            return cand
    return target.with_name(f"{target.stem} {int(time.time())}{target.suffix}")


def archive(course_dir, rel: str, new_size: int, *, now: float | None = None, keep: int = KEEP) -> dict:
    """Переносит файл course_dir/rel в резерв и записывает в список. Возвращает запись:
    {"file": путь внутри «_Резерв», "saved": "ГГГГ-ММ-ДД чч-мм", "ts", "size": прежний, "new_size", "stub": причина или ""}.
    Не удалось перенести — OSError (вызывающий решает, что делать: качать поверх или нет)."""
    course_dir = Path(course_dir)
    rel_p = PurePosixPath(rel)
    src = course_dir / Path(*rel_p.parts)
    old_size = os.path.getsize(_fsp(src))
    ts = now if now is not None else time.time()
    sub = Path(*rel_p.parent.parts) if rel_p.parent.parts else Path()
    name = rel_p.name
    stem, ext = os.path.splitext(name)
    target = _unique(course_dir / RESERVE_DIR / sub / f"{stem} (заменён {_stamp(ts)}){ext}")
    os.makedirs(_fsp(target.parent), exist_ok=True)
    os.replace(_fsp(src), _fsp(target))
    rec = {"file": target.relative_to(course_dir / RESERVE_DIR).as_posix(), "saved": _stamp(ts), "ts": int(ts),
           "size": old_size, "new_size": int(new_size), "stub": stub_reason(old_size, new_size)}
    d = load(course_dir)
    lst = d["files"].setdefault(rel_p.as_posix(), [])
    lst.append(rec)
    while len(lst) > max(1, keep):                      # самые старые версии выбрасываем
        old = lst.pop(0)
        try:
            os.remove(_fsp(course_dir / RESERVE_DIR / Path(*PurePosixPath(old["file"]).parts)))
        except OSError:
            pass
    _save(course_dir, d)
    return rec


def versions(course_dir) -> dict:
    """{путь файла в курсе: [версии от новой к старой]}; в каждой "path" — путь относительно папки курса.
    Записи, чьих файлов уже нет на диске, пропускаются."""
    d = load(course_dir)
    res = {}
    for rel, lst in d["files"].items():
        ok = []
        for r in lst if isinstance(lst, list) else []:
            f = r.get("file") if isinstance(r, dict) else None
            if not isinstance(f, str) or ".." in PurePosixPath(f).parts:
                continue
            if os.path.isfile(_fsp(Path(course_dir) / RESERVE_DIR / Path(*PurePosixPath(f).parts))):
                ok.append(dict(r, path=f"{RESERVE_DIR}/{f}"))
        if ok:
            res[rel] = sorted(ok, key=lambda r: r.get("ts", 0), reverse=True)
    return res


def total(course_dir) -> tuple[int, int]:
    """(сколько файлов в резерве, сколько байт) — для подписи в настройках."""
    n = size = 0
    for lst in versions(course_dir).values():
        for r in lst:
            n += 1
            size += int(r.get("size") or 0)
    return n, size

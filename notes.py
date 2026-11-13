"""Заметки: к элементам курса и общий блокнот.

Каждая заметка — обычный файл .md в папке «Заметки» внутри папки сохранения: открывается любым редактором, попадает в
офлайн-поиск (папка «Заметки» там — как ещё один курс) и переносится на другое устройство вместе с остальным (каждый файл
отдельно, поэтому заметки с двух устройств не затирают друг друга). Первая строка файла — служебный комментарий с номером
заметки и привязкой к курсу; её можно не трогать. Файл без такой строки (его положили руками) тоже считается заметкой.

Удалённое уходит в «корзину» в папке данных программы (последние 50), чтобы можно было вернуть.
Без сети, без Moodle-кода и без сторонних библиотек."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

NOTES_DIR = "Заметки"                 # папка в корне папки сохранения
GENERAL = "Блокнот"                   # подпапка заметок, не привязанных к курсу
HEAD = "<!--mo-note "
HEAD_END = "-->"
MAX_TITLE = 120
MAX_BODY = 200_000                    # символов в одной заметке
MAX_NAME = 80                         # длина имени файла (без .md)
TRASH_KEEP = 50
PREVIEW = 160
_ID = re.compile(r"^[nx][0-9a-f]{6,40}$")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class NoteError(Exception):
    """Понятная человеку причина, почему с заметкой не вышло."""


def _fsp(p) -> str:
    """Путь для файловых операций; в Windows включает поддержку длинных путей (>260 символов)."""
    s = str(p)
    if os.name == "nt":
        s = os.path.abspath(s)
        if len(s) >= 240 and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
    return s


def root(out) -> Path:
    return Path(out) / NOTES_DIR


def safe_name(s: str, limit: int = MAX_NAME, fallback: str = "заметка") -> str:
    """Название → имя файла или папки, допустимое в Windows, Android и Linux."""
    s = _BAD.sub(" ", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > limit:
        s = s[:limit].rstrip(" .")
    if not s:
        return fallback
    if s.split(".")[0].casefold() in _RESERVED:
        s += "_"
    return s


# ── служебная строка ──────────────────────────────────────────────────────────────────────────
def _split(text: str) -> tuple[dict | None, str]:
    """Текст файла → (служебные данные или None, сама заметка)."""
    if text.startswith("﻿"):
        text = text[1:]
    if text.startswith(HEAD):
        end = text.find(HEAD_END)
        if 0 < end < 4096:
            try:
                meta = json.loads(text[len(HEAD):end])
            except ValueError:
                meta = None
            if isinstance(meta, dict):
                body = text[end + len(HEAD_END):]
                return meta, body[1:] if body.startswith("\n") else body
    return None, text


def _head(meta: dict) -> str:
    j = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace(">", "\\u003e")   # «-->» внутри значений не должно закрыть комментарий
    return HEAD + j + HEAD_END + "\n"


def _read(path) -> tuple[dict | None, str]:
    with open(_fsp(path), "rb") as f:
        raw = f.read(MAX_BODY * 4 + 8192)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    return _split(text)


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _info(out: Path, path: Path, meta: dict | None, body: str) -> dict:
    rel = os.path.relpath(_fsp(path), _fsp(root(out))).replace(os.sep, "/")      # через _fsp: в Windows у длинных путей другой префикс
    try:
        mtime = int(os.stat(_fsp(path)).st_mtime)
    except OSError:
        mtime = 0
    m = meta or {}
    nid = str(m.get("id") or "")
    if not _ID.match(nid):
        nid = "x" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]     # файл положили руками: номер от пути
    created = _int(m.get("created")) or mtime
    updated = max(_int(m.get("updated")) or 0, mtime)                       # правили снаружи — файл новее записи в заголовке
    flat = re.sub(r"\s+", " ", body).strip()
    return {"id": nid, "title": str(m.get("title") or Path(rel).stem)[:MAX_TITLE],
            "course": _int(m.get("course")), "course_name": str(m.get("course_name") or "")[:200],
            "item": str(m.get("item") or "") or None, "item_name": str(m.get("item_name") or "")[:200],
            "pinned": bool(m.get("pinned")), "created": created, "updated": updated,
            "path": f"{NOTES_DIR}/{rel}", "preview": flat[:PREVIEW] + ("…" if len(flat) > PREVIEW else "")}


def _files(out):
    base = root(out)
    b = _fsp(base)
    if not os.path.isdir(b):
        return
    for dp, dns, fns in os.walk(b):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for fn in sorted(fns):
            if fn.lower().endswith(".md") and not fn.startswith("."):
                yield Path(dp) / fn


def _load(out, path):
    try:
        meta, body = _read(path)
    except OSError:
        return None
    return _info(Path(out), Path(path), meta, body), body


# ── чтение ────────────────────────────────────────────────────────────────────────────────────
def _all(out):
    """(путь, описание, текст) всех заметок. Если у двух файлов один номер (так бывает после переноса данных, когда
    заметку переименовали на разных устройствах), у второго номер берётся от пути — иначе правка попала бы не в тот файл."""
    seen = set()
    for p in _files(out):
        got = _load(out, p)
        if not got:
            continue
        n, body = got
        if n["id"] in seen:
            rel = os.path.relpath(_fsp(p), _fsp(root(out))).replace(os.sep, "/")
            n["id"] = "x" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
        seen.add(n["id"])
        yield p, n, body


def list_notes(out, *, course=None, item=None, general=False, q: str = "") -> list[dict]:
    """Заметки: все, по курсу, по элементу курса, только общие (general) и/или по тексту (в названии и в самой заметке)."""
    q = (q or "").strip().casefold()
    res = []
    for _p, n, body in _all(out):
        if course is not None and n["course"] != course:
            continue
        if item is not None and n["item"] != item:
            continue
        if general and n["course"] is not None:
            continue
        if q and q not in n["title"].casefold() and q not in body.casefold():
            continue
        res.append(n)
    res.sort(key=lambda n: (not n["pinned"], -n["updated"], n["title"].casefold()))
    return res


def find(out, nid: str):
    """(путь, описание, текст) заметки по номеру или None."""
    if not _ID.match(str(nid or "")):
        return None
    for p, n, body in _all(out):
        if n["id"] == nid:
            return p, n, body
    return None


def get_note(out, nid: str) -> dict:
    f = find(out, nid)
    if not f:
        raise NoteError("Заметка не найдена — возможно, её удалили или переместили.")
    return {**f[1], "body": f[2]}


# ── запись ────────────────────────────────────────────────────────────────────────────────────
def _unique(folder: Path, stem: str, keep: Path | None = None) -> Path:
    """Свободное имя файла в папке (без учёта регистра, как в Windows)."""
    taken = set()
    try:
        taken = {n.casefold() for n in os.listdir(_fsp(folder))}
    except OSError:
        pass
    if keep is not None:
        taken.discard(keep.name.casefold())
    name, i = f"{stem}.md", 2
    while name.casefold() in taken:
        name = f"{stem} ({i}).md"
        i += 1
    return folder / name


def _write(path: Path, text: str):
    os.makedirs(_fsp(path.parent), exist_ok=True)
    tmp = path.parent / ("." + path.name + ".tmp")
    with open(_fsp(tmp), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(_fsp(tmp), _fsp(path))


def _title_from(title: str, body: str) -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        for line in str(body or "").splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                t = line[:60]
                break
    return (t or time.strftime("Заметка %d.%m.%Y %H:%M"))[:MAX_TITLE]


def _new_id(out, ts: int) -> str:
    """Номер новой заметки: секунды + 3 случайных байта, и такого номера ещё нет ни у одной заметки. Раньше хвост был
    в 2 байта без проверки: у 58 заметок за одну секунду совпадение ≈ 2,6 %, и две разные заметки получали один номер."""
    used = {n["id"] for _p, n, _b in _all(out)}
    base = "n" + format(ts, "x")
    for _ in range(100):
        cand = base + secrets.token_hex(3)
        if cand not in used:
            return cand
    return "n" + format(time.time_ns(), "x")           # недостижимо на деле: 16 млн вариантов на секунду


def save_note(out, *, nid: str | None = None, title: str = "", body: str = "", course=None, course_name: str = "",
              item=None, item_name: str = "", pinned=None, now: float | None = None) -> dict:
    """Создать заметку (nid не указан) или изменить существующую. Привязка к курсу задаётся при создании и дальше не меняется."""
    body = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(body) > MAX_BODY:
        raise NoteError(f"Слишком длинная заметка: больше {MAX_BODY // 1000} тысяч символов. Разбейте её на две.")
    ts = int(time.time() if now is None else now)
    old = None
    if nid:
        old = find(out, nid)
        if not old:
            raise NoteError("Заметка не найдена — возможно, её удалили или переместили.")
    title = _title_from(title, body)
    if old:
        path, info, _b = old
        meta = {"id": info["id"], "title": title, "course": info["course"], "course_name": info["course_name"],
                "item": info["item"], "item_name": info["item_name"], "pinned": info["pinned"] if pinned is None else bool(pinned),
                "created": info["created"], "updated": ts}
        folder = path.parent
        stem = safe_name(title)
        dest = path if path.stem.casefold() == stem.casefold() or re.fullmatch(re.escape(stem) + r" \(\d+\)", path.stem) else _unique(folder, stem, keep=path)
    else:
        cid = _int(course)
        meta = {"id": _new_id(out, ts), "title": title, "course": cid,
                "course_name": str(course_name or "")[:200] if cid is not None else "",
                "item": (str(item) if item not in (None, "") else None) if cid is not None else None,
                "item_name": str(item_name or "")[:200] if cid is not None and item not in (None, "") else "",
                "pinned": bool(pinned), "created": ts, "updated": ts}
        sub = safe_name(meta["course_name"], 60, f"Курс {cid}") if cid is not None else GENERAL
        folder = root(out) / sub
        dest = _unique(folder, safe_name(title))
        path = None
    _write(dest, _head(meta) + body + ("" if body.endswith("\n") or not body else "\n"))
    if path is not None and path != dest:
        try:
            os.remove(_fsp(path))                       # название изменилось — файл переименован
        except OSError:
            pass
    got = _load(out, dest)
    return {**got[0], "body": got[1]}


def _meta_of(info: dict) -> dict:
    return {"id": info["id"], "title": info["title"], "course": info["course"], "course_name": info["course_name"],
            "item": info["item"], "item_name": info["item_name"], "pinned": info["pinned"],
            "created": info["created"], "updated": info["updated"]}


def set_pinned(out, nid: str, pinned: bool) -> dict:
    """Закрепить или открепить, не меняя текста и не поднимая заметку наверх по дате правки. Файл без служебной строки
    (его положили руками) получает её — название и текст остаются как были."""
    f = find(out, nid)
    if not f:
        raise NoteError("Заметка не найдена — возможно, её удалили или переместили.")
    path, info, body = f
    if bool(info["pinned"]) == bool(pinned):
        return info
    try:
        before = int(os.stat(_fsp(path)).st_mtime)
    except OSError:
        before = int(time.time())
    meta = {**_meta_of(info), "pinned": bool(pinned)}
    _write(path, _head(meta) + body)
    # Файл переписан, но дата правки заметки не меняется. Дата файла — на секунду новее: при «Переносе данных» побеждает
    # более новый файл, и без этого закрепление не дошло бы до другого устройства.
    try:
        os.utime(_fsp(path), (before + 1, before + 1))
    except OSError:
        pass
    got = _load(out, path)
    return {**got[0], "body": got[1]}


def _trim_trash(td: Path, keep_names: list[str]):
    """Корзина не растёт: хранятся последние TRASH_KEEP. Только что удалённые (keep_names, по порядку) не трогаем —
    дата файла у них от последней правки заметки, а не от удаления, и по ней «последние» определить нельзя."""
    try:
        protected = set(keep_names[-TRASH_KEEP:])
        others = sorted((p for p in os.listdir(_fsp(td)) if p.endswith(".md") and p not in protected),
                        key=lambda n: os.stat(_fsp(td / n)).st_mtime)
        for n in others[:max(0, len(others) + len(protected) - TRASH_KEEP)]:
            os.remove(_fsp(td / n))
        for n in keep_names[:-TRASH_KEEP]:
            if n not in protected:
                try:
                    os.remove(_fsp(td / n))
                except OSError:
                    pass
    except OSError:
        pass


def _to_trash(path: Path, info: dict, body: str, td: Path, stamp_ns: int) -> str:
    """Файл заметки → корзина. Заметку без служебной строки кладём с ней: иначе вернуть её было бы нельзя."""
    name = f"{info['id']}.md"
    dst = td / name
    meta, _b = _read(path)
    if meta:
        shutil.move(_fsp(path), _fsp(dst))               # папка данных может быть на другом диске
    else:
        _write(dst, _head(_meta_of(info)) + body)
        os.remove(_fsp(path))
    try:
        os.utime(_fsp(dst), ns=(stamp_ns, stamp_ns))     # время удаления: по нему корзина решает, что старое
    except OSError:
        pass
    return name


def _drop_empty(folder: Path):
    try:
        os.rmdir(_fsp(folder))                          # пустая папка курса не нужна (непустая — не удалится)
    except OSError:
        pass


def delete_note(out, nid: str, trash_dir=None) -> dict:
    """Убрать заметку в «корзину» (trash_dir). → {"id", "trash"}: по trash её можно вернуть (restore_note)."""
    f = find(out, nid)
    if not f:
        raise NoteError("Заметка не найдена — возможно, её уже удалили.")
    path, info, body = f
    trash = None
    if trash_dir is not None:
        td = Path(trash_dir)
        os.makedirs(_fsp(td), exist_ok=True)
        trash = _to_trash(path, info, body, td, time.time_ns())
        _trim_trash(td, [trash])
    else:
        os.remove(_fsp(path))
    _drop_empty(path.parent)
    return {"id": info["id"], "trash": trash}


def delete_many(out, ids, trash_dir=None) -> dict:
    """Удалить сразу несколько заметок (галочки в списке). Повторы в списке считаются один раз.
    → {"deleted": сколько удалено, "missing": сколько не нашлось, "trash": имена для restore_many (только те, что ещё
    можно вернуть — в корзине помещается TRASH_KEEP), "kept": их число}."""
    want, seen = [], set()
    for i in ids or []:
        i = str(i or "")
        if i not in seen:
            seen.add(i)
            want.append(i)
    byid = {n["id"]: (p, n, b) for p, n, b in _all(out)}        # один проход по папке, а не по разу на заметку
    deleted, missing, names, folders = 0, 0, [], set()
    td = Path(trash_dir) if trash_dir is not None else None
    if td is not None and want:
        os.makedirs(_fsp(td), exist_ok=True)
    base = time.time_ns()
    for k, i in enumerate(want):
        f = byid.get(i) if _ID.match(i) else None
        if not f:
            missing += 1
            continue
        path, info, body = f
        try:
            if td is not None:
                names.append(_to_trash(path, info, body, td, base + k * 1000))
            else:
                os.remove(_fsp(path))
        except OSError:
            missing += 1
            continue
        deleted += 1
        folders.add(path.parent)
    if td is not None and names:
        _trim_trash(td, names)
        names = names[-TRASH_KEEP:]
    for d in folders:
        _drop_empty(d)
    return {"deleted": deleted, "missing": missing, "trash": names, "kept": len(names)}


def _restore(out, trash: str, td: Path, known: set) -> dict | None:
    if not re.fullmatch(r"[nx][0-9a-f]{6,40}\.md", str(trash or "")):
        raise NoteError("Нечего возвращать.")
    src = td / trash
    if not os.path.isfile(_fsp(src)):
        raise NoteError("Эту заметку уже нельзя вернуть: корзина очищена.")
    meta, body = _read(src)
    if not meta:
        raise NoteError("Файл в корзине повреждён.")
    nid = str(meta.get("id", ""))
    if nid in known:                                    # такая заметка уже есть (вернули раньше) — копия в корзине лишняя
        os.remove(_fsp(src))
        return None
    cid = _int(meta.get("course"))
    sub = safe_name(str(meta.get("course_name") or ""), 60, f"Курс {cid}") if cid is not None else GENERAL
    dest = _unique(root(out) / sub, safe_name(str(meta.get("title") or "заметка")))
    os.makedirs(_fsp(dest.parent), exist_ok=True)
    shutil.move(_fsp(src), _fsp(dest))
    known.add(nid)
    return {"path": dest}


def restore_note(out, trash: str, trash_dir) -> dict:
    """Вернуть удалённую заметку на место (папка и имя — по её названию и курсу)."""
    td = Path(trash_dir)
    known = {n["id"] for _p, n, _b in _all(out)}
    r = _restore(out, trash, td, known)
    if r is None:                                       # уже на месте
        return get_note(out, trash[:-3])
    got = _load(out, r["path"])
    return {**got[0], "body": got[1]}


def restore_many(out, trash_names, trash_dir) -> dict:
    """Вернуть несколько заметок (отмена удаления пачки). → {"restored", "failed", "ids"}."""
    td = Path(trash_dir)
    known = {n["id"] for _p, n, _b in _all(out)}
    ok, bad, paths = 0, 0, []
    for name in dict.fromkeys(str(x or "") for x in (trash_names or [])):
        try:
            r = _restore(out, name, td, known)
        except (NoteError, OSError):
            bad += 1
            continue
        ok += 1
        if r:
            paths.append(r["path"])
    ids = []
    for p in paths:
        got = _load(out, p)
        if got:
            ids.append(got[0]["id"])
    return {"restored": ok, "failed": bad, "ids": ids}

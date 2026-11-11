# -*- coding: utf-8 -*-
"""
Несколько расписаний: своё + чужие группы (история), обмен файлом.

  Расписание/                          — своё расписание (как раньше)
  Расписание/Другие группы/<код>/      — чужие группы: свои данные, офлайн-страница, .ics
  Расписание/Другие группы/<код>/.profile.json — «карточка»: когда добавили/смотрели, источники, автообновление
  Расписание/Поделиться/*.zip          — файлы для друзей (то же, что «бэкап», но только про расписание)

Файл для обмена — обычный .zip: manifest.json, README.txt, <код>/schedule.json, <код>/index.html (откроется в любом
браузере без программы), <код>/raspisanie.ics. Принимающая сторона НЕ доверяет присланным index.html и ics: берёт только
пары из schedule.json, проверяет каждую запись и пересобирает страницу своим просмотрщиком.

Без сети и сторонних библиотек (сеть — в schedule.py, вызывает gui.py / moodle_offline.py).
"""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import moodle_offline as mo
import schedule as sch

PROFILE_FILE = ".profile.json"
SHARE_DIR = "Поделиться"
FORMAT = "moodle-offline-schedule"
FORMAT_VERSION = 1
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,59}$")
MAX_UNPACKED = 60 * 1024 * 1024       # больше — не расписание
MAX_ENTRIES = 300
MAX_SCHEDULES = 30
MAX_LESSONS = 30000
STR_FIELDS = ("subject", "type", "room", "address", "teacher", "subgroup", "stream", "pair")
_DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
_TIME = re.compile(r"^\d{1,2}:\d\d$")
_SRC = re.compile(r"^[a-z]{1,20}$")


class BookError(Exception):
    pass


# ── пути ────────────────────────────────────────────────────────────────────
def valid_slug(slug) -> bool:
    return isinstance(slug, str) and bool(SLUG_RE.match(slug))


def other_root(out) -> Path:
    return Path(out) / sch.SCHEDULE_DIR / sch.OTHER_DIR


def profile_dir(out, slug: str) -> Path:
    if not valid_slug(slug):
        raise BookError("неверный код группы")
    return other_root(out) / slug


def rel_dir(slug: str | None) -> str:
    """Путь папки расписания от папки сохранения (для файлов, открытия и просмотра)."""
    return f"{sch.SCHEDULE_DIR}/{sch.OTHER_DIR}/{slug}" if slug else sch.SCHEDULE_DIR


def _read_json(p):
    try:
        with mo.fopen(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ── «карточка» профиля ──────────────────────────────────────────────────────
def read_profile(root) -> dict:
    d = _read_json(Path(root) / PROFILE_FILE)
    return d if isinstance(d, dict) else {}


def write_profile(root, **kw) -> dict:
    root = Path(root)
    d = read_profile(root)
    d.update({k: v for k, v in kw.items() if v is not None})
    mo.fmkdir(root)
    mo.write_text_atomic(root / PROFILE_FILE, json.dumps(d, ensure_ascii=False, indent=1))
    return d


def ensure_profile(out, slug: str, title: str = "", origin: str = "site") -> Path:
    """Создаёт папку и карточку чужой группы (если их ещё нет); возвращает папку."""
    root = profile_dir(out, slug)
    prof = read_profile(root)
    if not prof:
        write_profile(root, slug=slug, title=title or "", origin=origin, added=int(time.time()), last_used=int(time.time()),
                      auto=False, sources=["schedule"])
    elif title and not prof.get("title"):
        write_profile(root, title=title)
    return root


def pick_title(site_name: str, slug: str, fallback: str = "") -> str:
    """Название группы для списка: с сайта берём, только если оно правда про эту группу (код совпадает) — иначе запасное."""
    try:
        if site_name and sch.resolve_group(site_name) == slug:
            return site_name
    except Exception:
        pass
    return fallback or sch.group_name(slug) or slug


def touch(out, slug: str):
    """Запоминает, что чужое расписание открывали сейчас (для порядка в истории)."""
    root = profile_dir(out, slug)
    if (root / PROFILE_FILE).exists():
        write_profile(root, last_used=int(time.time()))


def summary(root) -> dict:
    """Кратко о сохранённом расписании в папке: есть ли, сколько пар, период, когда обновлено."""
    d = _read_json(Path(root) / "schedule.json")
    if not isinstance(d, dict):
        return {"saved": False, "count": 0, "updated": "", "first": "", "last": "", "name": "", "published_until": ""}
    ls = d.get("lessons") or []
    meta = d.get("meta") or {}
    return {"saved": True, "count": len(ls), "updated": meta.get("generated", ""), "name": meta.get("group", ""),
            "first": ls[0].get("date", "") if ls else "", "last": ls[-1].get("date", "") if ls else "",
            "published_until": meta.get("published_until", "")}


def list_profiles(out, own_slug: str, own_name: str = "") -> list[dict]:
    """Своё расписание первым, дальше чужие — недавно открытые сверху."""
    out = Path(out)
    res = []
    own_root = out / sch.SCHEDULE_DIR
    sm = summary(own_root)
    res.append({"id": "own", "slug": own_slug, "own": True, "title": own_name or sm["name"] or own_slug,
                "dir": rel_dir(None), "origin": "site", "auto": False, "sources": [], "last_used": 0, **sm})
    root = other_root(out)
    others = []
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir() and valid_slug(p.name))
    except OSError:
        entries = []
    for p in entries:
        prof = read_profile(p)
        sm = summary(p)
        if not sm["saved"] and not prof:
            continue                      # пустая папка без данных и карточки
        others.append({"id": p.name, "slug": p.name, "own": False, "title": prof.get("title") or sm["name"] or p.name,
                       "dir": rel_dir(p.name), "origin": prof.get("origin") or "site", "auto": bool(prof.get("auto")),
                       "sources": prof.get("sources") or ["schedule"], "last_used": int(prof.get("last_used") or 0),
                       "added": int(prof.get("added") or 0), **sm})
    others.sort(key=lambda e: (-e["last_used"], e["title"].casefold()))
    return res + others


def auto_profiles(out) -> list[dict]:
    """Чужие группы, которые обновляются вместе с основным расписанием (по желанию человека, по умолчанию — нет)."""
    return [e for e in list_profiles(out, "") if not e["own"] and e.get("auto")]


def delete_profile(out, slug: str) -> bool:
    root = profile_dir(out, slug)
    base = other_root(out).resolve()
    if not root.is_dir() or base not in root.resolve().parents:
        return False
    shutil.rmtree(mo.fsp(root), ignore_errors=True)
    return not root.exists()


# ── файл для друзей ─────────────────────────────────────────────────────────
_README = """Расписание для Moodle Offline
=============================
Этот файл сделан программой Moodle Offline (раздел «Расписание» → «Поделиться»).

Как открыть без программы:   распакуйте архив и откройте в браузере файл  <группа>/index.html
                              (это обычная страница, интернет не нужен), а <группа>/raspisanie.ics
                              можно добавить в календарь телефона или компьютера.
Как добавить в программу:    Moodle Offline → «Расписание» → «Чужие расписания» → «Загрузить из файла» → выберите этот .zip.

Внутри только расписание (пары, преподаватели, аудитории). Если расписание сделано из личного кабинета, в нём могут быть
отметки ваших потоков и подгрупп — решайте сами, кому отправлять.
"""


def export_zip(out, ids: list[str], own_slug: str, own_name: str = "", app_version: str = "") -> tuple[Path, list[str]]:
    """Собирает .zip из выбранных расписаний: "own" — своё, остальное — коды чужих групп. → (путь, замечания)."""
    out = Path(out)
    profiles = {e["id"]: e for e in list_profiles(out, own_slug, own_name)}
    chosen, notes = [], []
    for i in ids:
        e = profiles.get(i)
        if not e:
            notes.append(f"расписание «{i}» не найдено — пропущено")
        elif not e["saved"]:
            notes.append(f"«{e['title']}»: ещё не сохранено — пропущено")
        elif e not in chosen:
            chosen.append(e)
    if not chosen:
        raise BookError("нечего отправлять: выберите сохранённое расписание")
    stamp = date.today().isoformat()
    names = "+".join(mo.safe_filename(e["title"]).replace(" ", "") for e in chosen[:3])
    dest_dir = out / sch.SCHEDULE_DIR / SHARE_DIR
    mo.fmkdir(dest_dir)
    dest = dest_dir / f"Расписание_{names}{'_и_др' if len(chosen) > 3 else ''}_{stamp}.zip"
    manifest = {"format": FORMAT, "version": FORMAT_VERSION, "app": app_version, "created": datetime.now().strftime("%d.%m.%Y %H:%M"),
                "schedules": []}
    tmp = dest.with_name(dest.name + ".tmp")
    used = set()
    with zipfile.ZipFile(mo.fsp(tmp), "w", zipfile.ZIP_DEFLATED) as z:
        for e in chosen:
            folder = e["slug"] if valid_slug(e["slug"]) else "group"
            while folder in used:
                folder += "-2"
            used.add(folder)
            src = out / Path(*e["dir"].split("/"))
            for name in ("schedule.json", "index.html", "raspisanie.ics"):
                p = src / name
                if mo.fexists(p):
                    with mo.fopen(p, "rb") as f:
                        z.writestr(f"{folder}/{name}", f.read())
            manifest["schedules"].append({"slug": folder, "title": e["title"], "count": e["count"], "first": e["first"],
                                          "last": e["last"], "generated": e["updated"]})
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("README.txt", _README)
    mo.freplace(tmp, dest)
    return dest, notes


# ── приём файла ─────────────────────────────────────────────────────────────
def _clean_lessons(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for le in raw[:MAX_LESSONS]:
        if not isinstance(le, dict):
            continue
        d, st = le.get("date"), le.get("start")
        if not (isinstance(d, str) and _DATE.match(d) and isinstance(st, str) and _TIME.match(st)):
            continue
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        c = {"date": d, "start": st.zfill(5), "end": ""}
        en = le.get("end")
        if isinstance(en, str) and _TIME.match(en):
            c["end"] = en.zfill(5)
        for k in STR_FIELDS:
            v = le.get(k, "")
            c[k] = (str(v) if isinstance(v, (str, int, float)) else "")[:300].strip()
        srcs = le.get("src")
        c["src"] = [x for x in srcs if isinstance(x, str) and _SRC.match(x)][:5] if isinstance(srcs, list) else []
        c["src"] = [x for x in c["src"] if x != "timetable"] or [sch.IMPORT_SRC]   # «личное» чужого человека — не ваше личное
        out.append(c)
    return out


def _slug_for(entry_slug, title: str) -> str:
    if valid_slug(entry_slug):
        return entry_slug
    s = sch.group_slug(title or "")
    return s if valid_slug(s) else "file-" + str(int(time.time()))[-6:]


def import_zip(out, data: bytes) -> list[dict]:
    """Принимает файл расписания. Каждое расписание из файла становится отдельной чужой группой (или дополняет уже имеющуюся).
    → [{"slug","title","count","first","last"}]; при непонятном файле — BookError с объяснением по-русски."""
    out = Path(out)
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        raise BookError("это не файл расписания: ожидается архив .zip, сделанный программой («Расписание» → «Поделиться»)")
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise BookError("файл слишком большой для расписания")
        names = {i.filename: i for i in infos}
        if "manifest.json" not in names:
            raise BookError("в архиве нет manifest.json — это не файл расписания Moodle Offline")
        try:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BookError("manifest.json повреждён")
        if not isinstance(man, dict) or man.get("format") != FORMAT:
            raise BookError("это архив не из Moodle Offline (расписание)")
        if int(man.get("version") or 0) > FORMAT_VERSION:
            raise BookError("файл сделан более новой версией программы — обновите программу")
        results = []
        for ent in (man.get("schedules") or [])[:MAX_SCHEDULES]:
            if not isinstance(ent, dict):
                continue
            folder = ent.get("slug")
            if not valid_slug(folder) or f"{folder}/schedule.json" not in names:
                continue
            try:
                d = json.loads(z.read(f"{folder}/schedule.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(d, dict):
                continue
            lessons = _clean_lessons(d.get("lessons"))
            if not lessons:
                continue
            meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
            title = str(ent.get("title") or meta.get("group") or folder)[:80].strip()
            slug = _slug_for(folder, title)
            root = ensure_profile(out, slug, title, origin="file")
            data_dir = root / "data"
            mo.fmkdir(data_dir)
            lessons.sort(key=lambda x: (x["date"], x["start"]))
            mo.write_text_atomic(data_dir / f"{sch.IMPORT_SRC}.json", json.dumps(
                {"source": sch.IMPORT_SRC, "group": slug, "updated": datetime.now().strftime("%d.%m.%Y %H:%M"), "covered": [],
                 "lessons": lessons}, ensure_ascii=False))
            if not (data_dir / "meta.json").exists():
                mo.write_text_atomic(data_dir / "meta.json", json.dumps({"group_name": title}, ensure_ascii=False))
            write_profile(root, title=title, last_used=int(time.time()))
            sch.build_outputs(root, slug, None, own=False)
            sm = summary(root)
            results.append({"slug": slug, "title": title, "count": sm["count"], "first": sm["first"], "last": sm["last"]})
    if not results:
        raise BookError("в файле нет ни одного расписания с парами")
    return results

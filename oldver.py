"""Старые версии самой программы: найти скачанные раньше файлы (exe, apk, архивы) и остатки обновлений, показать списком,
дать удалить выбранное.

Что считается «версией программы»: файл с именем «MoodleOffline…» (exe, apk, zip, бинарник Linux, контрольная сумма .sha256)
и остатки обновления рядом (.old, .new, .part, недокачанное .crdownload). Ищем только в обычных местах (Загрузки, Рабочий стол,
Документы, папка самой программы; на телефоне — «Download»/«Documents» общей памяти) и только в самих этих папках и в их
подпапках с таким же именем — по всему диску не ходим. Запущенную сейчас программу не показываем никогда.

Версию берём из имени файла («MoodleOffline-1.28.0.exe») и из самого apk (AndroidManifest.xml, versionName); у остальных —
сравниваем по размеру и дате с запущенной копией. Удаляется только то, что нашёл свежий поиск: идентификаторы с окна сверяются
с ним, чужие пути не принимаются. Сеть не трогает, Moodle-кода и сторонних библиотек нет."""
from __future__ import annotations

import hashlib
import os
import re
import struct
import zipfile
from pathlib import Path

import updater

NAME_RE = re.compile(r"^moodle[ _.\-]?offline", re.I)
LEFTOVER_EXT = {".old", ".new", ".part", ".crdownload", ".download", ".tmp"}      # остатки обновления и недокачанные файлы
BIN_EXT = {".exe": "exe", ".apk": "apk", ".zip": "zip"}
MAX_ITEMS = 300                  # больше не показываем (обычно их единицы)
MAX_DIR_ENTRIES = 20000          # в одной папке читаем не больше (Загрузки бывают огромные)
MAX_MANIFEST = 2 * 1024 * 1024
DATE_SLACK = 60                  # секунд: «скачан раньше запущенной копии» только с запасом, чтобы копирование не путало
STATUS_ORDER = {"old": 0, "junk": 1, "unknown": 2, "newer": 3, "same": 4}
KIND_TEXT = {"exe": "программа для Windows", "apk": "приложение для Android", "zip": "архив", "bin": "программа для Linux",
             "sha": "контрольная сумма", "leftover": "остаток обновления"}


# ── что считать файлом программы ──────────────────────────────────────────────
def kind_of(name: str) -> str | None:
    """exe / apk / zip / bin (Linux) / sha (контрольная сумма) / leftover (остаток обновления) — или None, если это не наше."""
    if not NAME_RE.match(name or ""):
        return None
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext in LEFTOVER_EXT:
        return "leftover"
    if ext == ".sha256":
        return "sha"
    if ext in BIN_EXT:
        return BIN_EXT[ext]
    if ext == "" and low.startswith(("moodleoffline-linux", "moodle-offline-linux", "moodle_offline_linux")):
        return "bin"
    return None


def name_version(name: str) -> str | None:
    """«MoodleOffline-1.28.0.exe» → «1.28.0»; без цифр с точками → None."""
    m = re.search(r"(?<![\d.])v?(\d+\.\d+(?:\.\d+)*)(?![\d])", name or "")
    return m.group(1) if m else None


def compare(a, b) -> int:
    """−1, если версия a старше b; 0 — одинаковые; 1 — новее. Если одну из них не понять — 0 (не решаем за человека)."""
    x, y = updater.parse_version(a), updater.parse_version(b)
    if not x or not y:
        return 0
    n = max(len(x), len(y))
    x, y = x + (0,) * (n - len(x)), y + (0,) * (n - len(y))
    return (x > y) - (x < y)


# ── версия внутри apk (двоичный AndroidManifest.xml) ──────────────────────────
def _pool(data: bytes, off: int, hsz: int, size: int) -> list[str]:
    count, _styles, flags, start = struct.unpack_from("<IIII", data, off + 8)
    if count > 100000:
        return []
    utf8 = bool(flags & 0x100)
    offs = struct.unpack_from(f"<{count}I", data, off + hsz)
    base, end, out = off + start, off + size, []
    for o in offs:
        p = base + o
        if p >= end:
            out.append("")
            continue
        if utf8:
            n = data[p]; p += 1                      # длина в символах (одна или две цифры), потом длина в байтах
            if n & 0x80:
                p += 1
            n = data[p]; p += 1
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p]; p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]; p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]; p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le", "replace"))
    return out


def manifest_version(data: bytes) -> dict | None:
    """{"name": "1.29.1", "code": 12901} из двоичного AndroidManifest.xml; не вышло — None."""
    try:
        if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != 0x0003:
            return None
        total = min(struct.unpack_from("<I", data, 4)[0], len(data))
        off, strings = struct.unpack_from("<H", data, 2)[0], []
        while off + 8 <= total:
            typ, hsz, size = struct.unpack_from("<HHI", data, off)
            if size < 8 or off + size > total:
                return None
            if typ == 0x0001:
                strings = _pool(data, off, hsz, size)
            elif typ == 0x0102:                      # начало элемента; нужен только первый — <manifest>
                _ln, _cm, _ns, name, a_start, a_size, a_count = struct.unpack_from("<IIIIHHH", data, off + 8)
                if name >= len(strings) or strings[name] != "manifest":
                    return None
                res = {"name": None, "code": None}
                for i in range(a_count):
                    a = off + 16 + a_start + i * a_size
                    _ans, aname, raw, _sz, _r0, dtype, val = struct.unpack_from("<IIIHBBI", data, a)
                    label = strings[aname] if aname < len(strings) else ""
                    if label == "versionName":
                        idx = raw if raw != 0xFFFFFFFF else (val if dtype == 0x03 else 0xFFFFFFFF)
                        res["name"] = strings[idx] if idx < len(strings) else None
                    elif label == "versionCode" and dtype in (0x10, 0x11):
                        res["code"] = val
                return res
            off += size
    except (struct.error, IndexError, ValueError):
        return None
    return None


def apk_version(path) -> dict | None:
    """Версия из самого apk (имя файла могло быть любым — «MoodleOffline (3).apk»). Не apk или не дочитан — None."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("AndroidManifest.xml")
            if info.file_size > MAX_MANIFEST:
                return None
            return manifest_version(z.read(info))
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, EOFError, NotImplementedError):
        return None


# ── где искать ────────────────────────────────────────────────────────────────
def search_dirs(home, system: str, exe_dir=None, shared=None) -> list[Path]:
    """Обычные места, куда попадают скачанные файлы. Только существующие, без повторов (на Windows регистр не важен)."""
    home = Path(home)
    names = ["Downloads", "Загрузки", "Desktop", "Рабочий стол", "Documents", "Документы"]
    cand: list[Path] = []
    if exe_dir:
        cand.append(Path(exe_dir))
    if system == "android":
        if shared:
            sh = Path(shared)
            cand += [sh / "Download", sh / "Downloads", sh / "Documents", sh / "Telegram" / "Telegram Documents",
                     sh / "Android" / "media" / "org.telegram.messenger" / "Telegram" / "Telegram Documents", sh]
    else:
        cand += [home / n for n in names]
        if system == "windows":                      # папки, перенесённые в OneDrive
            cand += [home / "OneDrive" / n for n in ("Desktop", "Рабочий стол", "Documents", "Документы", "Downloads", "Загрузки")]
    out, seen = [], set()
    for d in cand:
        try:
            if not d.is_dir():
                continue
            key = os.path.normcase(os.path.realpath(d))
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _folders(d: Path, skip: set):
    """Сама папка и её подпапки с именем «MoodleOffline…» (распакованный архив, «Загрузки/MoodleOffline/»)."""
    yield d
    try:
        with os.scandir(d) as it:
            for i, e in enumerate(it):
                if i >= MAX_DIR_ENTRIES:
                    break
                try:
                    if e.is_dir(follow_symlinks=False) and NAME_RE.match(e.name) and os.path.normcase(os.path.realpath(e.path)) not in skip:
                        yield Path(e.path)
                except OSError:
                    continue
    except OSError:
        return


def _real(p) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def _sha256(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def item_id(path) -> str:
    return hashlib.sha1(_real(path).encode("utf-8", "replace")).hexdigest()[:16]


# ── поиск ─────────────────────────────────────────────────────────────────────
def scan(dirs, current: str, running=None, busy=(), skip=(), sha=_sha256) -> dict:
    """Список найденных файлов программы с оценкой «старая / такая же / новее / не знаю / остаток».
    dirs — где искать; current — версия запущенной программы; running — её файл (exe), если запущена собранная копия;
    busy — файлы, которые сейчас нужны обновлению (не показываем); skip — папки, куда не заходим (данные программы)."""
    run_real = _real(running) if running else None
    busy_set = {_real(b) for b in busy}
    skip_set = {_real(s) for s in skip}
    ref_mtime = run_size = None
    if running:
        try:
            st = os.stat(running)
            ref_mtime, run_size = st.st_mtime, st.st_size
        except OSError:
            pass
    run_hash = []                                    # считаем не больше одного раза и только если понадобится

    items, seen, truncated = [], set(), False
    for d in dirs:
        for folder in _folders(Path(d), skip_set):
            try:
                it = os.scandir(folder)
            except OSError:
                continue
            with it:
                for n, e in enumerate(it):
                    if n >= MAX_DIR_ENTRIES:
                        break
                    kind = kind_of(e.name)
                    if kind is None:
                        continue
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue                  # папки и ссылки не трогаем
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    real = _real(e.path)
                    if real in seen or real == run_real or real in busy_set:
                        continue
                    seen.add(real)
                    if len(items) >= MAX_ITEMS:
                        truncated = True
                        continue
                    items.append({"id": item_id(e.path), "name": e.name, "dir": str(folder), "path": e.path, "size": st.st_size,
                                  "mtime": int(st.st_mtime), "kind": kind, "kind_text": KIND_TEXT[kind], "version": None,
                                  "status": "unknown", "why": "", "checked": False, "_mt": st.st_mtime})

    for x in items:
        if x["kind"] in ("leftover", "sha"):
            continue
        ver = None
        if x["kind"] == "apk":
            info = apk_version(x["path"])
            ver = (info or {}).get("name")
        ver = ver or name_version(x["name"])
        x["version"] = ver
        if ver and current:
            c = compare(ver, current)
            x["status"] = "old" if c < 0 else "newer" if c > 0 else "same"
            x["why"] = {"old": f"версия {ver} старше вашей ({current})",
                        "same": "та же версия, что и у вас",
                        "newer": f"версия {ver} новее вашей — не удаляйте, пока не обновились"}[x["status"]]
            continue
        if x["kind"] in ("exe", "bin") and run_size is not None and x["size"] == run_size:
            try:                                      # одинаковый размер — возможно, точно такая же копия
                if not run_hash:
                    run_hash.append(sha(running))
                if sha(x["path"]) == run_hash[0]:
                    x["status"], x["why"] = "same", "точно такая же копия, как запущенная"
                    continue
            except OSError:
                pass
        if ref_mtime is not None and x["_mt"] < ref_mtime - DATE_SLACK:
            x["status"], x["why"] = "old", "скачан раньше запущенной копии (версию определить не удалось)"
        elif ref_mtime is not None and x["_mt"] > ref_mtime + DATE_SLACK:
            x["status"], x["why"] = "unknown", "скачан позже запущенной копии — возможно, новее; версию определить не удалось"
        else:
            x["status"], x["why"] = "unknown", "версию определить не удалось"

    by_name = {(x["dir"], x["name"].lower()): x for x in items}
    for x in items:
        if x["kind"] == "leftover":
            x["status"], x["why"] = "junk", "остаток обновления или недокачанный файл"
        elif x["kind"] == "sha":
            partner = by_name.get((x["dir"], x["name"][:-len(".sha256")].lower()))
            if partner is None:
                x["status"], x["why"] = "junk", "контрольная сумма без самого файла"
            else:
                x["status"], x["version"] = partner["status"], partner["version"]
                x["why"] = "контрольная сумма к файлу «" + partner["name"] + "»"

    for x in items:
        x["checked"] = x["status"] in ("old", "junk")
        x.pop("_mt", None)
    items.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 9), x["dir"].lower(), x["name"].lower()))
    return {"items": items, "dirs": [str(d) for d in dirs], "truncated": truncated,
            "total": sum(x["size"] for x in items), "to_delete": sum(x["size"] for x in items if x["checked"])}


# ── удаление ──────────────────────────────────────────────────────────────────
def delete(found: dict, ids) -> dict:
    """Удалить файлы с этими идентификаторами — только из свежего поиска `found`. Чужие и пропавшие пропускаем."""
    want = {str(i) for i in (ids or [])}
    by_id = {x["id"]: x for x in found.get("items", [])}
    done, failed, freed = [], [], 0
    for i in want:
        x = by_id.get(i)
        if x is None:
            failed.append({"id": i, "name": "", "why": "такого файла уже нет в списке — обновите поиск"})
            continue
        p = x["path"]
        try:
            if os.path.islink(p) or not os.path.isfile(p):
                raise OSError("это уже не обычный файл")
            try:
                os.chmod(p, 0o666)                   # «только чтение» в Windows мешает удалению
            except OSError:
                pass
            os.remove(p)
            done.append({"id": i, "name": x["name"]})
            freed += x["size"]
        except PermissionError:
            failed.append({"id": i, "name": x["name"], "why": "файл занят или нет прав — закройте программу, которая его использует"})
        except OSError as e:
            failed.append({"id": i, "name": x["name"], "why": str(e) or "не удалось удалить"})
    return {"deleted": done, "failed": failed, "freed": freed}

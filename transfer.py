# -*- coding: utf-8 -*-
"""
Перенос данных между устройствами (часть Moodle Offline): ПК → телефон и обратно.

Два способа, одна и та же «посылка»:
  • файл-архив (.zip): на одном устройстве «Собрать архив», на другом «Импорт из файла»;
  • Wi-Fi: одно устройство на 15 минут раздаёт данные по адресу с одноразовым кодом (+ QR), другое забирает
    только то, чего у него нет или что изменилось.

Что переносится (виды — CATS): настройки, список курсов для офлайна, расписание, календарь и сроки, лента
«Что нового», элективы и (по желанию) материалы курсов.
Что НЕ переносится никогда: пароль и сохранённый вход (session.bin), токены и адреса уведомлений (notify.bin),
ключи и настройки нейросетей (ai.bin, settings["ai"] — нет в SETTINGS_KEYS),
токен и настройки доступа для внешних нейросетей (mcp.bin, settings["mcp"] — нет в SETTINGS_KEYS),
cookie, имя пользователя, пути папок и всё остальное из settings.json, кроме списка SETTINGS_KEYS.
Принимающая сторона ничего не удаляет: только добавляет и обновляет; свежее у неё не затирается.

Без сторонних библиотек (http.server, urllib, zipfile). Moodle здесь не трогается.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from httpbind import QuickHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import moodle_offline as mo

FORMAT = 1
CHUNK = 256 * 1024
SMALL = 1 << 20                       # файлы до 1 МБ сверяем по sha256, большие — по размеру и дате
TTL = 15 * 60                         # Wi-Fi-раздача живёт 15 минут
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"    # без похожих символов (0/O, 1/I/L)
CODE_LEN = 8
MAX_BAD_TRIES = 10                    # столько неверных кодов — и раздача останавливается

# id, название, подсказка
CATS = [
    ("settings", "Настройки", "группа, правила скачивания, выбранные курсы и расписание — без паролей и входа"),
    ("courses", "Список курсов для офлайна", "чтобы без входа были видны курсы и оглавления"),
    ("schedule", "Расписание", "папка «Расписание» и список групп СевГУ"),
    ("calendar", "Календарь и сроки", "сроки из календаря Moodle и отметки «сделано»"),
    ("news", "Лента «Что нового»", ""),
    ("electives", "Элективы", "сохранённый список вариантов"),
    ("notes", "Заметки", "ваши заметки к курсам и общий блокнот (сливаются: берётся более новая копия каждой)"),
    ("materials", "Материалы курсов", "все скачанные курсы — файлов может быть очень много"),
]
CAT_IDS = [c[0] for c in CATS]
FILE_LEVEL = ("materials", "notes")   # эти виды сливаются по файлам («новее побеждает»), остальные — целиком по самому свежему файлу
DEFAULT_CATS = list(CAT_IDS)   # материалов может быть много, но это главное, что носят на телефон; размер виден в окне до начала

# Из settings.json переносится только это (остальное — вход, пути, прокси, токены — остаётся на своём устройстве).
SETTINGS_KEYS = ("rules", "layout_dir", "layout_file", "mode", "original_names", "index_in_mode2", "course_ids",
                 "schedule_group", "schedule_sources", "schedule_urls", "schedule_pdf", "schedule_auto",
                 "schedule_auto_range", "schedule_excel", "news", "calendar", "electives_url", "keep_versions", "save_grades")
_SECRET_KEY = re.compile(r"token|secret|passw|webhook|telegram|cookie|chat_id|bot_|api_?key", re.I)

SERVICE_TOP = {"_бэкапы", "_debug", "Расписание", "Календарь", "Элективы", "Заметки", "снимки", "Перенос"}   # в корне папки сохранения — не курсы
SKIP_NAMES = {"desktop.ini", "Thumbs.db", ".DS_Store"}
SKIP_SUFFIX = (".tmp", ".part")


class TransferError(Exception):
    pass


# ════════════════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ════════════════════════════════════════════════════════════════════════════
def scrub(obj):
    """Выбрасывает всё, что похоже на секрет (на всякий случай — даже внутри разрешённых настроек)."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def safe_settings(settings: dict) -> dict:
    return {k: scrub(settings[k]) for k in SETTINGS_KEYS if k in (settings or {})}


def apply_settings(incoming: dict, current: dict) -> list:
    """Кладёт разрешённые настройки в current; возвращает, какие ключи изменились."""
    changed = []
    for k in SETTINGS_KEYS:
        if isinstance(incoming, dict) and k in incoming:
            val = scrub(incoming[k])
            if current.get(k) != val:
                current[k] = val
                changed.append(k)
    return changed


# ════════════════════════════════════════════════════════════════════════════
#  ЧТО ЛЕЖИТ НА ДИСКЕ
# ════════════════════════════════════════════════════════════════════════════
def _walk(base: Path, prefix: str = "", skip_top=()):
    """(относительный путь с «/», полный путь) для всех нужных файлов под base."""
    root = mo.fsp(base)
    if not os.path.isdir(root):
        return
    for dirpath, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        top = rel_dir == "."
        dirs[:] = sorted(d for d in dirs if not (top and d in skip_top) and d != "__pycache__")
        for f in sorted(files):
            if f in SKIP_NAMES or f.endswith(SKIP_SUFFIX):
                continue
            rel = f if top else os.path.join(rel_dir, f)
            yield prefix + rel.replace(os.sep, "/"), os.path.join(dirpath, f)


def _single(base: Path, name: str):
    p = base / name
    if os.path.isfile(mo.fsp(p)):
        yield name, mo.fsp(p)


def iter_cat(cat: str, out: Path, data: Path):
    """(root, rel, полный путь) по виду переносимых данных; root: «out» — папка сохранения, «data» — папка данных."""
    if cat == "courses":
        for rel, full in _single(data, "offline.json"):
            yield "data", rel, full
        for sub in ("structure", "covers"):
            for rel, full in _walk(data / sub, sub + "/"):
                yield "data", rel, full
        for rel, full in _single(out, ".moodle_grades.json"):      # оценки по курсам — к списку курсов
            yield "out", rel, full
    elif cat == "schedule":
        for rel, full in _walk(out / "Расписание", "Расписание/", skip_top=("Поделиться",)):   # готовые файлы «для друзей» не переносим
            yield "out", rel, full
        for rel, full in _single(data, "sevsu_groups.json"):
            yield "data", rel, full
    elif cat == "calendar":
        for rel, full in _walk(out / "Календарь", "Календарь/"):
            yield "out", rel, full
        for rel, full in _single(out, ".moodle_deadlines.json"):
            yield "out", rel, full
    elif cat == "news":
        for rel, full in _single(out, ".moodle_news.json"):
            yield "out", rel, full
    elif cat == "electives":
        for rel, full in _walk(out / "Элективы", "Элективы/"):
            yield "out", rel, full
    elif cat == "notes":
        for rel, full in _walk(out / "Заметки", "Заметки/"):
            yield "out", rel, full
    elif cat == "materials":
        try:
            tops = sorted(os.scandir(mo.fsp(out)), key=lambda e: e.name)
        except OSError:
            return
        for e in tops:
            if e.name.startswith((".", "_")) or e.name in SERVICE_TOP or not e.is_dir():
                continue
            for rel, full in _walk(out / e.name, e.name + "/"):
                yield "out", rel, full


def prepared(rel: str, full: str) -> bytes | None:
    """Файлы, которые перед отправкой чистим от лишнего. Сейчас — offline.json: из него убираем логин учётной записи
    (оставляем только имя для приветствия и список курсов). None — файл отдаётся как есть."""
    if rel == "offline.json":
        try:
            with open(full, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if isinstance(d, dict):
            d.pop("username", None)
            return json.dumps(d, ensure_ascii=False).encode("utf-8")
    return None


def _sha256(full: str) -> str:
    h = hashlib.sha256()
    with open(full, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(out: Path, data: Path, cats=None) -> dict:
    """Сколько файлов и байт в каждом виде — для окна выбора (без хэшей, быстро)."""
    res = {}
    for cat in (cats or CAT_IDS):
        if cat == "settings":
            continue
        n = b = 0
        stamp = 0
        for _r, _rel, full in iter_cat(cat, out, data):
            try:
                st = os.stat(full)
            except OSError:
                continue
            n += 1
            b += st.st_size
            stamp = max(stamp, int(st.st_mtime))
        res[cat] = {"files": n, "bytes": b, "stamp": stamp}
    return res


def build_manifest(out: Path, data: Path, cats, settings: dict | None = None, rep=None, control=None) -> dict:
    cats = [c for c in CAT_IDS if c in set(cats or [])]
    files, summary = [], {}
    for cat in cats:
        if cat == "settings":
            continue
        n = b = stamp = 0
        for root, rel, full in iter_cat(cat, out, data):
            if control:
                control.checkpoint()
            try:
                st = os.stat(full)
                size = st.st_size
                pre = prepared(rel, full)
                if pre is not None:
                    size, sha = len(pre), hashlib.sha256(pre).hexdigest()
                else:
                    sha = _sha256(full) if size <= SMALL else ""
            except OSError:
                continue
            files.append({"c": cat, "r": root, "p": rel, "s": size, "m": int(st.st_mtime), "h": sha})
            n += 1
            b += size
            stamp = max(stamp, int(st.st_mtime))
        summary[cat] = {"files": n, "bytes": b, "stamp": stamp}
    man = {"format": FORMAT, "app": mo.APP_NAME, "version": mo.VERSION,
           "created": datetime.now().strftime("%d.%m.%Y %H:%M"), "device": _device_name(), "cats": summary, "files": files}
    if "settings" in cats:
        man["settings"] = safe_settings(settings or {})
    return man


def _device_name() -> str:
    try:
        return socket.gethostname()[:60]
    except OSError:
        return ""


# ════════════════════════════════════════════════════════════════════════════
#  ПЛАН: ЧТО ВЗЯТЬ У ДРУГОГО УСТРОЙСТВА
# ════════════════════════════════════════════════════════════════════════════
def safe_rel(rel: str) -> str | None:
    """Путь из чужой посылки → безопасный относительный путь с «/», либо None (абсолютный, «..», диск, мусор)."""
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            return None
        if os.name == "nt" and (re.search(r'[<>:"|?*]', p) or p != p.strip(" ")):
            return None      # в Windows такие имена не бывают (и «C:» не должен стать диском)
    return "/".join(parts)


def local_path(root: str, rel: str, out: Path, data: Path) -> Path | None:
    r = safe_rel(rel)
    if r is None or root not in ("out", "data"):
        return None
    base = out if root == "out" else data
    p = base.joinpath(*r.split("/"))
    try:
        if os.path.commonpath([os.path.abspath(p), os.path.abspath(base)]) != os.path.abspath(base):
            return None
    except ValueError:
        return None
    return p


def _differs(f: dict, p: Path) -> bool:
    try:
        st = os.stat(mo.fsp(p))
    except OSError:
        return True
    if st.st_size != f["s"]:
        return True
    if f.get("h") and st.st_size <= SMALL:
        try:
            return _sha256(mo.fsp(p)) != f["h"]
        except OSError:
            return True
    # большие файлы не хэшируем: тот же размер и у источника дата не новее — считаем тем же файлом
    # (при передаче мы ставим файлу ту же дату, так что повторно он не качается)
    return int(f.get("m") or 0) > int(st.st_mtime) + 2


def _mtime(p: Path) -> int:
    try:
        return int(os.stat(mo.fsp(p)).st_mtime)
    except OSError:
        return 0


def make_plan(man: dict, cats, out: Path, data: Path, force: bool = False) -> dict:
    """Что именно скачать/распаковать. Ничего не удаляет; на устройстве более свежее — не трогает (без force)."""
    check_manifest(man)
    want = set(cats or [])
    by_cat: dict = {}
    for f in man.get("files", []):
        by_cat.setdefault(f.get("c"), []).append(f)
    items, notes, bad = [], [], 0
    for cat in CAT_IDS:
        if cat == "settings" or cat not in want or cat not in by_cat:
            continue
        title = dict((c[0], c[1]) for c in CATS)[cat]
        todo, newest_remote, newest_local = [], 0, 0
        for f in by_cat[cat]:
            p = local_path(f.get("r"), f.get("p"), out, data)
            if p is None:
                bad += 1
                continue
            newest_remote = max(newest_remote, int(f.get("m") or 0))
            newest_local = max(newest_local, _mtime(p))
            if not _differs(f, p):
                continue
            if cat in FILE_LEVEL:
                if os.path.exists(mo.fsp(p)) and not force and _mtime(p) >= int(f.get("m") or 0):
                    notes.append(f"{title}: «{f['p']}» на этом устройстве новее — оставил как есть")
                    continue
            todo.append(f)
        if not todo:
            continue
        if cat not in FILE_LEVEL and not force and newest_local and newest_local >= newest_remote:
            notes.append(f"{title}: на этом устройстве данные такие же или свежее — не трогаю")
            continue
        items += todo
    return {"items": items, "bytes": sum(int(i["s"]) for i in items), "notes": notes, "bad": bad,
            "settings": man.get("settings") if "settings" in want else None}


def check_manifest(man) -> None:
    if not isinstance(man, dict) or man.get("app") != mo.APP_NAME or not isinstance(man.get("files"), list):
        raise TransferError("это не данные Moodle Offline")
    if int(man.get("format") or 0) > FORMAT:
        raise TransferError("данные созданы более новой версией программы — обновите программу на этом устройстве")


# ════════════════════════════════════════════════════════════════════════════
#  ЗАПИСЬ ФАЙЛА НА ДИСК (общая для архива и Wi-Fi)
# ════════════════════════════════════════════════════════════════════════════
def check_space(plan: dict, out: Path, data: Path) -> None:
    need = plan["bytes"]
    if not need:
        return
    for base in (out, data):
        probe = base
        while not os.path.exists(mo.fsp(probe)) and probe.parent != probe:
            probe = probe.parent
        try:
            free = shutil.disk_usage(mo.fsp(probe)).free
        except OSError:
            continue
        if free < need * 1.05 + (50 << 20):
            raise TransferError(f"не хватает места: нужно около {need >> 20} МБ, свободно {free >> 20} МБ")
        return


def store(dest: Path, item: dict, chunks, rep=None, control=None, resume_from: int = 0) -> None:
    """Пишет файл через «.part», сверяет размер (и sha256 у малых), ставит дату как у исходного и переименовывает."""
    mo.fmkdir(dest.parent)
    part = dest.with_name(dest.name + ".part")
    size = int(item["s"])
    mode = "ab" if resume_from else "wb"
    done = resume_from
    if rep:
        rep.file_start(item["p"].rsplit("/", 1)[-1], size, resume_from)
    ok = False
    try:
        with mo.fopen(part, mode) as f:
            for chunk in chunks:
                if control:
                    control.checkpoint()
                f.write(chunk)
                done += len(chunk)
                if rep:
                    rep.file_progress(done)
        if done != size:
            raise TransferError(f"получено {done} байт из {size}")
        if item.get("h") and size <= SMALL and _sha256(mo.fsp(part)) != item["h"]:
            raise TransferError("контрольная сумма не совпала")
        mo.freplace(part, dest)
        m = int(item.get("m") or 0)
        if m:
            try:
                os.utime(mo.fsp(dest), (m, m))
            except OSError:
                pass
        ok = True
    finally:
        if rep:
            rep.file_end("ok" if ok else "error")
        if not ok and not resume_from_allowed(item):
            mo.funlink(part)


def resume_from_allowed(item) -> bool:
    return int(item.get("s") or 0) > SMALL        # большие файлы докачиваются с места обрыва, маленькие — с нуля


# ════════════════════════════════════════════════════════════════════════════
#  АРХИВ
# ════════════════════════════════════════════════════════════════════════════
_STORED_EXT = (".zip", ".7z", ".rar", ".gz", ".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".jpg", ".jpeg",
               ".png", ".gif", ".webp", ".mp3", ".mp4", ".mkv", ".avi", ".mov", ".apk", ".msi", ".exe")


def write_archive(fp, man: dict, out: Path, data: Path, rep=None, control=None) -> dict:
    """Пишет посылку в открытый для записи файл/поток. Возвращает {"files": n, "bytes": b, "skipped": [...]}."""
    n = b = 0
    skipped = []
    with zipfile.ZipFile(fp, "w", allowZip64=True) as zf:
        zf.writestr("manifest.json", json.dumps(man, ensure_ascii=False), compress_type=zipfile.ZIP_DEFLATED)
        for f in man["files"]:
            if control:
                control.checkpoint()
            p = local_path(f["r"], f["p"], out, data)
            if p is None or not os.path.isfile(mo.fsp(p)):
                skipped.append(f["p"])
                continue
            comp = zipfile.ZIP_STORED if f["p"].lower().endswith(_STORED_EXT) else zipfile.ZIP_DEFLATED
            try:
                zi = zipfile.ZipInfo(f"{f['r']}/{f['p']}", time.localtime(f["m"])[:6] if f["m"] > 315532800 else (1980, 1, 1, 0, 0, 0))
                zi.compress_type = comp
                zi.flag_bits |= 0x800
                pre = prepared(f["p"], mo.fsp(p))
                if pre is not None:
                    zf.writestr(zi, pre)
                else:
                    with open(mo.fsp(p), "rb") as src, zf.open(zi, "w", force_zip64=True) as dst:
                        for chunk in iter(lambda: src.read(CHUNK), b""):
                            dst.write(chunk)
                n += 1
                b += f["s"]
            except OSError as e:
                skipped.append(f"{f['p']} ({mo.describe_error(e)})")
            if rep and n % 200 == 0:
                rep.log(f"  в архиве файлов: {n}")
    return {"files": n, "bytes": b, "skipped": skipped}


def export_archive(dest: Path, out: Path, data: Path, cats, settings: dict, rep, control) -> dict:
    rep.log("Собираю список файлов…")
    man = build_manifest(out, data, cats, settings, rep, control)
    total = sum(v["files"] for v in man["cats"].values())
    rep.log(f"К переносу: файлов {total}, {_fmt_size(sum(v['bytes'] for v in man['cats'].values()))}")
    mo.fmkdir(dest.parent)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with mo.fopen(tmp, "wb") as fp:
            res = write_archive(fp, man, out, data, rep, control)
        mo.freplace(tmp, dest)
    except BaseException:
        mo.funlink(tmp)
        raise
    res["path"] = str(dest)
    res["size"] = mo.fsize(dest)
    return res


def read_archive_manifest(zip_path: Path) -> dict:
    try:
        with zipfile.ZipFile(mo.fsp(zip_path)) as zf:
            man = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as e:
        raise TransferError("это не архив переноса Moodle Offline (или файл повреждён)") from e
    check_manifest(man)
    return man


def import_archive(zip_path: Path, cats, out: Path, data: Path, rep, control, force: bool = False) -> dict:
    man = read_archive_manifest(zip_path)
    plan = make_plan(man, cats, out, data, force)
    check_space(plan, out, data)
    done = failed = 0
    errors = []
    with zipfile.ZipFile(mo.fsp(zip_path)) as zf:
        names = set(zf.namelist())
        for it in plan["items"]:
            control.checkpoint()
            dest = local_path(it["r"], it["p"], out, data)
            member = f"{it['r']}/{it['p']}"
            try:
                if member not in names:
                    raise TransferError("файла нет в архиве")
                with zf.open(member) as src:
                    store(dest, it, iter(lambda: src.read(CHUNK), b""), rep, control)
                done += 1
            except (TransferError, OSError, zipfile.BadZipFile) as e:
                failed += 1
                errors.append(f"{it['p']}: {mo.describe_error(e)}")
                rep.log(f"  ✗ {it['p']}: {mo.describe_error(e)}", "warn")
    return _result(man, plan, done, failed, errors)


def _result(man, plan, done, failed, errors) -> dict:
    return {"copied": done, "failed": failed, "errors": errors[:20], "notes": plan["notes"][:20], "bad": plan["bad"],
            "settings": plan["settings"], "bytes": plan["bytes"], "from": man.get("device", ""),
            "created": man.get("created", ""), "total": len(plan["items"])}


# ════════════════════════════════════════════════════════════════════════════
#  WI-FI: РАЗДАЧА
# ════════════════════════════════════════════════════════════════════════════
def is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_private or a.is_loopback or a.is_link_local or (a in ipaddress.ip_network("100.64.0.0/10"))


_HOST_SLOW_UNTIL = 0.0      # до какого момента не спрашивать у системы адреса по имени компьютера (раньше он отвечал слишком долго)


def _hostname_ips(timeout: float = 1.5) -> list:
    """Адреса по имени компьютера — дополнение к основному (если сетевых карт несколько). Ответ системы может занять десятки
    секунд (macOS без сети: имя вида «….local» ищется через mDNS, сервер сборки GitHub), а окно переноса ждёт этот список.
    Поэтому ждём недолго; не дождались — это дополнение в ближайшие 5 минут пропускаем."""
    global _HOST_SLOW_UNTIL
    if time.time() < _HOST_SLOW_UNTIL:
        return []
    out: list = []

    def run():
        try:
            out.extend(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        except OSError:
            pass
    th = threading.Thread(target=run, daemon=True, name="transfer-hostname")
    th.start()
    th.join(timeout)
    if th.is_alive():
        _HOST_SLOW_UNTIL = time.time() + 300
    return list(out)


def local_ips() -> list:
    """Адреса этого устройства в локальной сети: сначала домашние (192.168…), потом остальные частные."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    found.update(_hostname_ips())
    ips = [ip for ip in found if is_lan(ip) and not ip.startswith(("127.", "169.254."))]

    def rank(ip):
        return (0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2 if ip.startswith("172.") else 3, ip)
    return sorted(ips, key=rank)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def show_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def norm_code(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


class Sender:
    """Раздаёт посылку по HTTP внутри локальной сети. Только чтение, только по коду, только из частных адресов."""

    def __init__(self, out: Path, data: Path, cats, settings: dict, rep=None, ttl: int = TTL, bind: str = "0.0.0.0", port: int = 0):
        self.out, self.data, self.rep, self.ttl = out, data, rep, ttl
        self.cats = [c for c in CAT_IDS if c in set(cats or [])]
        self.settings = settings
        self.bind, self.port = bind, port
        self.code = new_code()
        self.man: dict = {}
        self.httpd = None
        self.expires = 0.0
        self.clients: set = set()
        self.served_files = 0
        self.served_bytes = 0
        self.bad = 0
        self.locked = False
        self._timer = None

    # ── управление ──
    def start(self) -> dict:
        self.man = build_manifest(self.out, self.data, self.cats, self.settings)
        sender = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_GET(self):
                sender._handle(self)

            def do_HEAD(self):
                sender._handle(self, head=True)

        self.httpd = QuickHTTPServer((self.bind, self.port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.expires = time.time() + self.ttl
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="transfer-send").start()
        self._timer = threading.Timer(self.ttl, self.stop)
        self._timer.daemon = True
        self._timer.start()
        return self.info()

    @property
    def active(self) -> bool:
        return self.httpd is not None and not self.locked and time.time() < self.expires

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self.httpd:
            try:
                threading.Thread(target=self.httpd.shutdown, daemon=True).start()
                self.httpd.server_close()
            except OSError:
                pass
            self.httpd = None

    def info(self, ip: str | None = None) -> dict:
        ips = local_ips() or ["127.0.0.1"]
        ip = ip if ip in ips else ips[0]
        return {"active": self.active, "port": self.port, "code": show_code(self.code), "ips": ips, "ip": ip,
                "url": f"http://{ip}:{self.port}/{self.code}/", "expires": int(self.expires),
                "left": max(0, int(self.expires - time.time())), "clients": sorted(self.clients),
                "files": self.served_files, "bytes": self.served_bytes, "locked": self.locked,
                "cats": self.cats, "summary": self.man.get("cats", {})}

    def _log(self, msg, level="info"):
        if self.rep:
            self.rep.log(msg, level)

    # ── запросы ──
    def _deny(self, h, ip, code=404):
        h.send_response(code)
        h.send_header("Content-Length", "0")
        h.end_headers()

    def _handle(self, h, head=False):
        ip = h.client_address[0]
        if not self.active or not is_lan(ip):
            return self._deny(h, ip, 403)
        parts = [unquote(x) for x in urlparse(h.path).path.split("/") if x != ""]
        if not parts or not secrets.compare_digest(norm_code(parts[0]), self.code):
            self.bad += 1
            if self.bad >= MAX_BAD_TRIES:
                self.locked = True
                self._log("Передача по Wi-Fi остановлена: слишком много неверных кодов.", "warn")
                threading.Thread(target=self.stop, daemon=True).start()
            return self._deny(h, ip)
        rest = parts[1:]
        if ip not in self.clients:
            self.clients.add(ip)
            self._log(f"К передаче подключилось устройство {ip}.")
        try:
            if not rest:
                return self._send(h, 200, _PAGE.format(code=show_code(self.code), device=self.man.get("device") or "компьютер").encode("utf-8"),
                                  "text/html; charset=utf-8", head)
            if rest == ["manifest.json"]:
                return self._send(h, 200, json.dumps(self.man, ensure_ascii=False).encode("utf-8"),
                                  "application/json; charset=utf-8", head)
            if rest[0] == "f" and len(rest) == 2 and rest[1].isdigit():
                return self._file(h, int(rest[1]), head)
            if rest == ["archive.zip"]:
                return self._archive(h, head)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        self._deny(h, ip)

    @staticmethod
    def _send(h, status, body, ctype, head=False):
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        if not head:
            h.wfile.write(body)

    def _file(self, h, idx, head):
        files = self.man.get("files", [])
        if idx >= len(files):
            return self._deny(h, h.client_address[0])
        f = files[idx]
        p = local_path(f["r"], f["p"], self.out, self.data)
        if p is None or not os.path.isfile(mo.fsp(p)):
            return self._deny(h, h.client_address[0])
        pre = prepared(f["p"], mo.fsp(p))
        size = len(pre) if pre is not None else os.path.getsize(mo.fsp(p))
        start = 0
        m = re.match(r"bytes=(\d+)-", h.headers.get("Range") or "")
        if m and int(m.group(1)) < size:
            start = int(m.group(1))
        h.send_response(206 if start else 200)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Content-Length", str(size - start))
        if start:
            h.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        h.send_header("Accept-Ranges", "bytes")
        h.end_headers()
        if head:
            return
        if pre is not None:
            h.wfile.write(pre[start:])
            self.served_bytes += len(pre) - start
            self.served_files += 1
            return
        with open(mo.fsp(p), "rb") as src:
            src.seek(start)
            for chunk in iter(lambda: src.read(CHUNK), b""):
                h.wfile.write(chunk)
                self.served_bytes += len(chunk)
        self.served_files += 1

    def _archive(self, h, head):
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Disposition", 'attachment; filename="MoodleOffline-transfer.zip"')
        h.end_headers()
        if head:
            return

        class _W:                         # у сокета нет tell/seek — zipfile умеет писать и в такой поток
            def __init__(s, w):
                s.w = w

            def write(s, b):
                s.w.write(b)
                self.served_bytes += len(b)
                return len(b)

            def flush(s):
                s.w.flush()
        write_archive(_W(h.wfile), self.man, self.out, self.data)
        self.served_files += len(self.man.get("files", []))


_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Moodle Offline — перенос данных</title>
<style>body{{font:16px system-ui,sans-serif;max-width:34rem;margin:2rem auto;padding:0 1rem;color:#222}}
a.b{{display:block;background:#2563eb;color:#fff;text-decoration:none;padding:14px;border-radius:12px;text-align:center;margin:1rem 0}}
code{{background:#eee;padding:2px 6px;border-radius:6px}}</style></head><body>
<h2>Данные с устройства «{device}»</h2>
<p>Код: <b>{code}</b>. Страница видна только в вашей локальной сети и только по этому коду; передача остановится сама через 15 минут.</p>
<p><b>Проще всего:</b> откройте приложение Moodle Offline → «Настройки» → «Дополнительно» → «Перенос данных» → «Принять» и вставьте адрес этой страницы.</p>
<p>Или скачайте всё одним файлом и откройте его в приложении («Перенос данных» → «Принять» → «Из файла»):</p>
<a class="b" href="archive.zip">Скачать архив (.zip)</a></body></html>"""


# ════════════════════════════════════════════════════════════════════════════
#  WI-FI: ПРИЁМ
# ════════════════════════════════════════════════════════════════════════════
_ADDR = re.compile(r"^(?:https?://)?(?P<host>[A-Za-z0-9.\-]+):(?P<port>\d{2,5})(?:/(?P<code>[A-Za-z0-9\-]*))?/?$")


def parse_address(text: str) -> tuple:
    """«http://192.168.1.5:8812/7QK4M2XD/», «192.168.1.5:8812 7QK4-M2XD» и т. п. → (база, код)."""
    t = (text or "").strip()
    code = ""
    m = re.match(r"^(\S+)\s+([A-Za-z0-9\-]{8,9})$", t)
    if m:
        t, code = m.group(1), norm_code(m.group(2))
    m = _ADDR.match(t.rstrip("/"))
    if not m:
        raise TransferError("не понял адрес. Он выглядит так: 192.168.1.5:8812 и код, или вся ссылка целиком")
    code = code or norm_code(m.group("code") or "")
    if len(code) != CODE_LEN:
        raise TransferError("в адресе нет кода из 8 символов (например 7QK4-M2XD)")
    host = m.group("host")
    try:
        resolved = socket.gethostbyname(host)
    except OSError:
        raise TransferError(f"не нашёл устройство «{host}» в сети") from None
    if not is_lan(resolved):
        raise TransferError("принимать можно только из своей локальной сети (адрес должен быть вида 192.168.x.x, 10.x.x.x)")
    return f"http://{host}:{m.group('port')}/{code}", code


def _open(url: str, headers=None, timeout: int = 20):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # в локальную сеть — всегда напрямую
    return opener.open(urllib.request.Request(url, headers=headers or {}), timeout=timeout)


def fetch_manifest(base: str) -> dict:
    try:
        with _open(base + "/manifest.json") as r:
            man = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise TransferError("устройство не принимает этот код — проверьте код и что раздача ещё включена" if e.code in (403, 404)
                            else f"устройство ответило ошибкой {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TransferError(f"нет связи с устройством: {mo.describe_error(e)}. Оба устройства в одной Wi-Fi сети? "
                            f"Не мешает ли брандмауэр Windows (разрешите программе доступ в частной сети)?") from None
    check_manifest(man)
    return man


def pull(base: str, cats, out: Path, data: Path, rep, control, force: bool = False, man: dict | None = None) -> dict:
    man = man or fetch_manifest(base)
    plan = make_plan(man, cats, out, data, force)
    check_space(plan, out, data)
    rep.log(f"С устройства «{man.get('device', '')}»: нужно файлов {len(plan['items'])}, {_fmt_size(plan['bytes'])}")
    index = {id(f): i for i, f in enumerate(man["files"])}      # номер файла в посылке — по нему его просят у раздающего
    done = failed = 0
    errors = []
    for it in plan["items"]:
        control.checkpoint()
        dest = local_path(it["r"], it["p"], out, data)
        url = f"{base}/f/{index[id(it)]}"
        ok = False
        for attempt in range(3):
            try:
                part = dest.with_name(dest.name + ".part")
                have = os.path.getsize(mo.fsp(part)) if resume_from_allowed(it) and os.path.exists(mo.fsp(part)) else 0
                if have >= int(it["s"]):
                    have = 0
                r = _open(url, {"Range": f"bytes={have}-"} if have else None, 30)
                if have and r.status != 206:
                    have = 0
                store(dest, it, iter(lambda: r.read(CHUNK), b""), rep, control, have)
                r.close()
                ok = True
                break
            except mo.Stopped:
                raise
            except (TransferError, OSError, urllib.error.URLError) as e:
                if attempt == 2:
                    errors.append(f"{it['p']}: {mo.describe_error(e)}")
                    rep.log(f"  ✗ {it['p']}: {mo.describe_error(e)}", "warn")
                else:
                    time.sleep(1 + attempt * 2)
        done += ok
        failed += not ok
        if failed >= 5 and not done:
            raise TransferError("устройство перестало отвечать — передача остановлена")
    return _result(man, plan, done, failed, errors)


def _fmt_size(n: int) -> str:
    n = float(n)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or u == "ГБ":
            return f"{n:.0f} {u}" if u == "Б" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} ГБ"

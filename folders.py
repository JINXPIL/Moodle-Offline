"""Выбор папки внутри программы (1.35.1).

На компьютере «…» рядом с папкой сохранения открывает системное окно выбора папки. На телефоне такого окна, которое
вернуло бы обычный путь, нет, поэтому здесь — свой «проводник» только по папкам: список подпапок, «Наверх»,
«Новая папка», «Выбрать эту папку». Работает и в приложении .apk (при доступе ко всем файлам), и в Termux.

Правила безопасности: смотрим только внутри «корней» (общая память телефона, карты памяти, папка приложения), отдаём
только имена папок (не файлов), скрытые папки (с точкой) не показываем, имя новой папки не может содержать путь.
Без Moodle-кода и без сторонних библиотек; сети не трогает.
"""
import os
import re
from pathlib import Path

MAX_ENTRIES = 400          # больше папок в одном списке не отдаём (окно всё равно не прокрутить)
MAX_NAME = 100
_BAD_NAME = re.compile(r"[\x00-\x1f/\\]")


def clean_name(name) -> str:
    """Имя новой папки. Путь внутри имени (`../x`, `a/b`) не принимается: папка создаётся только в открытой."""
    n = str(name or "").strip()
    if not n or n in (".", ".."):
        raise ValueError("Впишите название папки.")
    if _BAD_NAME.search(n):
        raise ValueError("В названии нельзя использовать «/» и «\\».")
    if len(n) > MAX_NAME:
        raise ValueError(f"Название длиннее {MAX_NAME} знаков.")
    return n


def _real(p) -> Path | None:
    try:
        return Path(p).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def volumes(storage="/storage") -> list[Path]:
    """Карты памяти и другие тома: /storage/XXXX-XXXX (общая память `emulated` и `self` — отдельно)."""
    out = []
    try:
        for e in sorted(os.scandir(storage), key=lambda e: e.name.lower()):
            if e.name in ("emulated", "self") or e.name.startswith("."):
                continue
            if e.is_dir():
                out.append(Path(e.path))
    except OSError:
        pass
    return out


def roots(shared: Path | None, home: Path, storage="/storage") -> list[dict]:
    """С чего можно начинать: [{name, path}]. Общая память и её «Документы»/«Загрузки», карты памяти, память приложения."""
    items: list[tuple[str, Path]] = []
    if shared:
        sh = Path(shared)
        items.append(("Память телефона", sh))
        for sub, label in (("Documents", "Документы"), ("Download", "Загрузки")):
            if _is_dir(sh / sub):
                items.append((label, sh / sub))
    for i, v in enumerate(volumes(storage), 1):
        items.append(("Карта памяти" if i == 1 else f"Карта памяти {i}", v))
    items.append(("Память приложения", Path(home)))
    res, seen = [], set()
    for name, p in items:
        r = _real(p)
        if r is None or not _is_dir(r) or str(r) in seen:
            continue
        seen.add(str(r))
        res.append({"name": name, "path": str(r)})
    return res


def _root_of(p: Path, rs: list[dict]) -> dict | None:
    """Самый глубокий корень, внутри которого лежит p (или который совпадает с p)."""
    best = None
    for r in rs:
        rp = Path(r["path"])
        if p == rp or rp in p.parents:
            if best is None or len(Path(best["path"]).parts) < len(rp.parts):
                best = r
    return best


def inside(path, rs: list[dict]) -> Path | None:
    """Настоящий путь папки, если она существует и лежит внутри одного из корней; иначе None."""
    p = _real(path) if path else None
    if p is None or not _is_dir(p) or _root_of(p, rs) is None:
        return None
    return p


def browse(path, rs: list[dict], start=None) -> dict:
    """Что показать в окне выбора. Путь вне корней или не папка — открываем `start` (если годится), иначе первый корень."""
    p = inside(path, rs) or inside(start, rs) or (Path(rs[0]["path"]) if rs else None)
    if p is None:
        return {"ok": False, "error": "Папки недоступны.", "roots": rs}
    root = _root_of(p, rs)
    rp = Path(root["path"])
    dirs, truncated = [], False
    try:
        with os.scandir(p) as it:
            names = []
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        names.append(e.name)
                except OSError:
                    continue
        names.sort(key=lambda s: (s.casefold(), s))
        if len(names) > MAX_ENTRIES:
            truncated, names = True, names[:MAX_ENTRIES]
        dirs = [{"name": n, "path": str(p / n)} for n in names]
    except OSError as e:
        return {"ok": False, "error": f"Не удалось открыть папку: {e.strerror or e}", "roots": rs, "path": str(p)}
    trail = [{"name": root["name"], "path": root["path"]}]
    cur = rp
    try:
        for part in p.relative_to(rp).parts:
            cur = cur / part
            trail.append({"name": part, "path": str(cur)})
    except ValueError:
        pass
    parent = str(p.parent) if p != rp else None
    return {"ok": True, "path": str(p), "parent": parent, "trail": trail, "root": root["name"], "dirs": dirs,
            "truncated": truncated, "can_write": os.access(p, os.W_OK | os.X_OK), "roots": rs}


def make_dir(parent, name, rs: list[dict]) -> Path:
    """Создаёт подпапку в открытой папке; → её путь. Ошибка — ValueError с понятным текстом."""
    base = inside(parent, rs)
    if base is None:
        raise ValueError("Откройте папку из списка и попробуйте ещё раз.")
    new = base / clean_name(name)
    try:
        new.mkdir()
    except FileExistsError:
        raise ValueError("Папка с таким названием уже есть.") from None
    except OSError as e:
        raise ValueError(f"Не удалось создать папку: {e.strerror or e}") from None
    return new


def probe_writable(path: Path) -> bool:
    """Настоящая проверка записи: создать и убрать маленький скрытый файл (`os.access` на телефоне ненадёжен)."""
    f = Path(path) / f".mo-write-{os.getpid()}"
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False
    try:
        os.close(fd)
    finally:
        try:
            f.unlink()
        except OSError:
            pass
    return True

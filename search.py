"""Поиск по скачанным материалам — полностью офлайн.

Индекс лежит рядом с материалами (`.moodle_search.sqlite` в папке сохранения) и читается без интернета.
Ищет и по содержимому (PDF, Word, PowerPoint, Excel, OpenDocument, HTML, текст и код),
и по названиям любых файлов. Без Moodle-кода: папку с файлами ему просто называют.

Как это устроено:
  • файлы режутся на фрагменты — страница PDF, слайд, лист таблицы или блок ~1500 символов;
  • фрагменты кладутся в SQLite FTS5 (если её нет — поиск идёт обычным перебором, медленнее, но работает);
  • индекс обновляется по размеру и времени файла: правится только то, что изменилось;
  • запрос: слова через пробел (все сразу), "точная фраза", -слово (исключить).
    Словоформы ловятся простым совпадением по началу слова («лекция» найдёт «лекции», «лекций»),
    это не полноценная морфология.
Сторонняя библиотека нужна одна и только для PDF — pypdfium2 (движок PDF из Chrome). Там, где её нет (Android), берётся
чисто питоновская pypdf — медленнее, но читает текст. Без обеих PDF ищутся по названию.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

try:  # нужна только для PDF
    import pypdfium2 as pdfium
except Exception:  # noqa: BLE001 — любая проблема с импортом = «библиотеки нет»
    pdfium = None
_PDFIUM_LOCK = threading.Lock()                 # PDFium не потокобезопасен
try:  # запасной вариант для PDF там, где нет pypdfium2 (Android: для него нет сборки). Чистый Python, медленнее
    import pypdf
    import logging as _logging
    _logging.getLogger("pypdf").setLevel(_logging.ERROR)   # про кривые файлы мы пишем сами, без шума в журнале
except Exception:  # noqa: BLE001
    pypdf = None

INDEX_FILE = ".moodle_search.sqlite"
SCHEMA = 1
HL0, HL1 = "\x01", "\x02"                 # маркеры подсветки в сниппетах (UI превращает их в <mark>)
NAME_LOC = "название"                      # «место» фрагмента, созданного из пути и названия файла

# Группы файлов: id → (название, расширения)
GROUPS = {
    "docs":   ("Документы",   (".pdf", ".docx", ".odt")),
    "slides": ("Презентации", (".pptx", ".odp")),
    "tables": ("Таблицы",     (".xlsx", ".ods", ".csv")),
    "pages":  ("Страницы",    (".html", ".htm")),
    "text":   ("Текст и код", (".txt", ".md", ".py", ".c", ".cpp", ".h", ".hpp", ".java", ".cs", ".js", ".sql",
                                ".json", ".xml", ".tex", ".sh", ".bat", ".pas", ".r", ".yaml", ".yml")),
}
OTHER = "other"
OTHER_TITLE = "Остальное"
LEGACY = {".doc": "docx", ".ppt": "pptx", ".xls": "xlsx"}   # старые форматы: содержимое не читаем
EXT_GROUP = {e: g for g, (_t, exts) in GROUPS.items() for e in exts}

DEFAULTS = {
    "auto": True,                               # обновлять индекс после каждой загрузки
    "types": list(GROUPS),                      # содержимое каких групп читать; остальное — только по названию
    "max_mb": 50,                               # файлы крупнее — только по названию
    "max_pages": 500,                           # в PDF читать не больше стольких страниц
    "exclude_courses": [],                      # папки курсов, которые не индексировать
}

BLOCK = 1500                                    # целевой размер фрагмента, символов
BLOCK_HARD = 2600                               # длиннее — режем
MAX_CHARS = 4_000_000                           # потолок текста из одного файла
ZIP_PART_LIMIT = 300 * 2**20                    # защита от «бомб»: распакованный XML крупнее — не читаем
MAX_TERMS = 12

SKIP_ROOT_DIRS = {"расписание", "элективы", "календарь"}     # служебные папки программы (верхний уровень)
GENERATED = {"index.html", "readme.md"}         # оглавление, которое программа кладёт в папку курса


class Stopped(Exception):
    """Индексацию остановили."""


class Unreadable(Exception):
    """Файл не удалось прочитать (причина — человеческим языком)."""


# ═══════════════════════════ настройки ═══════════════════════════
def normalize(cfg) -> dict:
    """Приводит сохранённые настройки к допустимому виду (мусор и неизвестное отбрасывается)."""
    c = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    if isinstance(cfg, dict):
        if "auto" in cfg:
            c["auto"] = bool(cfg["auto"])
        if isinstance(cfg.get("types"), list):
            c["types"] = [g for g in GROUPS if g in cfg["types"]]
        for k, lo, hi in (("max_mb", 1, 1000), ("max_pages", 10, 20000)):
            try:
                c[k] = min(hi, max(lo, int(cfg.get(k, c[k]))))
            except (TypeError, ValueError):
                pass
        if isinstance(cfg.get("exclude_courses"), list):
            c["exclude_courses"] = sorted({str(x) for x in cfg["exclude_courses"] if str(x).strip()})
    return c


def groups_meta() -> list[dict]:
    return [{"key": g, "title": t, "exts": list(e)} for g, (t, e) in GROUPS.items()] + \
           [{"key": OTHER, "title": OTHER_TITLE, "exts": []}]


def have_pdf() -> bool:
    return pdfium is not None or pypdf is not None


def fts_available() -> bool:
    try:
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            return True
        finally:
            db.close()
    except sqlite3.Error:
        return False


# ═══════════════════════════ разбор запроса ═══════════════════════════
_WORD = re.compile(r"[^\W_]+")
_TOK = re.compile(r'(-?)(?:"([^"]*)"|([^\s"]+))')
_CYR = re.compile(r"[а-я]+")
_LAT = re.compile(r"[a-z]+")
_STOP = frozenset("и в во на с со к по о об от до за из у а но не что как для при или же ли бы".split())


def norm(s: str) -> str:
    """Единый вид текста для поиска: нижний регистр, ё = е."""
    return s.lower().replace("ё", "е")


def stem(w: str) -> tuple[str, bool]:
    """(основа, искать_ли_по_началу_слова). Грубая обрезка окончаний — без словарей и морфологии."""
    n = len(w)
    if w.isdigit() or n <= 2:
        return w, False
    if _CYR.fullmatch(w):
        if n >= 8:
            return w[:-3], True
        if n >= 6:
            return w[:-2], True
        if n == 5:
            return w[:-1], True
        if n == 4 and w[-1] in "аяыиуюеоьй":
            return w[:-1], True
        return w, True
    if _LAT.fullmatch(w):
        if n >= 5 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        return w, True
    return w, True


class Query:
    """Разобранный запрос: слова (основа, по_началу), точные фразы и исключения."""

    def __init__(self):
        self.pos: list[tuple[str, bool]] = []
        self.phr: list[list[str]] = []
        self.nterms: list[tuple[str, bool]] = []
        self.nphr: list[list[str]] = []

    @property
    def empty(self) -> bool:
        return not (self.pos or self.phr)

    @property
    def count(self) -> int:
        return len(self.pos) + len(self.phr)


def parse_query(q: str) -> Query:
    pq = Query()
    for m in _TOK.finditer(q or ""):
        neg, quoted, bare = m.group(1) == "-", m.group(2), m.group(3)
        toks = _WORD.findall(norm(quoted if quoted is not None else bare or ""))
        toks = [t[:64] for t in toks]
        if not toks:
            continue
        if len(toks) == 1:
            t = (toks[0], False) if quoted is not None else stem(toks[0])
            (pq.nterms if neg else pq.pos).append(t)
        else:                      # «б-25-2о», "точная фраза" — слова подряд, без обрезки окончаний
            (pq.nphr if neg else pq.phr).append(toks)
    if pq.count > 1:               # «что такое …» — служебные слова не нужны, если есть содержательные
        kept = [t for t in pq.pos if t[0] not in _STOP]
        if kept or pq.phr:
            pq.pos = kept
    del pq.pos[MAX_TERMS:], pq.phr[MAX_TERMS:], pq.nterms[MAX_TERMS:], pq.nphr[MAX_TERMS:]
    return pq


def _fts_term(t: tuple[str, bool]) -> str:
    return f'"{t[0]}"*' if t[1] else f'"{t[0]}"'


def match_expr(pq: Query, mode: str = "and") -> str | None:
    """Строка запроса для FTS5. Только слова из букв и цифр в кавычках — подставить в неё чужой синтаксис нельзя."""
    parts = [_fts_term(t) for t in pq.pos] + ['"' + " ".join(p) + '"' for p in pq.phr]
    if not parts:
        return None
    expr = "(" + (" AND " if mode == "and" else " OR ").join(parts) + ")"
    neg = [_fts_term(t) for t in pq.nterms] + ['"' + " ".join(p) + '"' for p in pq.nphr]
    if neg:
        expr += " NOT (" + " OR ".join(neg) + ")"
    return expr


def _term_pat(t: tuple[str, bool]) -> str:
    p = re.escape(t[0]).replace("е", "[её]")
    return p + (r"[^\W_]*" if t[1] else "")


def _phrase_pat(toks: list[str]) -> str:
    return r"[\W_]+".join(re.escape(x).replace("е", "[её]") for x in toks)


def highlight_re(pq: Query) -> re.Pattern | None:
    alts = [_term_pat(t) for t in pq.pos] + [_phrase_pat(p) for p in pq.phr]
    if not alts:
        return None
    return re.compile(r"(?<![^\W_])(?:" + "|".join(alts) + r")(?![^\W_])", re.I)


def make_snippet(text: str, rx: re.Pattern | None, width: int = 230) -> str:
    """Короткая выдержка вокруг первого совпадения; найденное обёрнуто в HL0…HL1."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    ms = list(rx.finditer(flat)) if rx else []
    if not ms:
        return flat[:width] + ("…" if len(flat) > width else "")
    first = ms[0]
    s = max(0, first.start() - 70)
    if s > 0:
        j = flat.find(" ", s)
        s = j + 1 if 0 <= j < first.start() else s
    e = min(len(flat), max(s + width, first.end() + 20))
    if e < len(flat):
        j = flat.rfind(" ", first.end(), e)
        e = j if j > first.end() else e
    out, pos = [], s
    for m in ms:
        if m.end() <= s:
            continue
        if m.start() >= e:
            break
        a, b = max(m.start(), s), min(m.end(), e)
        out.append(flat[pos:a])
        out.append(HL0 + flat[a:b] + HL1)
        pos = b
    out.append(flat[pos:e])
    return ("…" if s > 0 else "") + "".join(out) + ("…" if e < len(flat) else "")


def _pack(text: str) -> bytes:
    """Текст фрагмента хранится сжатым: кириллица сжимается ~3 раза, индекс получается втрое компактнее."""
    return zlib.compress(text.encode("utf-8"), 6)


def _unpack(blob) -> str:
    return zlib.decompress(blob).decode("utf-8") if isinstance(blob, (bytes, bytearray)) else (blob or "")


def highlight(text: str, rx: re.Pattern | None) -> str:
    return rx.sub(lambda m: HL0 + m.group(0) + HL1, text) if rx and text else text


# ═══════════════════════════ чтение файлов ═══════════════════════════
_CTRL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f﻿  ]")


def clean(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    s = _CTRL.sub("", s).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s)


def split_blocks(text: str, size: int = BLOCK, hard: int = BLOCK_HARD) -> list[str]:
    """Делит текст на куски около `size` символов по границам строк; одну гигантскую строку режет по пробелам."""
    out, cur, n = [], [], 0
    for line in clean(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        while len(line) > hard:
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                out.append("\n".join(cur))
                cur, n = [], 0
            out.append(line[:cut].strip())
            line = line[cut:].strip()
        if n + len(line) > size and cur:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return [b for b in out if b.strip()]


class Ctx:
    """Что нужно читателям файлов: проверка остановки и ограничения."""

    def __init__(self, stop: threading.Event | None = None, max_pages: int = 500):
        self.stop = stop
        self.max_pages = max_pages
        self.note = ""

    def check(self):
        if self.stop is not None and self.stop.is_set():
            raise Stopped()


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter(root, local: str):
    """Все вложенные элементы с таким именем тега в любом пространстве имён."""
    return (e for e in root.iter() if isinstance(e.tag, str) and _ln(e.tag) == local)


R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
ODF_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
ODF_FIELDS = {"page-number", "page-count", "date", "time", "file-name", "sheet-name"}   # поля подстановки, не текст


def _zopen(zf: zipfile.ZipFile, name: str):
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise Unreadable(f"в файле нет «{name}» — повреждён или не тот формат") from None
    if info.file_size > ZIP_PART_LIMIT:
        raise Unreadable("внутри слишком большой XML")
    return zf.open(info)


def _zip(path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise Unreadable("не открывается как Office-документ (повреждён или защищён паролем)") from e


def read_text_file(path, ctx: Ctx) -> list[tuple[str, str]]:
    with open(path, "rb") as f:
        raw = f.read(MAX_CHARS * 2)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        s = raw.decode("utf-16", errors="replace")
    elif b"\x00" in raw[:4096]:
        raise Unreadable("бинарный файл")
    elif raw.startswith(b"\xef\xbb\xbf"):
        s = raw[3:].decode("utf-8", errors="replace")
    else:
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            s = raw.decode("cp1251", errors="replace")
    if s.startswith("<!--mo-note "):                      # заметка программы (notes.py): служебную строку в поиске не показываем
        j = s.find("-->")
        if 0 < j < 4096:
            s = s[j + 3:]
    out, line_no = [], 1
    for blk in split_blocks(s):
        n = blk.count("\n") + 1
        out.append((f"строки {line_no}–{line_no + n - 1}" if n > 1 else f"строка {line_no}", blk))
        line_no += n
    return out


def read_html(path, ctx: Ctx) -> list[tuple[str, str]]:
    from bs4 import BeautifulSoup
    with open(path, "rb") as f:
        raw = f.read(MAX_CHARS * 2)
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    for t in soup.select(".mo-meta"):   # шапка, которую добавляет сама программа: курс, раздел, «Сохранено …» — не содержимое
        t.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text("\n")
    text = (title + "\n" + body) if title and title not in body[:len(title) + 5] else body
    return [("", b) for b in split_blocks(text)]


def read_docx(path, ctx: Ctx) -> list[tuple[str, str]]:
    zf = _zip(path)
    with zf:
        paras = []
        for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if name not in zf.namelist():
                if name == "word/document.xml":
                    raise Unreadable("в файле нет word/document.xml — это не .docx")
                continue
            cur = []
            with _zopen(zf, name) as f:
                for _ev, el in ET.iterparse(f, events=("end",)):
                    ln = _ln(el.tag)
                    if ln == "t":
                        cur.append(el.text or "")
                    elif ln == "tab":
                        cur.append("\t")
                    elif ln in ("br", "cr"):
                        cur.append(" ")
                    elif ln == "p":
                        s = "".join(cur).strip()
                        cur = []
                        if s:
                            paras.append(s)
                        el.clear()
            ctx.check()
    return [("", b) for b in split_blocks("\n".join(paras))]


def _rels(zf: zipfile.ZipFile, name: str) -> dict[str, tuple[str, str]]:
    """Id → (тип, цель) из файла связей .rels; нет файла — пусто."""
    if name not in zf.namelist():
        return {}
    root = ET.parse(_zopen(zf, name)).getroot()
    return {r.get("Id"): (r.get("Type", ""), r.get("Target", "")) for r in root if _ln(r.tag) == "Relationship"}


def _a_paragraphs(root) -> list[str]:
    out = []
    for p in _iter(root, "p"):
        parts = []
        for ch in p:
            ln = _ln(ch.tag)
            if ln == "r":
                t = ch.find("{*}t")
                if t is not None and t.text:
                    parts.append(t.text)
            elif ln == "br":
                parts.append(" ")
        s = "".join(parts).strip()
        if s:
            out.append(s)
    return out


def read_pptx(path, ctx: Ctx) -> list[tuple[str, str]]:
    with _zip(path) as zf:
        names = set(zf.namelist())
        order = []
        try:   # настоящий порядок слайдов — из presentation.xml
            rels = _rels(zf, "ppt/_rels/presentation.xml.rels")
            pres = ET.parse(_zopen(zf, "ppt/presentation.xml")).getroot()
            for sid in _iter(pres, "sldId"):
                tgt = rels.get(sid.get(f"{{{R_NS}}}id"), ("", ""))[1]
                if tgt:
                    order.append(posixpath.normpath(posixpath.join("ppt", tgt)))
        except (Unreadable, ET.ParseError, KeyError):
            order = []
        order = [n for n in order if n in names]
        if not order:
            order = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if not order:
            raise Unreadable("в файле нет слайдов — это не .pptx")
        out = []
        for i, sl in enumerate(order, 1):
            ctx.check()
            lines = _a_paragraphs(ET.parse(_zopen(zf, sl)).getroot())
            rel_name = posixpath.join(posixpath.dirname(sl), "_rels", posixpath.basename(sl) + ".rels")
            for typ, tgt in _rels(zf, rel_name).values():   # заметки докладчика
                if typ.endswith("/notesSlide"):
                    nn = posixpath.normpath(posixpath.join(posixpath.dirname(sl), tgt))
                    if nn in names:
                        notes = _a_paragraphs(ET.parse(_zopen(zf, nn)).getroot())
                        lines += [x for x in notes if not x.isdigit()]
            txt = "\n".join(lines)
            for blk in split_blocks(txt, BLOCK * 2, BLOCK_HARD * 2):
                out.append((f"слайд {i}", blk))
        return out


def read_xlsx(path, ctx: Ctx) -> list[tuple[str, str]]:
    with _zip(path) as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            with _zopen(zf, "xl/sharedStrings.xml") as f:
                for _ev, el in ET.iterparse(f, events=("end",)):
                    if _ln(el.tag) == "si":
                        s = []
                        for ch in el:
                            ln = _ln(ch.tag)
                            if ln == "t":
                                s.append(el_text(ch))
                            elif ln == "r":
                                t = ch.find("{*}t")
                                s.append(el_text(t) if t is not None else "")
                        shared.append("".join(s))
                        el.clear()
        try:
            rels = _rels(zf, "xl/_rels/workbook.xml.rels")
            wb = ET.parse(_zopen(zf, "xl/workbook.xml")).getroot()
            sheets = []
            for sh in _iter(wb, "sheet"):
                tgt = rels.get(sh.get(f"{{{R_NS}}}id"), ("", ""))[1]
                if tgt:
                    sheets.append((sh.get("name", ""), posixpath.normpath(posixpath.join("xl", tgt.lstrip("/").removeprefix("xl/")))))
        except (Unreadable, ET.ParseError):
            sheets = []
        sheets = [(n, p) for n, p in sheets if p in names]
        if not sheets:
            sheets = [(f"лист {i}", n) for i, n in enumerate(
                sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                       key=lambda n: int(re.findall(r"\d+", n)[-1])), 1)]
        if not sheets:
            raise Unreadable("в файле нет листов — это не .xlsx")
        out = []
        for name, part in sheets:
            ctx.check()
            rows = []
            with _zopen(zf, part) as f:
                for _ev, el in ET.iterparse(f, events=("end",)):
                    ln = _ln(el.tag)
                    if ln == "row":
                        cells = []
                        for c in el:
                            t = c.get("t")
                            v = c.find("{*}v")
                            if t == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                                cells.append(shared[int(v.text)])
                            elif t == "inlineStr":
                                cells.append("".join(el_text(x) for x in _iter(c, "t")))
                            elif t == "str" and v is not None:
                                cells.append(v.text or "")
                        cells = [x.strip() for x in cells if x and x.strip()]
                        if cells:
                            rows.append(" | ".join(cells))
                        el.clear()
            for blk in split_blocks("\n".join(rows)):
                out.append((f"лист «{name}»" if name else "лист", blk))
        return out


def el_text(el) -> str:
    return (el.text or "") if el is not None else ""


def _odf_para(el) -> str:
    parts = [el.text or ""]
    for ch in el:
        ln = _ln(ch.tag)
        if ln == "s":
            try:
                parts.append(" " * min(50, int(ch.get(f"{{{ODF_TEXT}}}c", "1"))))
            except ValueError:
                parts.append(" ")
        elif ln == "tab":
            parts.append("\t")
        elif ln == "line-break":
            parts.append("\n")
        elif ln in ODF_FIELDS:
            pass
        else:
            parts.append(_odf_para(ch))
        parts.append(ch.tail or "")
    return "".join(parts)


def read_odf(path, ctx: Ctx, ext: str) -> list[tuple[str, str]]:
    with _zip(path) as zf:
        root = ET.parse(_zopen(zf, "content.xml")).getroot()
    out = []
    if ext == ".odp":
        for i, pg in enumerate(root.iter(f"{{{ODF_DRAW}}}page"), 1):
            lines = [_odf_para(p).strip() for p in pg.iter() if _ln(p.tag) in ("p", "h")]
            for blk in split_blocks("\n".join(x for x in lines if x), BLOCK * 2, BLOCK_HARD * 2):
                out.append((f"слайд {i}", blk))
    elif ext == ".ods":
        for tb in root.iter(f"{{{ODF_TABLE}}}table"):
            name = tb.get(f"{{{ODF_TABLE}}}name", "")
            rows = []
            for row in tb.iter(f"{{{ODF_TABLE}}}table-row"):
                cells = []
                for c in row:
                    if _ln(c.tag) == "table-cell" and c.get(f"{{{ODF_OFFICE}}}value-type") == "string":
                        cells.append(" ".join(_odf_para(p).strip() for p in c if _ln(p.tag) == "p").strip())
                cells = [x for x in cells if x]
                if cells:
                    rows.append(" | ".join(cells))
            for blk in split_blocks("\n".join(rows)):
                out.append((f"лист «{name}»" if name else "лист", blk))
    else:
        lines = [_odf_para(p).strip() for p in root.iter() if _ln(p.tag) in ("p", "h")]
        out = [("", b) for b in split_blocks("\n".join(x for x in lines if x))]
    return out


def _pdf_notes(ctx: Ctx, total: int, got: int, bad: int) -> None:
    notes = []
    if total > ctx.max_pages:
        notes.append(f"прочитаны первые {ctx.max_pages} страниц из {total}")
    if not got and total:
        notes.append("текста нет — вероятно, скан (распознавания нет)" if not bad else "текст не извлекается (кодировка шрифтов)")
    elif bad:
        notes.append(f"{bad} стр. без читаемого текста")
    ctx.note = "; ".join(notes)


def _bad_text(t: str) -> bool:
    """Шрифт без нормальной кодировки: вместо букв квадраты и значки."""
    junk = sum(t.count(ch) for ch in "\ufffd\ufffe\u25a0\u25a1")
    return junk * 4 > len(t.replace(" ", ""))


def _read_pdf_pypdf(path, ctx: Ctx) -> list[tuple[str, str]]:
    out, bad, got, total = [], 0, 0, 0
    try:
        with open(path, "rb") as f:
            reader = pypdf.PdfReader(f, strict=False)
            if reader.is_encrypted:
                try:
                    ok = reader.decrypt("")
                except Exception:  # noqa: BLE001
                    ok = 0
                if not ok:
                    raise Unreadable("PDF защищён паролем")
            total = len(reader.pages)
            for i in range(min(total, ctx.max_pages)):
                ctx.check()
                try:
                    t = clean(reader.pages[i].extract_text() or "")
                except Exception:  # noqa: BLE001 — одна кривая страница не должна ронять весь файл
                    bad += 1
                    continue
                if _bad_text(t):
                    bad += 1
                    continue
                if t.strip():
                    got += 1
                    for blk in split_blocks(t, BLOCK * 2, BLOCK_HARD * 2):
                        out.append((f"стр. {i + 1}", blk))
    except (Unreadable, Stopped):
        raise
    except RecursionError as e:
        raise Unreadable("PDF слишком сложный для чтения") from e
    except Exception as e:  # noqa: BLE001
        if "password" in str(e).lower() or "encrypt" in str(e).lower():
            raise Unreadable("PDF защищён паролем") from e
        raise Unreadable(f"PDF повреждён или нестандартный ({str(e)[:60]})") from e
    _pdf_notes(ctx, total, got, bad)
    return out


def read_pdf(path, ctx: Ctx) -> list[tuple[str, str]]:
    if pdfium is None:
        if pypdf is None:
            raise Unreadable("нет библиотеки для PDF")
        return _read_pdf_pypdf(path, ctx)
    out, bad, got, total = [], 0, 0, 0
    with _PDFIUM_LOCK, open(path, "rb") as f:
        try:
            doc = pdfium.PdfDocument(f)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "password" in msg.lower():
                raise Unreadable("PDF защищён паролем") from e
            raise Unreadable(f"PDF повреждён или нестандартный ({msg[:60]})") from e
        try:
            total = len(doc)
            for i in range(min(total, ctx.max_pages)):
                ctx.check()
                page = tp = None
                try:
                    page = doc[i]
                    tp = page.get_textpage()
                    t = clean(tp.get_text_range())
                except Exception:  # noqa: BLE001 — одна кривая страница не должна ронять весь файл
                    bad += 1
                    continue
                finally:
                    for obj in (tp, page):
                        if obj is not None:
                            try:
                                obj.close()
                            except Exception:  # noqa: BLE001
                                pass
                junk = sum(t.count(ch) for ch in "\ufffd\ufffe\u25a0\u25a1")
                if junk * 4 > len(t.replace(" ", "")):          # шрифт без нормальной кодировки: вместо букв квадраты
                    bad += 1
                    continue
                if t.strip():
                    got += 1
                    for blk in split_blocks(t, BLOCK * 2, BLOCK_HARD * 2):
                        out.append((f"стр. {i + 1}", blk))
        finally:
            doc.close()
    _pdf_notes(ctx, total, got, bad)
    return out


def extract(path, ext: str, ctx: Ctx) -> list[tuple[str, str]]:
    """[(место, текст)] — по одному элементу на фрагмент. Неподдерживаемое/битое — Unreadable."""
    if ext == ".pdf":
        return read_pdf(path, ctx)
    if ext == ".docx":
        return read_docx(path, ctx)
    if ext == ".pptx":
        return read_pptx(path, ctx)
    if ext == ".xlsx":
        return read_xlsx(path, ctx)
    if ext in (".odt", ".ods", ".odp"):
        return read_odf(path, ctx, ext)
    if ext in (".html", ".htm"):
        return read_html(path, ctx)
    return read_text_file(path, ctx)


READABLE = frozenset(EXT_GROUP)


# ═══════════════════════════ индекс ═══════════════════════════
def _fsp(p) -> str:
    """Путь для файловых операций; в Windows включает поддержку длинных путей (>260 символов)."""
    s = str(p)
    if os.name == "nt":
        s = os.path.abspath(s)
        if len(s) >= 240 and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
    return s


def fingerprint(path, size: int) -> str:
    """Отпечаток файла: размер + хэш начала и конца. Нужен, чтобы находить одинаковые файлы и не читать заново."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > (1 << 20):
            f.seek(max(0, size - 65536))
            h.update(f.read(65536))
    return f"{size}:{h.hexdigest()[:20]}"


def plan_for(ext: str, size: int, cfg: dict) -> str:
    """Что делать с файлом: read — читать содержимое; name — только название; legacy / big / nolib — тоже только название."""
    if ext in LEGACY:
        return "legacy"
    g = EXT_GROUP.get(ext)
    if g is None or g not in cfg["types"]:
        return "name"
    if size > cfg["max_mb"] * 2**20:
        return "big"
    if ext == ".pdf" and not have_pdf():
        return "nolib"
    return "read"


def _stale(old_status: str, plan: str) -> bool:
    if plan == "read":
        return old_status not in ("ok", "empty", "err")
    return old_status != plan


def scan(out_dir, cfg: dict) -> list[tuple[str, int, int]]:
    """Все файлы материалов: [(путь относительно папки через «/», размер, время изменения)]."""
    skip_courses = {c.casefold() for c in cfg["exclude_courses"]}
    base = _fsp(Path(out_dir)).rstrip("\\/")
    res = []
    for dp, dns, fns in os.walk(base):
        rel_dir = dp[len(base):].strip("\\/").replace("\\", "/")
        if not rel_dir:                                       # корень: файлы здесь служебные, папки — курсы
            dns[:] = [d for d in dns if not d.startswith((".", "_")) and d.casefold() not in SKIP_ROOT_DIRS
                      and d.casefold() not in skip_courses]
            continue
        dns[:] = [d for d in dns if not d.startswith(".")]
        if any(f.lower() == "imsmanifest.xml" for f in fns):  # пакет SCORM/IMS: внутри только «движок», не материалы
            dns[:] = []
            continue
        top_level = "/" not in rel_dir
        for fn in fns:
            low = fn.lower()
            if fn.startswith(".") or low.endswith((".tmp", ".part", ".crdownload")) or low in ("thumbs.db", "desktop.ini"):
                continue
            if top_level and low in GENERATED:
                continue
            try:
                st = os.stat(os.path.join(dp, fn))
            except OSError:
                continue
            res.append((f"{rel_dir}/{fn}", st.st_size, int(st.st_mtime)))
    res.sort()
    return res


class Index:
    """Индекс одной папки с материалами. Читать можно из любого потока; обновлять — из одного."""

    def __init__(self, out_dir):
        self.out = Path(out_dir)
        self.path = self.out / INDEX_FILE
        self.fts = fts_available()

    def exists(self) -> bool:
        return self.path.is_file()

    # ── служебное ──
    def _create(self, db):
        """Создаёт пустой индекс одной транзакцией: читающие видят либо старый, либо новый целиком."""
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            for t in ("fts", "chunks", "docs", "meta"):
                try:
                    db.execute(f"DROP TABLE IF EXISTS {t}")
                except sqlite3.Error:
                    pass
            db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
            db.execute("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, course TEXT, ext TEXT, grp TEXT, "
                       "size INTEGER, mtime INTEGER, fp TEXT, status TEXT, note TEXT, chunks INTEGER DEFAULT 0, ts REAL)")
            db.execute("CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc INTEGER NOT NULL, loc TEXT, text BLOB NOT NULL)")
            db.execute("CREATE INDEX chunks_doc ON chunks(doc)")
            db.execute("CREATE INDEX docs_course ON docs(course)")
            if self.fts:
                # content='' — в FTS нет второй копии текста: он лежит один раз, сжатым, в chunks
                db.execute("CREATE VIRTUAL TABLE fts USING fts5(norm, content='', tokenize='unicode61 remove_diacritics 2')")
            db.executemany("INSERT INTO meta VALUES(?,?)", [("ver", str(SCHEMA)), ("fts", "1" if self.fts else "0")])
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def _meta(self, db) -> dict:
        try:
            return dict(db.execute("SELECT k, v FROM meta").fetchall())
        except sqlite3.OperationalError:
            return {}

    def _open(self, create: bool = False):
        """Соединение с индексом. Нет файла и create=False → None. Битый файл откладывается в .bad."""
        if not create and not self.path.is_file():
            return None
        if create:
            self.out.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            db = sqlite3.connect(str(self.path), timeout=30)
            try:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
                db.execute("PRAGMA synchronous=NORMAL")
                meta = self._meta(db)
                if meta.get("ver") != str(SCHEMA):
                    if not create:
                        db.close()
                        return None
                    self._create(db)
                elif create and meta.get("fts") != ("1" if self.fts else "0"):
                    self._create(db)              # поменялась поддержка FTS5 — индекс надо собрать заново
                return db
            except sqlite3.DatabaseError as e:
                db.close()
                broken = any(w in str(e).lower() for w in ("not a database", "malformed", "corrupt"))
                if not broken or attempt == 2:      # «база занята» и прочее — не повод выбрасывать индекс
                    raise
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
                if not create:
                    return None
        return None

    def clear(self):
        for suf in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(str(self.path) + suf)
            except FileNotFoundError:
                pass

    def _drop_chunks(self, db, where: str, args=()):
        if self.fts:   # в FTS без содержимого строку можно убрать, только назвав тот же текст, что был добавлен
            for cid, blob in db.execute(f"SELECT id, text FROM chunks WHERE {where}", args).fetchall():
                db.execute("INSERT INTO fts(fts, rowid, norm) VALUES('delete', ?, ?)", (cid, norm(_unpack(blob))))
        db.execute(f"DELETE FROM chunks WHERE {where}", args)

    def _drop_doc(self, db, doc_id: int):
        self._drop_chunks(db, "doc = ?", (doc_id,))
        db.execute("DELETE FROM docs WHERE id=?", (doc_id,))

    # ── обновление индекса ──
    def update(self, cfg=None, *, full: bool = False, progress=None, stop: threading.Event | None = None) -> dict:
        """Просматривает папку и обновляет индекс: новое — добавляет, изменённое — перечитывает, пропавшее — убирает."""
        cfg = normalize(cfg)
        t0 = time.monotonic()
        P = {"phase": "scan", "total": 0, "done": 0, "current": "", "files": 0}

        def tick(**kw):
            P.update(kw)
            if progress:
                progress(dict(P))

        tick()
        res = {"files": 0, "new": 0, "changed": 0, "removed": 0, "unchanged": 0, "ok": 0, "empty": 0, "err": 0,
               "legacy": 0, "big": 0, "nolib": 0, "name": 0, "stopped": False, "secs": 0.0}
        db = self._open(create=True)
        try:
            if full:   # перечитать всё; пока файл не перечитан, его старый текст ещё ищется
                db.execute("UPDATE docs SET mtime = -1, fp = NULL")
                self._drop_chunks(db, "doc NOT IN (SELECT id FROM docs)")   # осиротевшие фрагменты, если вдруг есть
                db.commit()
            files = scan(self.out, cfg)
            if stop is not None and stop.is_set():
                raise Stopped()
            res["files"] = len(files)
            existing = {r[1]: r for r in db.execute("SELECT id, path, size, mtime, fp, status FROM docs")}
            seen = {f[0] for f in files}
            for path, row in list(existing.items()):                       # файлов больше нет (или курс исключён)
                if path not in seen:
                    self._drop_doc(db, row[0])
                    del existing[path]
                    res["removed"] += 1
            todo = []
            for rel, size, mtime in files:
                ext = os.path.splitext(rel)[1].lower()
                plan = plan_for(ext, size, cfg)
                old = existing.get(rel)
                if old is None or old[2] != size or old[3] != mtime or _stale(old[5], plan):
                    todo.append((rel, size, mtime, ext, plan, old))
                else:
                    res["unchanged"] += 1
            tick(phase="index", total=len(todo), done=0)
            base = _fsp(self.out).rstrip("\\/")
            last_commit, since = time.monotonic(), 0
            try:
                for i, (rel, size, mtime, ext, plan, old) in enumerate(todo, 1):
                    if stop is not None and stop.is_set():
                        raise Stopped()
                    tick(done=i - 1, current=rel)
                    apath = os.path.join(base, *rel.split("/"))
                    try:
                        fp = fingerprint(apath, size)
                    except OSError:
                        continue                                               # файл пропал между просмотром и чтением
                    if old and old[4] == fp and old[2] == size and not _stale(old[5], plan):
                        db.execute("UPDATE docs SET mtime=? WHERE id=?", (mtime, old[0]))   # тот же файл, перезаписан
                        res["unchanged"] += 1
                        continue
                    status, note, units = plan, "", []
                    if plan == "read":
                        ctx = Ctx(stop, cfg["max_pages"])
                        try:
                            units = extract(apath, ext, ctx)
                            note = ctx.note
                            status = "ok" if units else "empty"
                            if status == "empty" and not note:
                                note = "текста в файле нет"
                        except Stopped:
                            raise
                        except Unreadable as e:
                            status, note = "err", str(e)
                        except Exception as e:  # noqa: BLE001 — один плохой файл не должен ронять индексацию
                            status, note = "err", f"{type(e).__name__}: {str(e)[:150]}"
                    elif plan == "legacy":
                        note = f"старый формат — пересохраните как {LEGACY[ext]}, тогда найдётся и содержимое"
                    elif plan == "big":
                        note = f"файл больше {cfg['max_mb']} МБ — ищу только по названию"
                    elif plan == "nolib":
                        note = "для PDF нужна библиотека pypdfium2 или pypdf — пока ищу только по названию"
                    total_chars, kept = 0, []
                    for loc, text in units:
                        total_chars += len(text)
                        if total_chars > MAX_CHARS:
                            note = (note + "; " if note else "") + "текст обрезан (слишком большой файл)"
                            break
                        kept.append((loc, text))
                    parts = rel.split("/")
                    if old:
                        self._drop_doc(db, old[0])
                    cur = db.execute("INSERT INTO docs(path, course, ext, grp, size, mtime, fp, status, note, ts) "
                                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                                     (rel, parts[0], ext, EXT_GROUP.get(ext, OTHER), size, mtime, fp, status, note, time.time()))
                    doc_id = cur.lastrowid
                    rows = [(NAME_LOC, clean(" / ".join(parts[1:])))] + kept     # без папки курса: иначе по её названию «находились» бы все файлы
                    for loc, text in rows:
                        c = db.execute("INSERT INTO chunks(doc, loc, text) VALUES(?,?,?)", (doc_id, loc, _pack(text)))
                        if self.fts:
                            db.execute("INSERT INTO fts(rowid, norm) VALUES(?,?)", (c.lastrowid, norm(text)))
                    db.execute("UPDATE docs SET chunks=? WHERE id=?", (len(rows), doc_id))
                    res["changed" if old else "new"] += 1
                    res[status] += 1
                    since += 1
                    if since >= 25 or time.monotonic() - last_commit > 1.5:
                        db.commit()
                        last_commit, since = time.monotonic(), 0
            except Stopped:
                res["stopped"] = True
            tick(done=len(todo) if not res["stopped"] else P["done"])
            db.execute("INSERT OR REPLACE INTO meta VALUES('updated', ?)", (str(time.time()),))
            db.commit()
            if self.fts and (res["new"] + res["changed"]) > 200 and not res["stopped"]:
                db.execute("INSERT INTO fts(fts) VALUES('optimize')")
                db.commit()
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        except Stopped:
            res["stopped"] = True
        finally:
            try:
                db.commit()
            except sqlite3.Error:
                pass
            db.close()
        res["secs"] = round(time.monotonic() - t0, 1)
        tick(phase="stopped" if res["stopped"] else "done", current="")
        return res

    # ── сведения об индексе ──
    def status(self, problems: int = 60) -> dict:
        info = {"exists": False, "pdf": have_pdf(), "fts": self.fts}
        db = self._open()
        if db is None:
            return info
        try:
            by_status = dict(db.execute("SELECT status, COUNT(*) FROM docs GROUP BY status").fetchall())
            by_group = dict(db.execute("SELECT grp, COUNT(*) FROM docs WHERE status IN ('ok','empty') GROUP BY grp").fetchall())
            meta = self._meta(db)
            info.update({
                "exists": True,
                "docs": sum(by_status.values()),
                "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "bytes": self.path.stat().st_size,
                "updated": float(meta.get("updated") or 0),
                "by_status": by_status, "by_group": by_group,
                "courses": [{"name": n, "count": c} for n, c in
                            db.execute("SELECT course, COUNT(*) FROM docs GROUP BY course ORDER BY course")],
                "problems": [{"path": p, "status": s, "note": n or ""} for p, s, n in db.execute(
                    "SELECT path, status, note FROM docs WHERE status IN ('err','empty','legacy','big','nolib') "
                    "ORDER BY CASE status WHEN 'err' THEN 0 WHEN 'nolib' THEN 1 WHEN 'legacy' THEN 2 WHEN 'empty' THEN 3 ELSE 4 END, path LIMIT ?",
                    (problems,))],
            })
        except sqlite3.Error as e:
            info["error"] = str(e)
        finally:
            db.close()
        return info

    # ── поиск ──
    def search(self, q: str, *, course: str | None = None, groups=None, limit: int = 30, order: str = "rank",
               per_doc: int = 3) -> dict:
        t0 = time.perf_counter()
        pq = parse_query(q)
        if pq.empty:
            return {"ok": False, "error": "Введите слово или фразу. Одни исключения («-слово») искать не из чего."}
        try:
            db = self._open()
        except sqlite3.Error as e:
            return {"ok": False, "error": f"Индекс не открывается: {e}"}
        if db is None:
            return {"ok": False, "no_index": True, "error": "Индекс ещё не построен."}
        try:
            use_fts = self.fts and self._meta(db).get("fts") == "1"
            groups = [g for g in (groups or []) if g in GROUPS or g == OTHER]
            relaxed = False
            rows = self._rows(db, pq, "and", course, groups, use_fts)
            if not rows and pq.count > 1:
                rows = self._rows(db, pq, "or", course, groups, use_fts)
                relaxed = bool(rows)
            rx = highlight_re(pq)
            by_doc: dict[int, list] = {}
            for doc_id, cid, loc, text, score in rows:
                by_doc.setdefault(doc_id, []).append((score, cid, loc, text))
            docs = {}
            ids = list(by_doc)
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                for r in db.execute("SELECT id, path, course, ext, grp, size, mtime, fp, status, note FROM docs WHERE id IN (%s)"
                                    % ",".join("?" * len(part)), part):
                    docs[r[0]] = r
            by_doc = {d: h for d, h in by_doc.items() if d in docs}
            ordered = sorted(by_doc, key=(lambda d: (-(docs[d][6] or 0), min(h[0] for h in by_doc[d]))) if order == "new"
                             else (lambda d: min(h[0] for h in by_doc[d])))
            results, by_fp = [], {}
            for d in ordered:
                r = docs.get(d)
                if r is None:
                    continue
                _id, path, crs, ext, grp, size, mtime, fp, status, note = r
                if fp and fp in by_fp:                       # тот же файл в другом разделе — не дублируем, а помечаем
                    by_fp[fp]["dups"].append(path)
                    continue
                hits = sorted(by_doc[d], key=lambda h: h[0])
                content = [h for h in hits if h[2] != NAME_LOC]
                parts = path.split("/")
                e = {"path": path, "course": crs, "ext": ext, "group": grp, "size": size, "mtime": mtime,
                     "name": highlight(parts[-1], rx), "where": highlight(" › ".join(parts[1:-1]), rx),
                     "hits": [{"loc": h[2], "snip": make_snippet(_unpack(h[3]), rx)} for h in content[:per_doc]],
                     "more": max(0, len(content) - per_doc), "by_name": not content, "dups": []}
                if fp:
                    by_fp[fp] = e
                results.append(e)
            total = len(results)
            return {"ok": True, "q": q, "total": total, "results": results[:max(1, limit)], "relaxed": relaxed,
                    "truncated": len(rows) >= RAW_LIMIT, "ms": int((time.perf_counter() - t0) * 1000), "fts": use_fts}
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return {"ok": False, "no_index": True, "error": "Индекс сейчас создаётся заново — повторите через несколько секунд."}
            return {"ok": False, "error": f"Не удалось выполнить запрос: {e}"}
        finally:
            db.close()

    def _rows(self, db, pq: Query, mode: str, course, groups, use_fts: bool) -> list[tuple]:
        flt, args = "", []
        if course:
            flt += " AND d.course = ?"
            args.append(course)
        if groups:
            flt += " AND d.grp IN (%s)" % ",".join("?" * len(groups))
            args += groups
        if use_fts:
            expr = match_expr(pq, mode)
            return db.execute(
                "SELECT c.doc, c.id, c.loc, c.text, bm25(fts) AS r FROM fts JOIN chunks c ON c.id = fts.rowid "
                "JOIN docs d ON d.id = c.doc WHERE fts MATCH ?" + flt + " ORDER BY r LIMIT ?",
                [expr] + args + [RAW_LIMIT]).fetchall()
        # запасной путь без FTS5: перебор фрагментов (медленнее, но работает везде)
        pos = [re.compile(r"(?<![^\W_])" + _term_pat(t) + (r"(?![^\W_])" if not t[1] else ""), re.I) for t in pq.pos]
        pos += [re.compile(r"(?<![^\W_])" + _phrase_pat(p) + r"(?![^\W_])", re.I) for p in pq.phr]
        neg = [re.compile(r"(?<![^\W_])" + _term_pat(t) + (r"(?![^\W_])" if not t[1] else ""), re.I) for t in pq.nterms]
        neg += [re.compile(r"(?<![^\W_])" + _phrase_pat(p) + r"(?![^\W_])", re.I) for p in pq.nphr]
        out = []
        cur = db.execute("SELECT c.doc, c.id, c.loc, c.text FROM chunks c JOIN docs d ON d.id = c.doc WHERE 1=1" + flt, args)
        while True:
            batch = cur.fetchmany(2000)
            if not batch:
                break
            for doc, cid, loc, blob in batch:
                text = _unpack(blob)
                counts = [len(p.findall(text)) for p in pos]
                ok = all(counts) if mode == "and" else any(counts)
                if ok and not any(n.search(text) for n in neg):
                    out.append((doc, cid, loc, blob, -sum(counts) / (1.0 + len(text) / 800.0)))
        out.sort(key=lambda r: r[4])
        return out[:RAW_LIMIT]


RAW_LIMIT = 1500


# ═══════════════════════════ для консоли ═══════════════════════════
def plain(s: str) -> str:
    """Подсветку в обычном тексте показываем скобками: [найденное]."""
    return s.replace(HL0, "[").replace(HL1, "]")


def format_results(res: dict, limit_hits: int = 3) -> str:
    if not res.get("ok"):
        return res.get("error", "Ошибка поиска")
    lines = []
    if not res["results"]:
        return "Ничего не найдено."
    if res.get("relaxed"):
        lines.append("Всех слов сразу ни в одном фрагменте нет — показаны фрагменты, где есть хотя бы одно.")
    for i, r in enumerate(res["results"], 1):
        lines.append(f"{i}. {plain(r['name'])}  —  {r['course']}")
        if r["where"]:
            lines.append(f"   {plain(r['where'])}")
        for h in r["hits"][:limit_hits]:
            lines.append(f"   {h['loc'] + ': ' if h['loc'] else ''}{plain(h['snip'])}")
        if r["by_name"]:
            lines.append("   (совпало название)")
        if r["more"]:
            lines.append(f"   …и ещё {r['more']} фрагм.")
        if r["dups"]:
            lines.append(f"   такой же файл ещё в: {', '.join(r['dups'][:3])}")
    shown = len(res["results"])
    lines.append(f"Найдено файлов: {res['total']}" + (f", показано {shown}" if shown < res["total"] else "")
                 + f" ({res['ms']} мс)")
    return "\n".join(lines)


def format_status(st: dict) -> str:
    if not st.get("exists"):
        return "Индекс ещё не построен: запустите с --search-index."
    bs = st.get("by_status", {})
    parts = [f"файлов в индексе: {st['docs']}", f"фрагментов: {st['chunks']}", f"размер индекса: {st['bytes'] / 1048576:.1f} МБ"]
    for k, t in (("empty", "без текста (сканы)"), ("err", "не прочитано"), ("legacy", "старые .doc/.ppt/.xls"),
                 ("big", "слишком большие"), ("nolib", "PDF без библиотеки чтения")):
        if bs.get(k):
            parts.append(f"{t}: {bs[k]}")
    return "; ".join(parts)

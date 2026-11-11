# -*- coding: utf-8 -*-
"""
Расписание из Excel-файлов СевГУ (https://www.sevsu.ru/univers/shedule/) — часть Moodle Offline.

Университет выкладывает на странице «Расписание» большие xlsx-таблицы на весь семестр: на каждую учебную неделю свой лист
(«уч.н. 1», «уч.н. 2», …), на листе рядом стоят блоки групп (по 10 столбцов: день, дата, № занятия, время,
подгруппа 1 — занятие/тип/аудитория, подгруппа 2 — занятие/тип/аудитория). Здесь файл читается и превращается
в список пар в формате программы (как у schedule.py: date, start, end, pair, subject, type, teacher, room, address,
subgroup, stream, note).

Без сторонних библиотек и без кода сайтов: xlsx — это zip с XML, читаем его сами (zipfile + ElementTree).
Незнакомая таблица (например, расписание сессии в другом виде) не ломает программу: возвращается понятная ошибка.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

MAX_UNPACKED = 80 * 1024 * 1024        # защита от «бомбы» в архиве: больше столько не распаковываем
PAIR_MINUTES = 90                       # длительность пары (звонки СевГУ: 8:30–10:00, 10:10–11:40, …)


class XlsxError(Exception):
    """Понятная причина, почему файл не удалось прочитать (показывается человеку как есть)."""


# ═══════════════════════ чтение xlsx ═══════════════════════
def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_letters(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_REF = re.compile(r"^([A-Za-z]+)(\d+)$")


def _ref(ref: str):
    m = _REF.match(ref or "")
    return (int(m.group(2)), col_index(m.group(1))) if m else None


class Sheet:
    """Лист: cells[(строка, столбец)] = текст (с 1); merges[(строка, столбец верхней левой)] = (строка2, столбец2)."""

    def __init__(self, name: str):
        self.name = name
        self.cells: dict[tuple[int, int], str] = {}
        self.merges: dict[tuple[int, int], tuple[int, int]] = {}
        self.max_row = 0
        self.max_col = 0

    def get(self, r: int, c: int) -> str:
        return self.cells.get((r, c), "")

    def span_cols(self, r: int, c: int) -> int:
        """Сколько столбцов занимает ячейка (1, если не объединена)."""
        m = self.merges.get((r, c))
        return (m[1] - c + 1) if m else 1


def _text_of(si) -> str:
    return "".join(t.text or "" for t in si.iter(NS + "t"))


def _read_member(z: zipfile.ZipFile, name: str, budget: list) -> bytes:
    info = z.getinfo(name)
    budget[0] += info.file_size
    if budget[0] > MAX_UNPACKED:
        raise XlsxError("файл слишком большой после распаковки — это не похоже на расписание")
    return z.read(name)


def read_workbook(data: bytes) -> list[Sheet]:
    """Все листы книги (скрытые тоже — в них бывают недели). Только текст ячеек и объединения."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as e:
        raise XlsxError("это не файл Excel (.xlsx). Старый формат .xls программа не читает — скачайте .xlsx") from e
    budget = [0]
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        raise XlsxError("в файле нет книги Excel (xl/workbook.xml)")
    try:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(_read_member(z, "xl/sharedStrings.xml", budget))
            shared = [_text_of(si) for si in root.iter(NS + "si")]
        wb = ET.fromstring(_read_member(z, "xl/workbook.xml", budget))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in ET.fromstring(_read_member(z, "xl/_rels/workbook.xml.rels", budget)).iter(PKG_NS + "Relationship"):
                rels[rel.get("Id")] = rel.get("Target") or ""
        sheets: list[Sheet] = []
        for sh in wb.iter(NS + "sheet"):
            target = rels.get(sh.get(RNS + "id"), "")
            if not target:
                continue
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if path not in names:
                continue
            sheets.append(_read_sheet(sh.get("name") or "", _read_member(z, path, budget), shared))
        return sheets
    except ET.ParseError as e:
        raise XlsxError(f"файл Excel повреждён ({e})") from e


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f == int(f) and abs(f) < 1e15 else repr(f)


def _read_sheet(name: str, xml: bytes, shared: list[str]) -> Sheet:
    sh = Sheet(name)
    root = ET.fromstring(xml)
    for c in root.iter(NS + "c"):
        rc = _ref(c.get("r"))
        if not rc:
            continue
        t = c.get("t")
        if t == "inlineStr":
            val = _text_of(c.find(NS + "is")) if c.find(NS + "is") is not None else ""
        else:
            v = c.find(NS + "v")
            raw = v.text if v is not None and v.text is not None else ""
            if t == "s":
                try:
                    val = shared[int(raw)]
                except (ValueError, IndexError):
                    val = ""
            elif t in ("str", "e"):
                val = raw
            elif t == "b":
                val = "1" if raw == "1" else "0"
            else:
                val = _num(raw) if raw else ""
        if val != "":
            sh.cells[rc] = val
            sh.max_row = max(sh.max_row, rc[0])
            sh.max_col = max(sh.max_col, rc[1])
    for mc in root.iter(NS + "mergeCell"):
        ref = (mc.get("ref") or "").split(":")
        a = _ref(ref[0])
        b = _ref(ref[-1])
        if a and b and (a != b):
            sh.merges[a] = b
    return sh


# ═══════════════════════ разбор расписания ═══════════════════════
_GROUP_HDR = re.compile(r"^\s*группа\s*:?\s*(.+?)\s*$", re.I)
_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                      "u", "f", "h", "c", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def group_key(name: str) -> str:
    """«ИБ/б-25-2-о», «иб/б-25-2о» и «ib-b-25-2-o» дают один ключ — для сравнения названий групп."""
    t = re.sub(r"^\s*группа\s*:?\s*", "", (name or "").lower())
    t = "".join(_TRANSLIT.get(ch, ch) for ch in t)
    return re.sub(r"[^a-z0-9]+", "", t)


TYPES = {"л": "Лекция", "лк": "Лекция", "лек": "Лекция", "лекция": "Лекция",
         "лз": "Лабораторная (ЛЗ)", "лр": "Лабораторная (ЛЗ)", "лаб": "Лабораторная (ЛЗ)",
         "пз": "Практика (ПЗ)", "пр": "Практика (ПЗ)", "практика": "Практика (ПЗ)",
         "с": "Семинар", "сем": "Семинар", "семинар": "Семинар",
         "зач": "Зачёт", "зачет": "Зачёт", "зачёт": "Зачёт", "з": "Зачёт",
         "дз": "Дифференцированный зачёт", "зо": "Дифференцированный зачёт", "зачо": "Дифференцированный зачёт",
         "экз": "Экзамен", "экзамен": "Экзамен", "э": "Экзамен",
         "к": "Консультация", "конс": "Консультация", "консультация": "Консультация"}


def norm_type(t: str) -> str:
    t = (t or "").strip()
    return TYPES.get(t.lower().strip(" ."), t)


_FIO = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?(?:\s+[А-ЯЁ][а-яё]+(?:-[а-яё]+)?)?|[А-ЯЁ]\.\s?[А-ЯЁ]\.?)$")
_TAIL = re.compile(r"\s*\(([^()]*)\)\s*$")
_SUB = re.compile(r"^\s*подгр(?:уппа|\.)?\s*:?\s*(\d+)\s*(?:\(\s*из\s*\d+\s*\))?\s*[,;:]?\s*", re.I)
_STREAM = re.compile(r"^\s*уч\.?\s*гр\.?\s*:?\s*(.+?)\s*$", re.I)


def _room_like(p: str) -> bool:
    p = p.strip()
    return bool(p) and len(p) <= 16 and (bool(re.search(r"\d", p)) or p.lower() in ("д", "дист", "дистанционно", "онлайн"))


def parse_cell_line(line: str) -> dict:
    """«подгр.:2(из 2),Предмет, Фамилия Имя Отчество (вн. совм.) (I-А-503)(Уч.гр. ОТРИБ/б-25-1-о)» → поля пары."""
    s = (line or "").strip()
    sub = ""
    m = _SUB.match(s)
    if m:
        sub, s = m.group(1), s[m.end():]
    stream, room = "", ""
    while True:                                  # хвостовые скобки: поток, аудитория, пометки («вн. совм.»)
        m = _TAIL.search(s)
        if not m:
            break
        inner = m.group(1).strip()
        sm = _STREAM.match(inner)
        if sm:
            stream = stream or sm.group(1).strip()
        elif _room_like(inner) and not room:
            room = inner
        s = s[:m.start()]
    s = re.sub(r"\s+", " ", s).strip(" ,;")
    subject, teacher = s, ""
    m = re.search(r",\s*([^,]+)$", s)
    if m and _FIO.match(m.group(1).strip()):
        subject, teacher = s[:m.start()].strip(" ,;"), m.group(1).strip()
    return {"subject": subject, "teacher": teacher, "room": room, "stream": stream, "subgroup": sub}


def _lines(v: str) -> list[str]:
    return [x.strip() for x in (v or "").replace("\r", "").split("\n") if x.strip()]


def _parse_date(v: str) -> date | None:
    v = (v or "").strip()
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if re.fullmatch(r"\d{5}(\.\d+)?", v) and 30000 < float(v) < 80000:          # дата как число Excel
        return date(1899, 12, 30) + timedelta(days=int(float(v)))
    return None


def _parse_time(v: str) -> str:
    m = re.search(r"(\d{1,2})[:.](\d{2})", v or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _plus(hhmm: str, minutes: int) -> str:
    try:
        t = datetime.strptime(hhmm, "%H:%M") + timedelta(minutes=minutes)
    except ValueError:
        return ""
    return t.strftime("%H:%M")


class _Block:
    """Где в блоке группы какой столбец. Ищем по подписям шапки, при неудаче берём привычные смещения (+0 … +9)."""

    def __init__(self, sh: Sheet, hdr_row: int, c0: int, c1: int):
        self.c0, self.c1 = c0, c1
        self.date = c0 + 1
        self.num = c0 + 2
        self.time = c0 + 3
        self.s1 = c0 + 4
        self.s2 = c0 + 7
        found: dict[str, int] = {}
        for r in range(hdr_row + 1, hdr_row + 4):
            for c in range(c0, c1 + 1):
                t = sh.get(r, c).lower().replace(" ", "")
                if not t:
                    continue
                if t == "дата":
                    found["date"] = c
                elif t.startswith("№") or t.startswith("номер"):
                    found["num"] = c
                elif t == "время":
                    found["time"] = c
                elif t.startswith("подгруппа1"):
                    found["s1"] = c
                elif t.startswith("подгруппа2"):
                    found["s2"] = c
        self.date = found.get("date", self.date)
        self.num = found.get("num", self.num)
        self.time = found.get("time", self.time)
        self.s1 = found.get("s1", self.s1)
        self.s2 = found.get("s2", self.s2)
        self.first_row = hdr_row + 1
        for r in range(hdr_row + 1, hdr_row + 5):          # первая строка данных — где в «№ занятия» цифра
            if re.fullmatch(r"\d{1,2}", sh.get(r, self.num).strip()):
                self.first_row = r
                break


def legend(sheets: list[Sheet]) -> dict[str, str]:
    """Лист «Справка»: код корпуса (I, II, УТЦ, Д …) → название и адрес. Нужен, чтобы показать, где аудитория."""
    out: dict[str, str] = {}
    for sh in sheets:
        if "справк" not in sh.name.lower():
            continue
        for r in range(1, sh.max_row + 1):
            vals = [sh.get(r, c).strip() for c in range(1, min(sh.max_col, 8) + 1)]
            vals = [v for v in vals if v]
            if len(vals) >= 2 and len(vals[0]) <= 6:
                out[vals[0]] = vals[1]
    return out


def _address(room: str, leg: dict[str, str]) -> str:
    code = re.split(r"[-–]", room, maxsplit=1)[0].strip() if "-" in room or "–" in room else ""
    return leg.get(code, "") if code else ""


def group_blocks(sh: Sheet) -> list[tuple[str, int, int, int]]:
    """[(название группы, строка шапки, первый столбец, последний столбец)] на листе."""
    out = []
    for (r, c), v in sorted(sh.cells.items()):
        if r > 12:
            continue
        m = _GROUP_HDR.match(v)
        if not m or "\n" in v:
            continue
        end = sh.merges.get((r, c))
        out.append((m.group(1).strip(), r, c, end[1] if end else c + 9))
    return out


def list_groups(sheets: list[Sheet]) -> list[str]:
    names = []
    for sh in sheets:
        for name, *_ in group_blocks(sh):
            if name not in names:
                names.append(name)
    return names


def parse_workbook(data: bytes, group: str) -> tuple[list[dict], dict]:
    """→ (пары группы за весь файл, сведения). Если группы в файле нет — XlsxError со списком групп из файла."""
    sheets = read_workbook(data)
    names = list_groups(sheets)
    if not names:
        raise XlsxError("в таблице не нашёл блоков «Группа : …». Это, видимо, расписание в другом виде "
                        "(сессия или аттестация) — такие файлы программа пока не читает")
    want = group_key(group)
    pick = next((n for n in names if group_key(n) == want), None)
    if not pick:
        show = ", ".join(names[:12]) + (f" и ещё {len(names) - 12}" if len(names) > 12 else "")
        raise XlsxError(f"в файле нет группы «{group}». В нём группы: {show}")
    leg = legend(sheets)
    lessons: list[dict] = []
    seen = set()
    weeks = 0
    for sh in sheets:
        for name, hdr, c0, c1 in group_blocks(sh):
            if group_key(name) != want:
                continue
            weeks += 1
            _parse_block(sh, _Block(sh, hdr, c0, c1), leg, lessons, seen)
    lessons.sort(key=lambda x: (x["date"], x["start"], x["subgroup"], x["subject"]))
    info = {"group": pick, "weeks": weeks, "count": len(lessons), "groups": len(names),
            "first": lessons[0]["date"] if lessons else "", "last": lessons[-1]["date"] if lessons else ""}
    return lessons, info


def _parse_block(sh: Sheet, b: _Block, leg: dict, out: list, seen: set):
    cur: date | None = None
    for r in range(b.first_row, sh.max_row + 1):
        d = _parse_date(sh.get(r, b.date))
        if d:
            cur = d
        num = sh.get(r, b.num).strip()
        if not cur or not re.fullmatch(r"\d{1,2}", num):
            continue
        start = _parse_time(sh.get(r, b.time))
        # объединённая на обе подгруппы ячейка — занятие у всей группы; тип и аудитория тогда в столбцах подгруппы 2
        whole = sh.span_cols(r, b.s1) > (b.s2 - b.s1)
        cells = []     # (подгруппа по столбцу, занятие, тип, аудитория)
        if whole:
            cells.append(("", sh.get(r, b.s1), sh.get(r, b.s2 + 1) or sh.get(r, b.s1 + 1), sh.get(r, b.s2 + 2) or sh.get(r, b.s1 + 2)))
        else:
            cells.append(("1", sh.get(r, b.s1), sh.get(r, b.s1 + 1), sh.get(r, b.s1 + 2)))
            cells.append(("2", sh.get(r, b.s2), sh.get(r, b.s2 + 1), sh.get(r, b.s2 + 2)))
        for col_sub, text, typ, room in cells:
            ls = _lines(text)
            if not ls:
                continue
            ts, rs = _lines(typ), _lines(room)
            for i, line in enumerate(ls):
                p = parse_cell_line(line)
                if not p["subject"]:
                    continue
                t = ts[i] if i < len(ts) else (ts[0] if len(ts) == 1 else "")
                rm = rs[i] if i < len(rs) else (rs[0] if len(rs) == 1 else "") or p["room"]
                rm = rm or p["room"]
                if rm.lower() in ("д", "дист", "дистанционно"):
                    rm = "Дистанционно"
                sub = p["subgroup"] or col_sub
                le = {"date": cur.isoformat(), "start": start, "end": _plus(start, PAIR_MINUTES), "pair": int(num),
                      "subject": p["subject"], "type": norm_type(t), "teacher": p["teacher"], "room": rm,
                      "address": "" if rm == "Дистанционно" else _address(rm, leg),
                      "subgroup": sub, "stream": p["stream"], "note": ""}
                key = (le["date"], le["start"], le["subject"], le["subgroup"], le["stream"], le["room"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(le)

# -*- coding: utf-8 -*-
"""Правила скачивания: что брать, что пропускать и как называть папки.

Модуль не зависит от остальной программы и не использует сторонних библиотек: его проще проверять отдельно.

Правила лежат в настройках так:
    "rules": {"default": {...общие правила...}, "courses": {"123": {...правила только для курса 123...}}}
Правила курса полностью заменяют общие для тех полей, которые в них записаны.
"""
from __future__ import annotations

import hashlib
import json
import re

# ── Группы расширений: в списке можно писать «@документы», это короче, чем перечислять ───────────
EXT_GROUPS = {
    "документы": ["pdf", "doc", "docx", "odt", "rtf", "txt", "ppt", "pptx", "odp", "xls", "xlsx", "ods", "csv",
                  "djvu", "epub", "fb2"],
    "видео": ["mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp"],
    "аудио": ["mp3", "wav", "ogg", "m4a", "flac", "aac", "wma"],
    "архивы": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"],
    "картинки": ["jpg", "jpeg", "png", "gif", "bmp", "svg", "webp", "tif", "tiff"],
    "программы": ["exe", "msi", "dmg", "apk", "jar"],
}

# Типы элементов Moodle, которые можно отключать целиком (ключ — тип в Moodle).
MOD_CHOICES = [
    ("resource", "Файлы"), ("folder", "Папки"), ("url", "Ссылки"), ("page", "Страницы"),
    ("assign", "Задания"), ("book", "Книги"), ("quiz", "Тесты"), ("lesson", "Лекции (интерактивные)"),
    ("imscp", "Пакеты IMS"), ("scorm", "Пакеты SCORM"), ("file", "Файлы по ссылкам из текста"), ("text", "Текстовые блоки"), ("attendance", "Посещаемость"),
]

DEFAULT_RULES = {
    "ext_mode": "all",        # all — любые; only — только перечисленные; except — всё, кроме перечисленных
    "ext_list": [],           # расширения и группы: ["pdf", "docx", "@видео"]
    "max_mb": 0,              # файлы больше этого размера пропускаются (0 — без ограничения)
    "skip_mods": [],          # типы элементов, которые не качать целиком
    "skip_sections": [],      # разделы, в названии которых есть эти слова
    "skip_names": [],         # элементы и файлы, в названии которых есть эти слова
    "pdf": None,              # None — как в общих настройках, True/False — для этого курса отдельно
    "zip": None,
}
FILTER_KEYS = ("ext_mode", "ext_list", "max_mb", "skip_mods", "skip_sections", "skip_names")

PRESETS = {
    "all": {"title": "Всё подряд", "hint": "без ограничений (как раньше)", "rules": {}},
    "docs": {"title": "Только документы", "hint": "PDF, Word, PowerPoint, Excel, текст",
             "rules": {"ext_mode": "only", "ext_list": ["@документы"]}},
    "light": {"title": "Без видео и архивов", "hint": "и без файлов больше 200 МБ",
              "rules": {"ext_mode": "except", "ext_list": ["@видео", "@архивы", "@программы"], "max_mb": 200}},
    "tiny": {"title": "Экономия места", "hint": "документы до 25 МБ, без тестов и пакетов",
             "rules": {"ext_mode": "only", "ext_list": ["@документы"], "max_mb": 25,
                       "skip_mods": ["quiz", "lesson", "imscp", "scorm"]}},
}

# ── Шаблоны имён папок и файлов (режим «Свой шаблон») ─────────────────────────────────────────
DEFAULT_DIR_TPL = "{section_no}. {section}/{kind}"
DEFAULT_FILE_TPL = "{item}"
PLACEHOLDERS = {
    "section_no": "номер раздела (02)",
    "section": "название раздела",
    "kind": "тип занятия (Лекции, ПЗ, ЛЗ, Прочее)",
    "kind_code": "короткий код типа (ЛК, ПЗ, ЛЗ, ДР)",
    "seq": "номер элемента в разделе (03)",
    "item": "название элемента или файла",
    "type": "тип элемента Moodle (файл, страница…)",
}
LAYOUT_PRESETS = [
    {"title": "Раздел / Тип / файл", "dir": "{section_no}. {section}/{kind}", "file": "{item}"},
    {"title": "Тип / файл с номером", "dir": "{kind}", "file": "{section_no}_{seq} {item}"},
    {"title": "Всё в раздел", "dir": "{section_no}. {section}", "file": "{kind_code} {item}"},
    {"title": "Все файлы в одну папку", "dir": "", "file": "{section_no}_{kind_code}_{seq} {item}"},
]


class RuleError(ValueError):
    pass


def _as_list(v) -> list:
    """Список из строки (через запятую, точку с запятой или с новой строки) или из списка."""
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[,;\n]+", v)
    out = []
    for x in v:
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return out


def _tok_ext(t: str) -> str:
    t = t.strip()
    if t.startswith("@"):
        return "@" + t[1:].strip().lower()
    return t.lstrip("*.").strip().lower()


def _num(v, default=0.0) -> float:
    try:
        x = float(str(v).replace(",", ".").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(0.0, x)


def clean(raw: dict | None, *, partial: bool = False) -> dict:
    """Приводит правила к порядку. При partial=True оставляет только то, что реально задано."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}

    def want(k):
        return (k in raw) or not partial

    if want("ext_mode"):
        m = raw.get("ext_mode", "all")
        out["ext_mode"] = m if m in ("all", "only", "except") else "all"
    if want("ext_list"):
        out["ext_list"] = [t for t in (_tok_ext(x) for x in _as_list(raw.get("ext_list"))) if t]
    if want("max_mb"):
        x = _num(raw.get("max_mb"))
        out["max_mb"] = int(x) if x == int(x) else round(x, 1)
    if want("skip_mods"):
        known = {k for k, _ in MOD_CHOICES}
        out["skip_mods"] = [m for m in _as_list(raw.get("skip_mods")) if m in known]
    if want("skip_sections"):
        out["skip_sections"] = _as_list(raw.get("skip_sections"))
    if want("skip_names"):
        out["skip_names"] = _as_list(raw.get("skip_names"))
    for k in ("pdf", "zip"):
        if want(k):
            v = raw.get(k)
            out[k] = v if isinstance(v, bool) else None
    return out


def normalize(raw: dict | None) -> dict:
    """Весь набор правил из настроек: общие + по курсам."""
    raw = raw if isinstance(raw, dict) else {}
    courses = {}
    for cid, r in (raw.get("courses") or {}).items():
        if str(cid).isdigit() and isinstance(r, dict) and r:
            courses[str(int(cid))] = clean(r, partial=True)
    return {"default": clean(raw.get("default")), "courses": courses}


def effective(rules: dict | None, course_id) -> dict:
    """Правила для курса: общие, поверх них — записанное для самого курса."""
    rules = rules if isinstance(rules, dict) else {}
    eff = clean(rules.get("default"))
    over = (rules.get("courses") or {}).get(str(course_id))
    if isinstance(over, dict):
        eff.update(clean(over, partial=True))
    return eff


def validate(rules: dict | None) -> list[str]:
    """Понятные сообщения об ошибках в правилах (пустой список — всё в порядке)."""
    errs = []
    rules = normalize(rules)
    sets = [("общие правила", rules["default"])] + [(f"курс {c}", r) for c, r in rules["courses"].items()]
    for title, r in sets:
        for field, label in (("skip_sections", "пропуск разделов"), ("skip_names", "пропуск по названию")):
            for p in r.get(field, []):
                if p.lower().startswith("re:"):
                    try:
                        re.compile(p[3:])
                    except re.error as e:
                        errs.append(f"{title}: {label}: неверное выражение «{p[3:]}» ({e})")
        for t in r.get("ext_list", []):
            if t.startswith("@") and t[1:] not in EXT_GROUPS:
                errs.append(f"{title}: нет группы «{t}» (есть: {', '.join('@' + g for g in EXT_GROUPS)})")
    return errs


def _expand(tokens) -> set:
    out = set()
    for t in tokens:
        if t.startswith("@"):
            out.update(EXT_GROUPS.get(t[1:], []))
        else:
            out.add(t)
    return out


def _pattern(p: str):
    """Слово из правила: «re:…» — регулярное выражение, иначе обычное слово (регистр не важен)."""
    if p.lower().startswith("re:"):
        try:
            rx = re.compile(p[3:], re.I)
            return lambda s: bool(rx.search(s))
        except re.error:
            pass
    low = p.casefold()
    return lambda s: low in s.casefold()


def _ext_of(filename: str) -> str:
    m = re.search(r"\.([0-9A-Za-z_-]{1,10})$", filename or "")
    return m.group(1).lower() if m else ""


class CourseRules:
    """Готовые к проверке правила одного курса."""

    def __init__(self, eff: dict | None = None):
        self.eff = clean(eff)
        e = self.eff
        self.exts = _expand(e["ext_list"])
        self.ext_mode = e["ext_mode"] if self.exts else "all"
        self.max_bytes = int(float(e["max_mb"]) * 1024 * 1024) if e["max_mb"] else 0
        self.skip_mods = set(e["skip_mods"])
        self._sec = [(_pattern(p), p) for p in e["skip_sections"]]
        self._names = [(_pattern(p), p) for p in e["skip_names"]]
        self.pdf, self.zip = e["pdf"], e["zip"]

    @property
    def active(self) -> bool:
        return bool(self.ext_mode != "all" or self.max_bytes or self.skip_mods or self._sec or self._names)

    @property
    def sig(self) -> str:
        """Подпись правил: пока она не меняется, быстрая синхронизация может пропускать уже обработанное."""
        if not self.active:
            return ""
        data = {k: self.eff[k] for k in FILTER_KEYS}
        data["ext_list"] = sorted(self.exts)
        for k in ("skip_mods", "skip_sections", "skip_names"):
            data[k] = sorted(data[k])
        return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]

    def section_skip(self, section_name: str) -> str | None:
        for test, p in self._sec:
            if test(section_name or ""):
                return f"раздел «{section_name}» пропущен правилом «{p}»"
        return None

    def item_skip(self, modtype: str, item_name: str) -> str | None:
        if modtype in self.skip_mods:
            return f"тип «{modtype}» отключён в правилах"
        for test, p in self._names:
            if test(item_name or ""):
                return f"название подходит под правило «{p}»"
        return None

    def file_skip(self, filename: str) -> str | None:
        ext = _ext_of(filename)
        if self.ext_mode == "only" and ext not in self.exts:
            return f"расширение «{ext or 'нет'}» не входит в список разрешённых"
        if self.ext_mode == "except" and ext in self.exts:
            return f"расширение «{ext}» исключено правилами"
        for test, p in self._names:
            if test(filename or ""):
                return f"имя файла подходит под правило «{p}»"
        return None

    def size_skip(self, size: int | None) -> str | None:
        if self.max_bytes and size is not None and size > self.max_bytes:
            return f"файл {size / 1048576:.1f} МБ больше лимита {self.eff['max_mb']} МБ"
        return None


# ── Шаблоны ───────────────────────────────────────────────────────────────────────────────────
_PH = re.compile(r"\{(\w+)\}")


def template_errors(tpl: str) -> list[str]:
    bad = sorted({m for m in _PH.findall(tpl or "") if m not in PLACEHOLDERS})
    return [f"неизвестное поле {{{b}}} (есть: {', '.join('{' + k + '}' for k in PLACEHOLDERS)})" for b in bad]


def render_template(tpl: str, values: dict) -> str:
    """Подставляет значения; неизвестные поля заменяются пустотой."""
    return _PH.sub(lambda m: str(values.get(m.group(1), "")), tpl or "")


def dir_parts(tpl: str, values: dict, clean_part) -> list[str]:
    """Шаблон папки → список имён папок. Разделитель «/»; значения чистятся до подстановки, пустые части выбрасываются."""
    parts = []
    for raw in (tpl or "").replace("\\", "/").split("/"):
        s = render_template(raw, {k: clean_part(v) for k, v in values.items()}).strip(" .")
        if s:
            parts.append(clean_part(s))
    return parts

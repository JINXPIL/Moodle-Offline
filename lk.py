# -*- coding: utf-8 -*-
"""«Успеваемость» — электронная ведомость студента с lk.sevsu.ru (https://lk.sevsu.ru/student/grade).

Что это за страница: личный кабинет показывает все семестры учебного плана — предмет, вид контроля
(экзамен, зачёт, курсовая…), оценку, ЗЕТ, часы и дату. Оценка пустая, пока предмет не сдан.
Страница своя у каждого студента и открывается только после входа (учётная запись СевГУ).

Что делает модуль:
  • parse(html)       — разбирает страницу (вкладки-семестры, таблицы) в данные; ничего личного, кроме оценок,
                        не берёт: имя, группа и токены страницы не сохраняются;
  • stats / summary   — сдано/осталось, средний балл по оценкам 5–2, набранные ЗЕТ — для окна и страницы;
  • diff              — что изменилось с прошлого раза (появилась оценка, оценку исправили) → «Что нового»;
  • save / load       — `Успеваемость/lk_grades.json` и офлайн-страница `Успеваемость/index.html` в папке материалов;
  • import_html       — то же из сохранённой вручную страницы (запасной путь, если вход на сайт не получился);
  • fetch             — открыть страницу под своей учётной записью (вход — тот же, что у элективов и расписания).

Сторонние библиотеки: только bs4 (уже нужна программе). Пароль уходит только на https-страницы *.sevsu.ru.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import electives as el
import moodle_offline as mo
import news

LK_URL = "https://lk.sevsu.ru"
GRADE_URL = LK_URL + "/student/grade"
LK_DIR = "Успеваемость"
DATA_FILE = "lk_grades.json"
PAGE_FILE = "index.html"
MAX_IMPORT = 5 * 1024 * 1024          # сохранённая страница больше 5 МБ — это не она
TRUSTED_SUFFIX = ".sevsu.ru"
INSECURE_TEST = False                 # только в тестах: разрешить http и 127.0.0.1


class LkError(mo.MoodleError):
    diag: list = []        # журнал входа (адреса и названия форм) — для файла lk_debug.txt


# ── что считаем «своим» сайтом ─────────────────────────────────────────
def _trusted_host(host: str) -> bool:
    host = (host or "").lower()
    return host == "sevsu.ru" or host.endswith(TRUSTED_SUFFIX) or (INSECURE_TEST and host in ("127.0.0.1", "localhost"))


def trusted_url(url: str) -> bool:
    """Пароль и страницу с оценками берём только с https-адресов sevsu.ru."""
    p = urlparse(url or "")
    return (p.scheme == "https" or INSECURE_TEST) and _trusted_host(p.hostname or "")


# ══════════════════════════════════════════════════════════════════
#  РАЗБОР СТРАНИЦЫ
# ══════════════════════════════════════════════════════════════════
_ORD = {"первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "четвёртый": 4, "пятый": 5, "шестой": 6, "седьмой": 7,
        "восьмой": 8, "девятый": 9, "десятый": 10, "одиннадцатый": 11, "двенадцатый": 12}
_HEAD = {"#": "n", "№": "n", "предмет": "subject", "дисциплина": "subject", "вид контроля": "control",
         "оценка": "grade", "зет": "zet", "часы": "hours", "дата": "date"}
_DEFAULT_COLS = ["n", "subject", "control", "grade", "zet", "hours", "date"]
# оценка → (вид, балл). Вид: 5/4/3/2 — оценки; pass/fail — зачтено/не зачтено; absent — не явился; none — оценки ещё нет
_GRADES = {"отлично": ("5", 5), "хорошо": ("4", 4), "удовлетворительно": ("3", 3), "неудовлетворительно": ("2", 2)}
PASSED = ("5", "4", "3", "pass")
FAILED = ("2", "fail", "absent")


def _clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _num(s):
    m = re.search(r"\d+(?:[.,]\d+)?", s or "")
    if not m:
        return None
    v = float(m.group(0).replace(",", "."))
    return int(v) if v == int(v) else v


def grade_kind(text: str) -> tuple:
    """«Отлично» → ("5", 5), «Зачтено» → ("pass", None), пусто → ("none", None), непонятное → ("other", None)."""
    t = _clean(text).lower().replace("ё", "е")
    if not t or t in ("-", "—", "–"):
        return "none", None
    if t in _GRADES:
        return _GRADES[t]
    if t.startswith("не зачт") or t.startswith("незачт"):
        return "fail", None
    if t.startswith("зачт"):
        return "pass", None
    if "не явил" in t or "неявк" in t:
        return "absent", None
    return "other", None


def control_kind(text: str) -> str:
    t = _clean(text).lower().replace("ё", "е")
    if t.startswith("экзамен"):
        return "exam"
    if t.startswith("зачет") and ("диф" in t or "оценк" in t):
        return "credit_diff"
    if t.startswith("зачет"):
        return "credit"
    if "курсов" in t:
        return "course"
    if "практик" in t:
        return "practice"
    if "вкр" in t or "выпускн" in t or "госэкзамен" in t or "государственн" in t:
        return "thesis"
    return "other"


def _iso(date_text: str) -> str:
    m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", date_text or "")
    if not m:
        return ""
    d, mo_, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo_, d).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _program_name(s: str) -> str:
    s = re.sub(r"^\s*Специальность\s*:\s*", "", _clean(s), flags=re.I)
    return re.sub(r"^(\S+)\s*-\s*\1\s+", r"\1 ", s)       # сайт пишет код дважды: «10.03.01 - 10.03.01 Название»


def _semester_no(title: str, index: int) -> int:
    m = re.search(r"(\d+)\s*-?\s*(?:й\s+)?семестр", title or "", re.I)
    if m:
        return int(m.group(1))
    w = _clean(title).lower().split(" ")[0] if title else ""
    return _ORD.get(w, index + 1)


def _columns(table) -> list:
    heads = [_HEAD.get(_clean(th.get_text(" ")).lower(), "") for th in table.select("thead th")]
    return heads if heads and "subject" in heads else list(_DEFAULT_COLS)


def _row(tds, cols) -> dict | None:
    cell = {}
    for i, td in enumerate(tds):
        if i < len(cols) and cols[i]:
            cell[cols[i]] = _clean(td.get_text(" "))
    subject = cell.get("subject", "")
    if not subject:
        return None
    grade = cell.get("grade", "")
    gk, score = grade_kind(grade)
    date = cell.get("date", "")
    n = _num(cell.get("n", ""))
    return {"n": int(n) if isinstance(n, (int, float)) else None, "subject": subject,
            "control": cell.get("control", ""), "ck": control_kind(cell.get("control", "")),
            "grade": grade, "gk": gk, "score": score, "zet": _num(cell.get("zet", "")),
            "hours": _num(cell.get("hours", "")), "date": date, "iso": _iso(date)}


def _semester(title: str, index: int, scope) -> dict:
    rows = []
    for table in scope.select("table"):
        cols = _columns(table)
        trs = table.select("tbody tr") or table.select("tr")
        for tr in trs:
            tds = tr.find_all("td", recursive=False)
            if not tds:
                continue
            r = _row(tds, cols)
            if r:
                rows.append(r)
    return {"n": _semester_no(title, index), "title": _clean(title) or f"Семестр {index + 1}", "rows": rows}


def parse(text: str) -> dict:
    """Страница /student/grade → {"v":1,"programs":[{"name","semesters":[{"n","title","rows":[…]}]}]}.
    Имя студента и всё остальное, что не оценки, не читается. Пустой список programs — страница не та."""
    soup = BeautifulSoup(text or "", "html.parser")
    widget = soup.select_one(".grade-widget") or soup
    head = widget.select_one(".grade-header-title")
    head_name = _program_name(head.get_text(" ")) if head is not None else ""
    labels = {o.get("value", ""): _program_name(o.get_text(" ")) for o in soup.select("#speciality-select option")}
    containers = soup.select(".grade-container")
    programs = []
    for ci, cont in enumerate(containers or [widget]):
        tabs = {a.get("href", "")[1:]: _clean(a.get_text(" ")) for a in cont.select("ul.nav-tabs a[href^='#']")}
        panes = cont.select(".tab-pane")
        sems = []
        if panes:
            for pi, pane in enumerate(panes):
                sems.append(_semester(tabs.get(pane.get("id", ""), ""), pi, pane))
        else:
            for pi, table in enumerate(cont.select("table")):
                sems.append(_semester("", pi, table.parent if table.parent is not None else table))
        sems = [s for s in sems if s["rows"]]
        if not sems:
            continue
        name = labels.get(cont.get("id", "")) or (head_name if len(containers) <= 1 else "") or f"Программа {ci + 1}"
        programs.append({"name": name, "semesters": sems})
    return {"v": 1, "url": GRADE_URL, "programs": programs}


def has_rows(data) -> bool:
    return bool(isinstance(data, dict) and any(s.get("rows") for p in data.get("programs", []) for s in p.get("semesters", [])))


# ══════════════════════════════════════════════════════════════════
#  ЦИФРЫ ДЛЯ ОКНА
# ══════════════════════════════════════════════════════════════════
def stats(rows: list) -> dict:
    """Сдано / не сдано / ещё нет оценки, средний балл по оценкам 5–2 (зачёты без оценки в него не входят), ЗЕТ."""
    graded = [r for r in rows if r.get("gk") not in ("none", None)]
    passed = [r for r in graded if r["gk"] in PASSED]
    failed = [r for r in graded if r["gk"] in FAILED]
    scores = [r["score"] for r in rows if r.get("score")]
    zet_all = sum(r.get("zet") or 0 for r in rows)
    zet_done = sum(r.get("zet") or 0 for r in passed)
    return {"total": len(rows), "graded": len(graded), "passed": len(passed), "failed": len(failed),
            "left": len(rows) - len(graded), "avg": round(sum(scores) / len(scores), 2) if scores else None,
            "n5": sum(1 for s in scores if s == 5), "n4": sum(1 for s in scores if s == 4),
            "n3": sum(1 for s in scores if s == 3), "n2": sum(1 for s in scores if s == 2),
            "zet_all": round(zet_all, 2) if zet_all % 1 else int(zet_all),
            "zet_done": round(zet_done, 2) if zet_done % 1 else int(zet_done)}


def current_semester(program: dict) -> int:
    """Какой семестр показывать первым: последний, где уже есть хоть одна оценка (иначе первый)."""
    best = None
    for s in program.get("semesters", []):
        if any(r.get("gk") != "none" for r in s.get("rows", [])):
            best = s["n"] if best is None else max(best, s["n"])
    if best is None and program.get("semesters"):
        best = program["semesters"][0]["n"]
    return best or 1


def summary(data: dict) -> list:
    out = []
    for p in (data or {}).get("programs", []):
        allrows = [r for s in p["semesters"] for r in s["rows"]]
        out.append({"name": p["name"], "stats": stats(allrows), "cur": current_semester(p),
                    "semesters": [{"n": s["n"], "title": s["title"], "stats": stats(s["rows"])} for s in p["semesters"]]})
    return out


# ══════════════════════════════════════════════════════════════════
#  ЧТО ИЗМЕНИЛОСЬ
# ══════════════════════════════════════════════════════════════════
def _index(data) -> dict:
    out, seen = {}, {}
    for p in (data or {}).get("programs", []):
        for s in p.get("semesters", []):
            for r in s.get("rows", []):
                k = (p["name"], s["n"], r["subject"].casefold(), r["control"].casefold())
                seen[k] = seen.get(k, 0) + 1
                out[k + (seen[k],)] = (p["name"], s["n"], r)
    return out


def diff(old, new) -> list:
    """Что поменялось между двумя снимками: новая оценка (kind="new") или исправленная (kind="changed").
    Нет прошлого снимка — ничего не сообщаем: первый раз просто запоминаем, что уже стоит."""
    if not has_rows(old):
        return []
    before, after = _index(old), _index(new)
    ch = []
    for k, (prog, sem, r) in after.items():
        if r["gk"] == "none":
            continue
        prev = before.get(k)
        was = prev[2] if prev else None
        if was is None or was["gk"] == "none":
            ch.append({"kind": "new", "program": prog, "sem": sem, "subject": r["subject"], "control": r["control"],
                       "grade": r["grade"], "old": ""})
        elif _clean(was["grade"]).casefold() != _clean(r["grade"]).casefold():
            ch.append({"kind": "changed", "program": prog, "sem": sem, "subject": r["subject"], "control": r["control"],
                       "grade": r["grade"], "old": was["grade"]})
    return ch


def news_events(changes: list) -> list:
    """Изменения → события для «Что нового» (оценка — личное: во внешние каналы по умолчанию не уходит)."""
    evs = []
    for c in changes:
        what = f"Оценка исправлена: {c['old']} → {c['grade']}" if c["kind"] == "changed" else f"Оценка: {c['grade']}"
        detail = f"{what} · {c['control']} · {c['sem']} семестр".replace("  ", " ")
        evs.append(news.make_event("lk_grade", 0, "Успеваемость", c["subject"], detail=detail, url=GRADE_URL,
                                   section=f"{c['sem']} семестр", key=f"{c['program']}|{c['sem']}|{c['subject']}|{c['control']}",
                                   secret=True))
    return evs


# ══════════════════════════════════════════════════════════════════
#  ХРАНЕНИЕ И ОФЛАЙН-СТРАНИЦА
# ══════════════════════════════════════════════════════════════════
def _paths(out_dir):
    root = Path(out_dir) / LK_DIR
    return root, root / DATA_FILE, root / PAGE_FILE


def load(out_dir) -> dict | None:
    _, f, _ = _paths(out_dir)
    if not f.exists():
        return None
    try:
        data = json.loads(mo.read_text_retry(f))
        if not isinstance(data, dict) or not isinstance(data.get("programs"), list):
            raise ValueError("не тот формат")
        return data
    except ValueError:
        mo.keep_broken(f)
        return None
    except OSError:
        return None


def save(out_dir, data: dict, now=None) -> list:
    """Записать снимок и страницу; вернуть изменения относительно прошлого снимка (для «Что нового»)."""
    root, f, page = _paths(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    changes = diff(load(out_dir), data)
    data = {**data, "fetched": int(now if now is not None else time.time())}
    mo.write_text_atomic(f, json.dumps(data, ensure_ascii=False, indent=1), durable=True)
    mo.write_text_atomic(page, render_html(data))
    return changes


def import_html(out_dir, raw: bytes, now=None) -> tuple:
    """Страница, сохранённая из браузера («Сохранить как…»): разобрать и записать так же, как с сайта.
    Возвращает (данные, изменения). Не та страница → LkError."""
    if not raw:
        raise LkError("файл пустой")
    if len(raw) > MAX_IMPORT:
        raise LkError("файл слишком большой для страницы успеваемости")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            text = ""
    data = parse(text)
    if not has_rows(data):
        raise LkError("в файле нет таблиц успеваемости: откройте «Сведения об успеваемости» в личном кабинете и "
                      "сохраните именно эту страницу (Ctrl+S, «Веб-страница, только HTML»)")
    return data, save(out_dir, data, now)


GRADE_CLASS = {"5": "g5", "4": "g4", "3": "g3", "2": "g2", "pass": "gp", "fail": "gf", "absent": "gf", "none": "g0", "other": "g0"}


def _fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def render_html(data: dict) -> str:
    """Самодержащая страница без скриптов: открывается без программы и без интернета."""
    e = htmllib.escape
    parts = []
    for p, sm in zip(data.get("programs", []), summary(data)):
        st = sm["stats"]
        parts.append(f"<h2>{e(p['name'])}</h2>")
        avg = f"{st['avg']:.2f}".replace(".", ",") if st["avg"] is not None else "—"
        parts.append(f"<p class=sum>Сдано {st['passed']} из {st['total']} · не сдано {st['failed']} · ещё нет оценки {st['left']} · "
                     f"средний балл {avg} · ЗЕТ {st['zet_done']} из {st['zet_all']}</p>")
        for s, ss in zip(p["semesters"], sm["semesters"]):
            parts.append(f"<h3>{e(s['title'])}</h3><div class=tw><table><thead><tr><th>#<th>Предмет<th>Вид контроля<th>Оценка"
                         f"<th>ЗЕТ<th>Часы<th>Дата</thead><tbody>")
            for r in s["rows"]:
                g = e(r["grade"]) if r["grade"] else "—"
                parts.append(f"<tr><td>{r['n'] if r['n'] is not None else ''}<td class=l>{e(r['subject'])}<td>{e(r['control'])}"
                             f"<td><span class='b {GRADE_CLASS.get(r['gk'], 'g0')}'>{g}</span>"
                             f"<td>{'' if r['zet'] is None else r['zet']}<td>{'' if r['hours'] is None else r['hours']}"
                             f"<td>{e(r['date'])}")
            parts.append("</tbody></table></div>")
    stamp = _fmt_ts(data.get("fetched"))
    return ("<!DOCTYPE html><html lang=ru><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Успеваемость</title><style>"
            ":root{color-scheme:light dark;--bg:#fff;--fg:#1c1f24;--mut:#6b7280;--ln:#e5e7eb}"
            "@media(prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e8eaed;--mut:#9aa0a6;--ln:#2d3139}}"
            "body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;max-width:980px;margin:auto}"
            "h1{font-size:1.4rem}h2{font-size:1.1rem;margin:22px 0 4px}h3{font-size:1rem;margin:18px 0 6px}"
            ".sum,.stamp{color:var(--mut);margin:2px 0 8px}.tw{overflow-x:auto}table{border-collapse:collapse;width:100%}"
            "th,td{border-bottom:1px solid var(--ln);padding:6px 8px;text-align:center;vertical-align:top}th{font-size:.8rem;color:var(--mut)}"
            ".l{text-align:left}.b{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.85rem;white-space:nowrap}"
            ".g5,.gp{background:#d1fae5;color:#065f46}.g4{background:#dbeafe;color:#1e40af}.g3{background:#fef3c7;color:#92400e}"
            ".g2,.gf{background:#fee2e2;color:#991b1b}.g0{color:var(--mut)}"
            "</style></head><body><h1>Успеваемость</h1>"
            f"<p class=stamp>Сохранено программой Moodle Offline{(' · обновлено ' + stamp) if stamp else ''}. "
            "Источник: lk.sevsu.ru. Средний балл посчитан программой по оценкам 5–2 и может отличаться от официального.</p>"
            + "".join(parts) + "</body></html>")


# ══════════════════════════════════════════════════════════════════
#  ВХОД И ЗАГРУЗКА С САЙТА
# ══════════════════════════════════════════════════════════════════
class _Portal(el.Portal):
    """Тот же вход, что у элективов и личного расписания, но пароль и любые формы уходят только на *.sevsu.ru по https."""

    def _login(self, r, text):
        orig = self.c.request

        def guarded(method, url, *a, **kw):
            if str(method).upper() != "GET" and not trusted_url(url):
                raise el.PortalError(f"страница входа {(urlparse(url).hostname or '?')} — не сайт СевГУ: пароль туда не отправляю")
            return orig(method, url, *a, **kw)

        self.c.request = guarded
        try:
            super()._login(r, text)
        finally:
            self.c.request = orig


def fetch(rep, control, creds=None, cookies=None, use_system_proxy=False, page_delay=None, url=None):
    """Открыть страницу успеваемости под своей учётной записью → (данные, cookie для следующего раза, журнал входа).
    Журнал — только адреса и названия форм, без паролей и без содержимого страницы."""
    url = url or GRADE_URL
    if not trusted_url(url):
        raise LkError("адрес не похож на сайт СевГУ")
    portal = _Portal(url, rep, control, creds=creds, cookies=cookies, use_system_proxy=use_system_proxy,
                     page_delay=mo.PAGE_DELAY if page_delay is None else page_delay)
    try:
        try:
            r, text, _soup = portal.get()
        except el.PortalError as e:
            raise LkError(str(e)) from e
        if not trusted_url(r.url):
            raise LkError("сайт перенаправил не на страницу успеваемости")
        data = parse(text)
        if not has_rows(data):
            raise LkError("страница открылась, но таблиц успеваемости на ней нет (сайт мог поменять вёрстку) — "
                          "можно сохранить страницу из браузера и открыть её кнопкой «Из файла»")
    except LkError as e:
        e.diag = list(portal.diag)
        raise
    return data, portal.cookies(), portal.diag

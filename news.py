"""Что нового: события по курсам, лента «непрочитанного» и уведомления (Windows, Telegram, webhook).

Модуль без сторонних библиотек (requests подключается только при отправке).
Здесь нет обращений к Moodle: страницы скачивает moodle_offline.py и отдаёт сюда уже разобранный HTML.

Как работает «новое»: после обработки курса программа сравнивает его с тем, что запомнила в прошлый раз
(список элементов, оценки, id обсуждений в «Объявлениях»). Первый запуск для курса — только запоминание,
чтобы не получить сотню «новых» материалов, которые просто скачались впервые.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

NEWS_FILE = ".moodle_news.json"      # лента событий, лежит в папке с материалами
MAX_EVENTS = 1000                    # старше — выбрасываем (непрочитанные не трогаем, пока их меньше предела)

# вид события → (название, подсказка для настроек, следить по умолчанию, уведомлять по умолчанию)
KINDS = {
    "item_new": ("Новый материал", "в курсе появился новый элемент: файл, задание, тест, ссылка…", True, True),
    "file_new": ("Новый файл", "в уже известный элемент (например, папку) положили новый файл", True, True),
    "announcement": ("Объявление", "новая тема в форуме «Объявления» (или «Новости»)", True, True),
    "grade": ("Оценка", "новая оценка или комментарий преподавателя в журнале оценок", True, True),
    "item_gone": ("Убрано с сайта", "преподаватель скрыл или удалил элемент (у вас копия останется)", True, False),
    "item_renamed": ("Переименовано", "у элемента сменилось название", False, False),
}
WEBHOOK_FORMATS = {
    "json": "JSON {title, text, events} — для своих серверов, Make, n8n",
    "discord": "Discord (поле content)",
    "slack": "Slack / Mattermost (поле text)",
    "plain": "Простой текст — ntfy и похожие",
}
DEFAULTS = {
    "enabled": True,                                           # следить за новым вообще
    "track": {k: v[2] for k, v in KINDS.items()},              # какие виды событий собирать в ленту
    "push": {k: v[3] for k, v in KINDS.items()},               # о каких присылать уведомления
    "toast": True,                                             # окно Windows (или notify-send)
    "telegram": False, "telegram_chat": "",                    # токен бота хранится отдельно, зашифрованно
    "webhook": False, "webhook_format": "json",                # адрес webhook — тоже секрет
    "mute": [],                                                # id курсов без уведомлений (в ленте они остаются)
    "share_grades": False,                                     # показывать значения оценок в Telegram/webhook
    "forum_words": ["объявлен", "новост", "announce", "news"],  # какие форумы считать «Объявлениями»
    "backfill": 3,                                             # сколько последних объявлений сохранить при первом запуске
    "max_lines": 12,                                           # строк в одном уведомлении
}
EMPTY_GRADE = {"", "-", "—", "–"}


# ════════════════════════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ════════════════════════════════════════════════════════════════════════════
def _as_list(v) -> list:
    if isinstance(v, str):
        v = re.split(r"[\n,;]+", v)
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, (list, tuple)) else []


def normalize(cfg) -> dict:
    """Полный набор настроек: чего нет — по умолчанию, лишнее и неверные типы выбрасываем."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out = json.loads(json.dumps(DEFAULTS))
    for k in ("enabled", "toast", "telegram", "webhook", "share_grades"):
        if k in cfg:
            out[k] = bool(cfg[k])
    for grp in ("track", "push"):
        src = cfg.get(grp)
        if isinstance(src, dict):
            for k in KINDS:
                if k in src:
                    out[grp][k] = bool(src[k])
    out["telegram_chat"] = str(cfg.get("telegram_chat") or "").strip()
    fmt = str(cfg.get("webhook_format") or "json")
    out["webhook_format"] = fmt if fmt in WEBHOOK_FORMATS else "json"
    words = _as_list(cfg.get("forum_words", DEFAULTS["forum_words"]))
    out["forum_words"] = [w[:40] for w in words][:20] or list(DEFAULTS["forum_words"])
    mute = []
    for x in cfg.get("mute") or []:
        try:
            mute.append(int(x))
        except (TypeError, ValueError):
            pass
    out["mute"] = sorted(set(mute))
    for k, lo, hi in (("backfill", 0, 20), ("max_lines", 3, 50)):
        try:
            out[k] = min(hi, max(lo, int(cfg.get(k, DEFAULTS[k]))))
        except (TypeError, ValueError):
            pass
    return out


def validate(cfg) -> list[str]:
    errs = []
    cfg = cfg if isinstance(cfg, dict) else {}
    chat = str(cfg.get("telegram_chat") or "").strip()
    if chat and not re.fullmatch(r"-?\d{3,20}|@[A-Za-z0-9_]{5,32}", chat):
        errs.append("Telegram: chat id — это число (например 123456789) или @имя_канала")
    if cfg.get("webhook_format") not in (None, *WEBHOOK_FORMATS):
        errs.append("webhook: неизвестный формат")
    return errs


def check_webhook_url(url: str) -> str | None:
    """Текст ошибки или None."""
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        return "адрес webhook должен начинаться с http:// или https://"
    return None


def kinds_meta() -> list[dict]:
    return [{"key": k, "title": v[0], "hint": v[1], "track": v[2], "push": v[3]} for k, v in KINDS.items()]


def is_news_forum(name: str, words) -> bool:
    n = (name or "").lower()
    return any(w.lower() in n for w in words or [])


# ════════════════════════════════════════════════════════════════════════════
#  ЛЕНТА СОБЫТИЙ
# ════════════════════════════════════════════════════════════════════════════
def _clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _short(s: str, n: int) -> str:
    s = _clean(s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def make_event(kind: str, course_id, course: str, title: str, *, detail="", url="", path="", section="",
               files=None, key="", secret=False) -> dict:
    """secret=True — в detail личные данные (оценка): во внешние каналы их по умолчанию не отправляем."""
    eid = hashlib.sha1(f"{kind}|{course_id}|{key or title}|{detail}".encode("utf-8")).hexdigest()[:16]
    return {"id": eid, "ts": int(time.time()), "kind": kind, "course_id": course_id, "course": course,
            "title": _short(title, 200), "detail": _short(detail, 400), "url": url or "", "path": path or "",
            "section": _short(section, 120), "files": [_short(f, 120) for f in (files or [])][:8],
            "secret": bool(secret), "read": False}


class NewsLog:
    """Лента событий в файле .moodle_news.json. Читает заново, если файл изменила другая программа (консольный запуск)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.events: list[dict] = []
        self._mtime = None
        self._load()

    def _stat(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self):
        self._mtime = self._stat()
        self.events = []
        if self._mtime is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ev = data.get("events") if isinstance(data, dict) else None
            self.events = [e for e in (ev or []) if isinstance(e, dict) and e.get("id")]
        except (OSError, ValueError):
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass

    def _refresh(self):
        if self._stat() != self._mtime:
            self._load()

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"version": 1, "events": self.events}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self._stat()

    def add(self, events: list[dict]) -> list[dict]:
        """Добавляет новые события (одинаковые по id пропускает). Возвращает то, что реально добавлено."""
        with self.lock:
            self._refresh()
            have = {e["id"] for e in self.events}
            added = []
            for e in events:
                if e["id"] not in have:
                    have.add(e["id"])
                    added.append(e)
            if added:
                self.events = added + self.events   # новое сверху; внутри одного запуска порядок как найден
                if len(self.events) > MAX_EVENTS:
                    keep = [e for e in self.events if not e.get("read")][:MAX_EVENTS]
                    keep_ids = {e["id"] for e in keep}
                    rest = [e for e in self.events if e["id"] not in keep_ids][: max(0, MAX_EVENTS - len(keep))]
                    self.events = sorted(keep + rest, key=lambda e: -e.get("ts", 0))
                self._save()
            return added

    def unread(self) -> int:
        with self.lock:
            self._refresh()
            return sum(1 for e in self.events if not e.get("read"))

    def mark_read(self, ids=None) -> int:
        """ids=None — всё. Возвращает, сколько стало прочитанным."""
        with self.lock:
            self._refresh()
            want = None if ids is None else set(ids)
            n = 0
            for e in self.events:
                if not e.get("read") and (want is None or e["id"] in want):
                    e["read"] = True
                    n += 1
            if n:
                self._save()
            return n

    def clear_read(self) -> int:
        with self.lock:
            self._refresh()
            keep = [e for e in self.events if not e.get("read")]
            n = len(self.events) - len(keep)
            if n:
                self.events = keep
                self._save()
            return n

    def listing(self, *, limit=300, kind="", course=0, unread_only=False) -> dict:
        with self.lock:
            self._refresh()
            courses = {}
            for e in self.events:
                c = courses.setdefault(e.get("course_id"), {"id": e.get("course_id"), "name": e.get("course", ""), "count": 0})
                c["count"] += 1
            rows = [e for e in self.events
                    if (not kind or e.get("kind") == kind) and (not course or e.get("course_id") == course)
                    and (not unread_only or not e.get("read"))]
            return {"events": rows[:limit], "total": len(self.events), "shown": min(len(rows), limit),
                    "unread": sum(1 for e in self.events if not e.get("read")),
                    "courses": sorted(courses.values(), key=lambda c: c["name"].lower())}


# ════════════════════════════════════════════════════════════════════════════
#  СРАВНЕНИЕ С ПРОШЛЫМ РАЗОМ
# ════════════════════════════════════════════════════════════════════════════
def _basename(path: str) -> str:
    return os.path.basename(path or "")


def diff_items(course_id, course_name, prev, now, item_new_tracked=True):
    """prev — {key: {"n": название, "t": тип, "f": [названия файлов]}} с прошлого раза или None (первый запуск курса);
    now — [{"key","name","modtype","url","section","note","files":[{"title","path"}]}].
    Возвращает (события, новый_prev, подозрительно). Новые файлы определяем по спискам файлов, а не по тому, что только
    что скачано, — поэтому прерванная загрузка ничего не теряет. Длинные текстовые блоки («text») не отслеживаем:
    их ключ зависит от текста, любая правка выглядела бы новым элементом."""
    tracked = [it for it in now if it["modtype"] != "text"]
    seen = {it["key"]: {"n": it["name"], "t": it["modtype"], "f": [f.get("title", "") for f in it.get("files", [])]}
            for it in tracked}
    if prev is None:
        return [], seen, False
    events = []

    def file_event(it, files, key):
        names = [f.get("title") or _basename(f.get("path")) for f in files]
        detail = ", ".join(names[:3]) + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")
        return make_event("file_new", course_id, course_name, it["name"] if it["modtype"] != "file" else names[0] or it["name"],
                          detail=detail if it["modtype"] != "file" else it["name"], url=it.get("url"),
                          path=next((f["path"] for f in files if f.get("path")), ""), section=it.get("section", ""),
                          files=names, key=key)

    for it in tracked:
        old = prev.get(it["key"])
        files = it.get("files", [])
        first = next((f["path"] for f in files if f.get("path")), "")
        if it["modtype"] == "file":      # ссылка на файл из текста раздела: новая ссылка — новый файл
            if old is None and files:
                events.append(file_event(it, files, it["key"]))
            continue
        if old is None:
            detail = it.get("section", "")
            if it.get("note"):
                detail = (detail + " · " if detail else "") + it["note"]
            if item_new_tracked:
                events.append(make_event("item_new", course_id, course_name, it["name"], detail=detail, url=it.get("url"),
                                         path=first, section=it.get("section", ""), files=[f.get("title", "") for f in files],
                                         key=it["key"]))
            elif files:
                events.append(file_event(it, files, it["key"]))
            continue
        if old.get("n") != it["name"]:
            events.append(make_event("item_renamed", course_id, course_name, it["name"], detail=f"было: {old.get('n', '')}",
                                     url=it.get("url"), path=first, section=it.get("section", ""), key=it["key"]))
        old_files = old.get("f")
        if old_files is not None:
            fresh = [f for f in files if f.get("title", "") not in old_files]
            if fresh:
                events.append(file_event(it, fresh, it["key"] + "|" + "|".join(f.get("title", "") for f in fresh)))
    gone = [k for k, v in prev.items() if k not in seen and v.get("t") != "file"]
    # Если «пропала» половина курса — скорее всего страница разобрана не до конца, а не преподаватель всё удалил.
    suspicious = len(prev) >= 6 and len(gone) > max(3, len(prev) // 2)
    if suspicious:
        seen.update({k: prev[k] for k in gone})
    else:
        for k in gone:
            events.append(make_event("item_gone", course_id, course_name, prev[k].get("n", k), key=k,
                                     detail="убрано со страницы курса; скачанное осталось у вас"))
    return events, seen, suspicious


# ── объявления ──────────────────────────────────────────────────────────────
def parse_discussions(soup, base_url, limit=30) -> list[dict]:
    """Темы форума со страницы mod/forum/view.php → [{"id","title","url"}], как на странице (новые сверху)."""
    best: dict[int, dict] = {}
    order: list[int] = []
    for a in soup.select("a[href*='discuss.php']"):
        href = urljoin(base_url, a.get("href") or "")
        p = urlparse(href)
        q = parse_qs(p.query)
        d = (q.get("d") or [""])[0]
        if not d.isdigit() or p.fragment or q.get("parent") or q.get("reply"):
            continue
        d = int(d)
        text = _clean(a.get_text(" "))
        if d not in best:
            order.append(d)
            best[d] = {"id": d, "title": "", "url": f"{p.scheme}://{p.netloc}{p.path}?d={d}"}
        if len(text) > len(best[d]["title"]):
            best[d]["title"] = _short(text, 200)
    return [best[d] for d in order][:limit]


def parse_post(soup) -> dict:
    """Первое сообщение темы: {"title","author","when","text","html"}. Подходит под разметку Moodle 3.x и 4.x,
    а если ничего не узнали — берёт основную область страницы."""
    box = soup.select_one(".forumpost, article[data-region='post'], [data-region='post'], .forum-post-container")
    area = box
    if area is None:
        area = soup.select_one("#region-main [role='main'], [role='main'], #region-main, #page-content") or soup.body or soup
    body = (box.select_one(".posting, .post-content-container, [data-content='forum-post-core']") if box else None) or area
    subj = box.select_one(".subject, [data-region-content='forum-post-core-subject'], h3, h2") if box else None
    if subj is None:
        subj = soup.select_one("h2, h1")
    author = box.select_one(".author a, [data-region='author'] a") if box else None
    when = box.select_one("time[datetime]") if box else None
    return {"title": _short(subj.get_text(" ") if subj else "", 200), "author": _clean(author.get_text(" ")) if author else "",
            "when": (when.get("datetime") if when else "") or "", "text": _clean(body.get_text(" ")), "html": body}


# ── оценки ──────────────────────────────────────────────────────────────────
def _grade_value(cell) -> str:
    t = _clean(cell.get_text(" ")) if cell is not None else ""
    return "" if t in EMPTY_GRADE else t


def parse_grades(soup) -> dict | None:
    """Отчёт «Оценки пользователя» → {"Название": {"g": оценка, "p": проценты, "f": комментарий}} или None,
    если таблицы нет (нет доступа, другая тема оформления)."""
    table = soup.select_one("table.user-grade, table#user-grade")
    if table is None:
        table = next((t for t in soup.select("table") if t.select_one("th.column-itemname, td.column-itemname")), None)
    if table is None:
        return None
    rows: dict[str, dict] = {}
    for tr in table.select("tr"):
        nm = tr.select_one("th.column-itemname, td.column-itemname")
        gr = tr.select_one("td.column-grade")
        if nm is None or gr is None:
            continue
        name = _clean(nm.get_text(" "))
        if not name:
            continue
        base, n = name, 2
        while name in rows:
            name, n = f"{base} ({n})", n + 1
        rows[name] = {"g": _grade_value(gr), "p": _grade_value(tr.select_one("td.column-percentage")),
                      "f": _short(tr.select_one("td.column-feedback").get_text(" "), 300) if tr.select_one("td.column-feedback") else ""}
    return rows


def diff_grades(course_id, course_name, prev, now, url=""):
    """prev — прошлый снимок или None (первый раз). Возвращает события; «пусто → оценка» и «оценка → другая» считаем новостью."""
    if prev is None:
        return []
    events = []
    for name, cur in now.items():
        old = prev.get(name)
        og, ng = (old or {}).get("g", ""), cur.get("g", "")
        of, nf = (old or {}).get("f", ""), cur.get("f", "")
        if ng != og and ng:
            detail = f"{og} → {ng}" if og else ng
            if cur.get("p"):
                detail += f" ({cur['p']})"
            events.append(make_event("grade", course_id, course_name, name, detail=detail, url=url, secret=True, key=name))
        elif not ng and og:
            events.append(make_event("grade", course_id, course_name, name, detail="оценка снята", url=url, secret=True,
                                     key=name + "|снята"))
        elif nf and nf != of:
            events.append(make_event("grade", course_id, course_name, name, detail="комментарий преподавателя: " + _short(nf, 160),
                                     url=url, secret=True, key=name + "|" + nf))
    return events


# ════════════════════════════════════════════════════════════════════════════
#  ТЕКСТ УВЕДОМЛЕНИЯ
# ════════════════════════════════════════════════════════════════════════════
def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def event_line(e: dict, hide_secrets=False) -> str:
    label = KINDS.get(e.get("kind"), (e.get("kind", ""),))[0]
    line = f"{label}: {e.get('title', '')}"
    detail = "" if (hide_secrets and e.get("secret")) else e.get("detail", "")
    if detail:
        line += f" — {detail}"
    return _short(line, 160)


def build_digest(events: list[dict], *, hide_secrets=False, max_lines=12, app="Moodle Offline") -> tuple[str, str]:
    """→ (заголовок, текст). Группируем по курсам, лишнее сворачиваем в «…и ещё N»."""
    n = len(events)
    title = f"{app}: {n} {plural(n, 'новое событие', 'новых события', 'новых событий')}"
    by_course: dict[str, list] = {}
    for e in events:
        by_course.setdefault(e.get("course") or "Курс", []).append(e)
    lines, used = [], 0
    for course, evs in by_course.items():
        if used >= max_lines:
            break
        lines.append(f"{course}:")
        for e in evs:
            if used >= max_lines:
                break
            lines.append("  • " + event_line(e, hide_secrets))
            used += 1
    if used < n:
        lines.append(f"…и ещё {n - used}")
    return title, "\n".join(lines)


def toast_text(events: list[dict], hide_secrets=False) -> str:
    """Короткий текст для всплывающего окна Windows: пара первых событий в одну строку."""
    parts = [event_line(e, hide_secrets) for e in events[:3]]
    rest = len(events) - len(parts)
    return _short(" · ".join(parts) + (f" · …и ещё {rest}" if rest > 0 else ""), 240)


# ════════════════════════════════════════════════════════════════════════════
#  ОТПРАВКА (Telegram, webhook)
# ════════════════════════════════════════════════════════════════════════════
def scrub(text, *secrets) -> str:
    """Убирает секреты (токен, адрес webhook) из текста ошибки — чтобы они не попали ни в журнал, ни на экран."""
    t = str(text)
    for s in secrets:
        if s and len(s) >= 4:
            t = t.replace(s, "***")
    t = re.sub(r"/bot[0-9]+:[A-Za-z0-9_-]+", "/bot***", t)
    return t


def net_error(e: Exception, *secrets) -> str:
    """Коротко и без секретов: что пошло не так при отправке."""
    name = type(e).__name__
    if "Timeout" in name:
        return "время ожидания вышло"
    if "SSL" in name:
        return "ошибка сертификата сайта"
    if "Connection" in name:
        return "нет соединения (проверьте интернет и адрес)"
    return f"{name}: {scrub(e, *secrets)}"[:120]


def _chunks(text: str, size: int):
    buf = ""
    for line in text.split("\n"):
        while len(line) > size:
            if buf:
                yield buf
                buf = ""
            yield line[:size]
            line = line[size:]
        if len(buf) + len(line) + 1 > size:
            yield buf
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        yield buf


def _post(url, *, system_proxy=False, timeout=20, **kw):
    import requests
    s = requests.Session()
    s.trust_env = bool(system_proxy)   # по умолчанию прокси из системы не берём — как и для Moodle
    return s.post(url, timeout=timeout, **kw)


def send_telegram(token: str, chat: str, text: str, *, use_system_proxy=False, timeout=20) -> tuple[bool, str]:
    if not token or not chat:
        return False, "не заданы токен бота и chat id"
    try:
        for part in _chunks(text, 3800):
            r = _post(f"https://api.telegram.org/bot{token}/sendMessage", system_proxy=use_system_proxy, timeout=timeout,
                      data={"chat_id": chat, "text": part, "disable_web_page_preview": "true"})
            try:
                js = r.json()
            except ValueError:
                js = {}
            if not js.get("ok"):
                desc = scrub(js.get("description") or f"HTTP {r.status_code}", token)
                hint = " — проверьте токен бота" if r.status_code in (401, 404) else (
                    " — напишите боту любое сообщение и проверьте chat id" if r.status_code in (400, 403) else "")
                return False, desc + hint
        return True, "отправлено"
    except Exception as e:
        return False, net_error(e, token)


def _mime_header(s: str) -> str:
    try:
        s.encode("ascii")
        return s
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode("ascii") + "?="


def send_webhook(url: str, fmt: str, title: str, text: str, events: list[dict], *, hide_secrets=False,
                 use_system_proxy=False, timeout=20) -> tuple[bool, str]:
    err = check_webhook_url(url)
    if err:
        return False, err
    sp = use_system_proxy
    body = f"{title}\n\n{text}" if text else title
    try:
        if fmt == "discord":
            r = _post(url, system_proxy=sp, timeout=timeout, json={"content": body[:1900]})
        elif fmt == "slack":
            r = _post(url, system_proxy=sp, timeout=timeout, json={"text": body})
        elif fmt == "plain":
            r = _post(url, system_proxy=sp, timeout=timeout, data=body.encode("utf-8"),
                      headers={"Title": _mime_header(title), "Content-Type": "text/plain; charset=utf-8"})
        else:
            evs = [{"kind": e.get("kind"), "course": e.get("course"), "title": e.get("title"),
                    "detail": "" if (hide_secrets and e.get("secret")) else e.get("detail", ""), "url": e.get("url", "")}
                   for e in events]
            r = _post(url, system_proxy=sp, timeout=timeout, json={"title": title, "text": text, "events": evs})
        if 200 <= r.status_code < 300:
            return True, "отправлено"
        return False, f"сервер ответил HTTP {r.status_code}"
    except Exception as e:
        return False, net_error(e, url)


def mask_secret(s: str) -> str:
    s = s or ""
    return ("•••• " + s[-4:]) if len(s) >= 8 else ("••••" if s else "")


def mask_url(u: str) -> str:
    p = urlparse(u or "")
    return f"{p.scheme}://{p.hostname}/…" if p.hostname else ""

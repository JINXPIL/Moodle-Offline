# -*- coding: utf-8 -*-
"""
Спортивные элективы СевГУ (часть Moodle Offline).

• Открывает страницу элективов под вашей учётной записью (тот же вход, что и в Moodle: форма или единый аккаунт).
• Сохраняет варианты на неделю для офлайна: снимок страницы (HTML + PDF) и список вариантов.
• Находит на странице кнопки «Записаться» / «Отписаться» вместе с описанием занятия и выполняет запись ТОЛЬКО
  по вашему нажатию (с подтверждением) — ровно то же, что нажатие кнопки на сайте.
• Может раз в N часов проверять страницу и присылать уведомление, когда появились новые варианты.

Автоматическую «ловлю» мест программа сознательно не делает: это нечестно по отношению к другим студентам
и рискованно (можно записаться не туда).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import moodle_offline as mo

ELECTIVES_URL = "https://iot.sevsu.ru/electives/elektivnye-kursy-po-fizicheskoi-kulture-i-sportu"
ELECTIVES_DIR = "Элективы"
ACTION_RX = re.compile(r"запис|отпис|отказ|выбрать|участв|забронир|enrol|enroll|join|signup|sign up|subscribe|register", re.I)
SKIP_RX = re.compile(r"выход|logout|поиск|search|фильтр|войти|вход|login", re.I)
TOKEN_RX = re.compile(r"token|csrf|nonce|state|session|sid", re.I)
FULL_RX = re.compile(r"мест нет|нет мест|мест не осталось|заполнен|запись закрыта|недоступн", re.I)


def signature(context: str) -> str:
    """Описание без меняющихся частей (свободные места, «вы записаны») — чтобы узнавать «новые» варианты."""
    t = re.sub(r"(свободно|осталось|мест)\w*[:\s]*\d+|\d+\s*мест\w*|вы записаны|мест нет|нет мест|заполнен\w*", " ",
               context or "", flags=re.I)
    return re.sub(r"\s+", " ", t).strip().lower()


class PortalError(mo.MoodleError):
    pass


def _redact(html_text: str) -> str:
    def repl(m):
        tag = m.group(0)
        name = re.search(r'name=["\']([^"\']+)', tag)
        if name and TOKEN_RX.search(name.group(1)):
            tag = re.sub(r'(value=["\'])[^"\']*', r"\1REDACTED", tag)
        return tag
    return re.sub(r"<input[^>]*>", repl, html_text or "", flags=re.I)


class Portal:
    """Сайт под учётной записью вуза: вход (форма / единый аккаунт), чтение страниц, отправка форм."""

    def __init__(self, url, rep: mo.Reporter, control: mo.Control, creds=None, cookies=None, use_system_proxy=False,
                 page_delay=mo.PAGE_DELAY):
        p = urlparse(url)
        self.url = url
        self.host = (p.hostname or "").lower()
        self.c = mo.MoodleClient(f"{p.scheme}://{p.netloc}", page_delay=page_delay, wait_minutes=15,
                                 control=control, reporter=rep, use_system_proxy=use_system_proxy)
        self.rep = rep
        self.creds = creds
        self.diag = []
        if cookies:
            self.c.import_cookies(cookies)

    def _needs_login(self, r, soup) -> bool:
        host = (urlparse(r.url).hostname or "").lower()
        if host != self.host:
            return True
        f = self.c._pick_login_form(soup)
        if f is not None and not self.c._is_guest_form(f):
            return True
        return bool(re.search(r"/(login|signin|auth|sso)(/|$|\?|\.)", urlparse(r.url).path, re.I))

    def get(self, url=None):
        url = url or self.url
        r = self.c.request("GET", url, kind="page")
        text = self.c._text(r)
        soup = BeautifulSoup(text, mo.PARSER)
        self.diag.append(f"GET {self.c._safe_url(url)} → {r.status_code} {self.c._safe_url(r.url)} · {self.c._describe_forms(soup)}")
        if self._needs_login(r, soup):
            if not self.creds:
                raise PortalError("сайт просит войти — нужен логин и пароль учётной записи СевГУ")
            self._login(r, text)
            r = self.c.request("GET", url, kind="page")
            text = self.c._text(r)
            soup = BeautifulSoup(text, mo.PARSER)
            self.diag.append(f"GET после входа → {r.status_code} {self.c._safe_url(r.url)}")
            if self._needs_login(r, soup):
                raise PortalError("после входа сайт снова показывает страницу входа")
        r.raise_for_status()
        return r, text, soup

    def _login(self, r, text):
        username, password = self.creds
        c = self.c
        sent = False
        for step in range(1, 11):
            soup = BeautifulSoup(text, mo.PARSER)
            host = (urlparse(r.url).hostname or "").lower()
            self.diag.append(f"  вход {step}: {r.status_code} {c._safe_url(r.url)} · {c._describe_forms(soup)}")
            if sent and not self._needs_login(r, soup):
                return
            form = c._pick_login_form(soup)
            if form is not None and not c._is_guest_form(form) and not sent:
                action = urljoin(r.url, form.get("action") or r.url)
                if urlparse(action).scheme != "https" and urlparse(self.url).scheme == "https":
                    raise PortalError(f"страница входа {host} не защищена (http) — пароль туда не отправляю")
                data = {i["name"]: i.get("value", "") for i in form.select("input[name]")
                        if (i.get("type") or "text").lower() in ("hidden", "submit")}
                ui = (form.select_one("input[name=username], input[name=UserName], input[name=login], input[name=email]")
                      or form.select_one("input[type=text], input[type=email], input:not([type])"))
                pw = form.select_one("input[type=password]")
                data[(ui.get("name") if ui is not None and ui.get("name") else "username")] = username
                data[(pw.get("name") if pw is not None and pw.get("name") else "password")] = password
                self.diag.append(f"    ввожу логин/пароль на {host} (поля {list(data)})")
                r = c.request((form.get("method") or "post").upper(), action, data=data, kind="page", referer=r.url)
                text = c._text(r)
                sent = True
                continue
            auto = next((f for f in soup.find_all("form") if f.select_one(
                "input[name=SAMLResponse], input[name=SAMLRequest], input[name=code], input[name=id_token], "
                "input[name=wresult], input[name=RelayState], input[name=state]")), None)
            if auto is not None:
                data = {i["name"]: i.get("value", "") for i in auto.select("input[name]")}
                r = c.request((auto.get("method") or "post").upper(), urljoin(r.url, auto.get("action") or r.url),
                              data=data, kind="page", referer=r.url)
                text = c._text(r)
                continue
            if not sent:
                idps = c._find_idps(soup, r.url)
                if idps:
                    self.diag.append(f"    кнопка внешнего входа «{idps[0][0]}»")
                    r = c.request("GET", idps[0][1], kind="page", referer=r.url)
                    text = c._text(r)
                    continue
            if sent:
                err = soup.select_one("#input-error, .kc-feedback-text, .alert-danger, .alert-error, .invalid-feedback, "
                                      ".error, [class*=error]")
                msg = mo.clean_text(err.get_text(" ")) if err is not None else ""
                raise PortalError(f"вход не удался{': ' + mo.short(msg, 200) if msg else ''}")
            break
        if not sent:
            raise PortalError("не нашёл, куда вводить логин и пароль на странице входа")

    def submit(self, method, action, data, referer):
        r = self.c.request(method, action, data=data if method == "POST" else None,
                           params=data if method == "GET" else None, kind="page", referer=referer)
        return r, self.c._text(r)


# ════════════════════════════════════════════════════════════════════════════
#  ПОИСК ВАРИАНТОВ И КНОПОК ЗАПИСИ НА СТРАНИЦЕ
# ════════════════════════════════════════════════════════════════════════════
def _context(el, label: str) -> str:
    """Описание варианта: текст ближайшего «карточного» контейнера вокруг кнопки."""
    best = ""
    for p in el.parents:
        if p.name in ("body", "html", "[document]"):
            break
        t = mo.clean_text(p.get_text(" "))
        if len(t) >= 25:
            best = t
            break
    if label:
        best = best.replace(label, " ").strip()
    return mo.short(re.sub(r"\s{2,}", " ", best), 400)


def scan_actions(soup, page_url) -> list[dict]:
    actions, seen = [], set()
    for form in soup.find_all("form"):
        if form.select_one("input[type=password]"):
            continue
        method = (form.get("method") or "get").upper()
        action_url = urljoin(page_url, form.get("action") or page_url)
        base = {}
        for i in form.select("input[name]"):
            t = (i.get("type") or "text").lower()
            if t in ("hidden",) or (t in ("radio", "checkbox") and i.has_attr("checked")):
                base[i["name"]] = i.get("value", "on")
        for s in form.select("select[name]"):
            opt = s.select_one("option[selected]") or s.select_one("option")
            if opt is not None:
                base[s["name"]] = opt.get("value", mo.clean_text(opt.get_text()))
        buttons = form.select("button, input[type=submit]")
        for b in buttons or [None]:
            label = mo.clean_text((b.get("value") if b is not None and b.name == "input" else b.get_text(" ")) if b is not None else "")
            hay = " ".join([label, form.get("action") or "", " ".join(f"{k}={v}" for k, v in base.items())])
            if not ACTION_RX.search(hay) or SKIP_RX.search(label):
                continue
            fields = dict(base)
            if b is not None and b.get("name"):
                fields[b["name"]] = b.get("value", "")
            ctx = _context(form, label)
            aid = hashlib.sha1(f"{ctx}|{label}|{sorted(fields.items())}".encode()).hexdigest()[:12]
            if aid in seen:
                continue
            seen.add(aid)
            actions.append({"id": aid, "kind": "form", "label": label or "Отправить", "method": method,
                            "action": action_url, "fields": fields, "context": ctx,
                            "disabled": b is not None and b.has_attr("disabled"),
                            "leave": bool(re.search(r"отпис|отказ|leave|unenrol|cancel", label, re.I))})
    for el in soup.select("a[href], button"):
        if el.find_parent("form") is not None:
            continue
        label = mo.clean_text(el.get_text(" "))
        full = bool(FULL_RX.search(label))
        if not label or (not ACTION_RX.search(label) and not full) or SKIP_RX.search(label) or len(label) > 60:
            continue
        href = el.get("href") or ""
        kind = "link" if href and not href.startswith(("#", "javascript:")) else "js"
        ctx = _context(el, label)
        aid = hashlib.sha1(f"{ctx}|{label}|{href}".encode()).hexdigest()[:12]
        if aid in seen:
            continue
        seen.add(aid)
        actions.append({"id": aid, "kind": kind, "label": label, "method": "GET",
                        "action": urljoin(page_url, href) if kind == "link" else "", "fields": {}, "context": ctx,
                        "disabled": full or el.has_attr("disabled") or "disabled" in (el.get("class") or []),
                        "leave": bool(re.search(r"отпис|отказ|leave|cancel", label, re.I))})
    return actions


def page_messages(soup) -> list[str]:
    out = []
    for el in soup.select(".alert, .notification, .toast, .message, .messages li, [role=alert], .flash, .notice"):
        t = mo.clean_text(el.get_text(" "))
        if t and t not in out:
            out.append(mo.short(t, 200))
    return out[:5]


# ════════════════════════════════════════════════════════════════════════════
#  ОПЕРАЦИИ (используются окном программы и ночной синхронизацией)
# ════════════════════════════════════════════════════════════════════════════
class Electives:
    def __init__(self, out_dir: Path, data_dir: Path, rep: mo.Reporter, control: mo.Control, url=None, creds=None,
                 cookies=None, use_system_proxy=False, pdf=True, page_delay=mo.PAGE_DELAY):
        self.out = Path(out_dir) / ELECTIVES_DIR
        self.data_dir = Path(data_dir)
        self.url = url or ELECTIVES_URL
        self.rep = rep
        self.pdf = pdf
        self.portal = Portal(self.url, rep, control, creds=creds, cookies=cookies,
                             use_system_proxy=use_system_proxy, page_delay=page_delay)

    @property
    def state_path(self):
        return self.out / "элективы.json"

    def load(self) -> dict:
        try:
            with mo.fopen(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_debug(self, text):
        try:
            mo.fmkdir(self.data_dir)
            mo.write_text_atomic(self.data_dir / "electives_debug.txt", "\n".join(self.portal.diag) + "\n")
            if text:
                mo.write_text_atomic(self.data_dir / "electives_page.html", _redact(text))
        except OSError:
            pass

    def refresh(self) -> dict:
        """Открывает страницу, сохраняет снимок и список вариантов. → состояние + новые варианты."""
        text = ""
        try:
            r, text, soup = self.portal.get()
        except Exception:
            self._save_debug(text)
            raise
        actions = scan_actions(soup, r.url)
        title = mo.clean_text(soup.title.get_text()) if soup.title else "Элективы"
        mo.fmkdir(self.out)
        from schedule import _with_base
        mo.write_text_atomic(self.out / "страница.html", _with_base(text, r.url))
        pdf_ok = False
        if self.pdf:
            browser = mo.find_browser()
            if browser:
                # PDF печатаем из сохранённой копии: живая страница требует входа, а у фонового браузера своих cookie нет
                pdf_ok = mo.html_to_pdf(browser, self.out / "страница.html", self.out / "страница.pdf")
        old = self.load()
        old_sig = {signature(a["context"]) for a in old.get("actions", [])}
        new = [a for a in actions if signature(a["context"]) not in old_sig and not a["leave"]] if old else []
        state = {"url": self.url, "final_url": r.url, "title": title, "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
                 "actions": actions, "messages": page_messages(soup), "pdf": pdf_ok,
                 "text": mo.short(" ".join(mo.clean_text(soup.get_text(" ")).split()), 6000)}
        mo.write_text_atomic(self.state_path, json.dumps(state, ensure_ascii=False, indent=1))
        if not actions:
            self._save_debug(text)
            self.rep.log("Кнопки записи на странице не найдены — сохранил снимок и диагностику (electives_page.html).", "warn")
        self.rep.log(f"Элективы: вариантов с кнопкой записи — {len(actions)}" + (f", новых — {len(new)}" if new else ""))
        state["new"] = new
        return state

    def act(self, action_id: str) -> dict:
        """Нажимает выбранную кнопку (с актуальными скрытыми полями страницы — токены одноразовые)."""
        r, text, soup = self.portal.get()
        fresh = {a["id"]: a for a in scan_actions(soup, r.url)}
        old = {a["id"]: a for a in self.load().get("actions", [])}
        a = fresh.get(action_id)
        if a is None and action_id in old:   # поля могли поменяться (новый токен) — ищем по описанию и надписи
            o = old[action_id]
            a = next((x for x in fresh.values() if x["context"] == o["context"] and x["label"] == o["label"]), None)
        if a is None:
            raise PortalError("этого варианта уже нет на странице — обновите список")
        if a["disabled"]:
            raise PortalError("кнопка на сайте неактивна (мест нет или запись закрыта)")
        if a["kind"] == "js":
            raise PortalError("эта кнопка работает через JavaScript сайта — нажмите её на сайте")
        self.rep.log(f"Элективы: «{a['label']}» — {mo.short(a['context'], 120)}")
        r2, text2 = self.portal.submit(a["method"] if a["kind"] == "form" else "GET", a["action"], a["fields"], r.url)
        soup2 = BeautifulSoup(text2, mo.PARSER)
        msgs = page_messages(soup2)
        ok = r2.status_code < 400
        state = self.refresh()
        return {"ok": ok, "status": r2.status_code, "messages": msgs, "state": state}

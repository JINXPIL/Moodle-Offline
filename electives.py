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
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import moodle_offline as mo

ELECTIVES_URL = "https://iot.sevsu.ru/electives/elektivnye-kursy-po-fizicheskoi-kulture-i-sportu"
ELECTIVES_DIR = "Элективы"
ACTION_RX = re.compile(r"запис|отпис|отказ|выбрат|участв|бронир|заявк|подать|регистр|enrol|enroll|join|signup|sign up|"
                       r"subscribe|register", re.I)
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

    def cookies(self) -> list:
        """Cookie всех сайтов вуза из этой сессии (включая единый вход auth.*) — чтобы в следующий раз без пароля."""
        site = ".".join(self.host.split(".")[-2:])
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": bool(c.secure)}
                for c in self.c.s.cookies if (c.domain or "").lstrip(".").endswith(site)]

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
                if form.select_one("input[name=rememberMe]") is not None:
                    data["rememberMe"] = "on"     # единый вход СевГУ: «Запомнить меня» — реже вводить пароль
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
            dom = b.get("data-mo-idx") if b is not None else None
            if aid in seen:
                continue
            seen.add(aid)
            actions.append({"id": aid, "kind": "form", "label": label or "Отправить", "method": method,
                            "action": action_url, "fields": fields, "context": ctx, "dom": dom,
                            "disabled": b is not None and b.has_attr("disabled"),
                            "leave": bool(re.search(r"отпис|отказ|leave|unenrol|cancel", label, re.I))})
    clickable = "a[href], button, input[type=button], [role=button], .btn, .v-btn, .ant-btn, .el-button, [data-mo-click]"
    for el in soup.select(clickable):
        if el.find_parent("form") is not None or el.find_parent(["a", "button"]) is not None \
                or (el.has_attr("data-mo-click") and el.find_parent(attrs={"data-mo-click": True}) is not None):
            continue
        if el.name == "input":
            el.string = el.get("value", "")
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
        cls = " ".join(el.get("class") or [])
        actions.append({"id": aid, "kind": kind, "label": label, "method": "GET",
                        "action": urljoin(page_url, href) if kind == "link" else "", "fields": {}, "context": ctx,
                        "dom": el.get("data-mo-idx"),
                        "disabled": full or el.has_attr("disabled") or el.get("aria-disabled") == "true"
                        or bool(re.search(r"(^|\s|-)disabled(\s|$)", cls)),
                        "leave": bool(re.search(r"отпис|отказ|leave|cancel", label, re.I))})
    return actions


JS_HAS_ACTION = (r"/запис|отпис|мест нет|нет мест|выбрат|бронир|заявк/i.test(document.body ? document.body.innerText : '')"
                 r" && !/^\s*(загрузка|loading)/i.test(document.body.innerText)")


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
    """Страница элективов iot.sevsu.ru — это веб-приложение: его кнопки рисует скрипт после входа через единый
    аккаунт СевГУ (auth.sevsu.ru). Поэтому страница открывается в фоновом Edge/Chrome (как у вас в браузере):
    вход — один раз, дальше по сохранённым cookie; запись — нажатием той же кнопки, что и на сайте."""

    def __init__(self, out_dir: Path, data_dir: Path, rep: mo.Reporter, control: mo.Control, url=None, creds=None,
                 cookies=None, use_system_proxy=False, pdf=True, page_delay=mo.PAGE_DELAY, browser=True):
        self.out = Path(out_dir) / ELECTIVES_DIR
        self.data_dir = Path(data_dir)
        self.url = url or ELECTIVES_URL
        self.rep = rep
        self.pdf = pdf
        self.creds = creds
        self.cookies = cookies or []
        self.cookies_out = None
        self.browser = mo.find_browser() if browser else None
        self.fallback_reason = ""   # почему фоновый браузер не открыл страницу (чтобы сказать об этом прямо)
        self.diag = []
        self.use_system_proxy = use_system_proxy
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
            mo.write_text_atomic(self.data_dir / "electives_debug.txt", "\n".join(self.portal.diag + self.diag) + "\n")
            if text:
                mo.write_text_atomic(self.data_dir / "electives_page.html", _redact(text))
        except OSError:
            pass

    def _why_empty(self, soup, actions, how) -> list[str]:
        """Сообщения со страницы + прямая причина, если страница пуста из-за того, что её не открыл фоновый браузер."""
        msgs = page_messages(soup)
        if how == "http" and not actions:
            why = self.fallback_reason or ("не найден Microsoft Edge или Google Chrome" if not self.browser else "")
            if why:
                msgs = [f"Страница элективов строится скриптом сайта, а фоновый браузер её не открыл: {why}."] + msgs
        return msgs

    def _state(self, text, soup, final_url, actions, pdf_ok, how):
        title = mo.clean_text(soup.title.get_text()) if soup.title else "Элективы"
        old = self.load()
        old_sig = {signature(a["context"]) for a in old.get("actions", [])}
        new = [a for a in actions if signature(a["context"]) not in old_sig and not a["leave"]] if old else []
        state = {"url": self.url, "final_url": final_url, "title": title, "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
                 "actions": actions, "messages": self._why_empty(soup, actions, how), "pdf": pdf_ok, "how": how,
                 "text": mo.short(" ".join(mo.clean_text(soup.get_text(" ")).split()), 6000)}
        mo.write_text_atomic(self.state_path, json.dumps(state, ensure_ascii=False, indent=1))
        if not actions:
            self._save_debug(text)
            self.rep.log("Кнопки записи на странице не найдены — сохранил снимок и диагностику "
                         f"({self.data_dir / 'electives_page.html'}, без паролей).", "warn")
        self.rep.log(f"Элективы: вариантов с кнопкой записи — {len(actions)}" + (f", новых — {len(new)}" if new else ""))
        state["new"] = new
        return state

    def _capture(self, br, _depth=0) -> dict:
        """Снимок открытой в фоновом браузере страницы: HTML, PDF «как на сайте», список вариантов."""
        br.annotate()
        text, final = br.html(), br.url()
        soup = BeautifulSoup(text, mo.PARSER)
        actions = scan_actions(soup, final)
        if not actions:
            info = br.page_info()
            self.diag.append(f"страница: {info}")
            frames = [u for u in info.get("iframes", []) if (urlparse(u).hostname or "").endswith(
                ".".join((urlparse(self.url).hostname or "").split(".")[-2:]))]
            if frames and _depth == 0:   # варианты внутри встроенного окна — открываем его напрямую
                self.diag.append(f"открываю встроенное окно {frames[0]}")
                br.goto(frames[0], settle=8)
                br.wait_for(JS_HAS_ACTION, 15)
                return self._capture(br, 1)
            try:
                mo.fmkdir(self.data_dir)
                br.screenshot(self.data_dir / "electives_screen.png")
                mo.write_text_atomic(self.data_dir / "electives_text.txt", br.text())
            except OSError:
                pass
        mo.fmkdir(self.out)
        from schedule import _with_base
        mo.write_text_atomic(self.out / "страница.html", _with_base(re.sub(r'\sdata-mo-idx="\d+"', "", text), final))
        pdf_ok = br.pdf(self.out / "страница.pdf") if self.pdf else False
        return self._state(text, soup, final, actions, pdf_ok, "browser")

    def _open(self):
        import cdp
        self.diag = []
        try:
            br = cdp.open_authed(self.browser, self.url, self.creds, self.cookies, rep=self.rep, diag=self.diag, settle=12,
                                     direct=not self.use_system_proxy)
        except cdp.CDPLoginError as e:
            self._save_debug("")
            raise PortalError(str(e))
        self.cookies_out = br.cookies()
        if (urlparse(br.url()).hostname or "") != (urlparse(self.url).hostname or ""):
            self.diag.append(f"остался на {urlparse(br.url()).hostname}")
        if not br.wait_for(JS_HAS_ACTION, 20):   # веб-приложение подгружает варианты не сразу
            self.diag.append("за 20 с на странице не появилось надписей о записи")
        br.settle(4, quiet=1.0)
        return br

    def refresh(self) -> dict:
        """Открывает страницу, сохраняет снимок и список вариантов. → состояние + новые варианты."""
        if self.browser:
            import cdp
            try:
                br = self._open()
                with br:
                    return self._capture(br)
            except PortalError:
                raise
            except cdp.CDPError as e:
                self.fallback_reason = str(e) or type(e).__name__
                self.diag.append(f"фоновый браузер: {self.fallback_reason}")
                self.rep.log(f"Фоновый браузер не справился ({e}) — пробую обычным запросом", "warn")
        return self._refresh_http()

    def _refresh_http(self) -> dict:
        text = ""
        try:
            r, text, soup = self.portal.get()
        except Exception:
            self._save_debug(text)
            raise
        actions = scan_actions(soup, r.url)
        mo.fmkdir(self.out)
        from schedule import _with_base
        mo.write_text_atomic(self.out / "страница.html", _with_base(text, r.url))
        pdf_ok = False
        if self.pdf and self.browser:
            pdf_ok = mo.html_to_pdf(self.browser, self.out / "страница.html", self.out / "страница.pdf")
        if not actions and len(mo.clean_text(soup.get_text(" "))) < 400:
            self.rep.log("Страница элективов строится скриптом — для неё нужен Edge или Chrome (на Windows Edge есть всегда).", "warn")
        self.cookies_out = self.portal.cookies()
        return self._state(text, soup, r.url, actions, pdf_ok, "http")

    @staticmethod
    def _find(fresh: dict, old: dict, action_id: str):
        a = fresh.get(action_id)
        if a is None and action_id in old:   # поля могли поменяться (новый токен) — ищем по описанию и надписи
            o = old[action_id]
            a = next((x for x in fresh.values() if x["context"] == o["context"] and x["label"] == o["label"]), None)
        if a is None:
            raise PortalError("этого варианта уже нет на странице — обновите список")
        if a["disabled"]:
            raise PortalError("кнопка на сайте неактивна (мест нет или запись закрыта)")
        return a

    def act(self, action_id: str) -> dict:
        """Нажимает выбранную кнопку — после вашего подтверждения в программе."""
        old = {a["id"]: a for a in self.load().get("actions", [])}
        if self.browser:
            import cdp
            try:
                br = self._open()
            except cdp.CDPError as e:
                raise PortalError(f"фоновый браузер не открылся: {e}")
            with br:
                br.annotate()
                soup = BeautifulSoup(br.html(), mo.PARSER)
                a = self._find({x["id"]: x for x in scan_actions(soup, br.url())}, old, action_id)
                self.rep.log(f"Элективы: «{a['label']}» — {mo.short(a['context'], 120)}")
                before = set(br.text().splitlines())
                if a.get("dom") is None or not br.click_idx(a["dom"]):
                    raise PortalError("не удалось нажать кнопку на странице — обновите список и попробуйте ещё раз")
                time.sleep(1.5)
                br.wait_loaded(15)
                br.settle(6, quiet=1.0)
                conf = br.confirm_dialog()
                if conf:
                    self.rep.log(f"  сайт спросил подтверждение — нажимаю «{conf}»")
                    time.sleep(1.5)
                    br.wait_loaded(15)
                    br.settle(6, quiet=1.0)
                msgs = page_messages(BeautifulSoup(br.html(), mo.PARSER))
                if not msgs:   # всплывающие уведомления: новые короткие строки на странице
                    msgs = [mo.short(x.strip(), 200) for x in br.text().splitlines()
                            if x.strip() and x not in before and 8 < len(x.strip()) < 200][:3]
                state = self._capture(br)
            return {"ok": True, "status": 200, "messages": msgs, "state": state}
        r, text, soup = self.portal.get()
        a = self._find({x["id"]: x for x in scan_actions(soup, r.url)}, old, action_id)
        if a["kind"] == "js":
            raise PortalError("эта кнопка работает через скрипт сайта — нужен Edge/Chrome, или нажмите её на сайте")
        self.rep.log(f"Элективы: «{a['label']}» — {mo.short(a['context'], 120)}")
        r2, text2 = self.portal.submit(a["method"] if a["kind"] == "form" else "GET", a["action"], a["fields"], r.url)
        msgs = page_messages(BeautifulSoup(text2, mo.PARSER))
        ok = r2.status_code < 400
        state = self._refresh_http()
        return {"ok": ok, "status": r2.status_code, "messages": msgs, "state": state}

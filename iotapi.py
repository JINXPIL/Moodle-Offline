# -*- coding: utf-8 -*-
"""
Элективы СевГУ через API сайта iot.sevsu.ru (часть Moodle Offline).

Страница iot.sevsu.ru — это веб-приложение (Vue): форм и кнопок в её HTML нет, всё рисует скрипт и берёт данные
у программного интерфейса https://iot.sevsu.ru/api. Поэтому здесь программа делает то же, что сам сайт:
  1) входит через единый аккаунт СевГУ (Keycloak: auth.sevsu.ru, область portal, клиент iot) и получает ключ доступа;
  2) читает блоки выбора (/electives), варианты с местами, ваш выбор (/choices), занятия (/calendar-events);
  3) по вашему нажатию сохраняет выбор «основной вариант + резервы по приоритету» (POST /choices)
     и записывает на занятие или снимает запись (/calendar-event-registrations).

Данные кладутся в «Элективы/iot.json» в папке сохранения: без интернета и на телефоне (после переноса) они читаются как есть.

Что здесь сознательно НЕ делается: автоматическая «ловля» мест, любые записи и выборы без вашего нажатия.
Пароль уходит только на страницу входа сайта вуза (https, *.sevsu.ru). Ключ доступа живёт только в памяти и в журнал не пишется.
Только стандартная библиотека и то, что уже нужно программе (requests, bs4 — через moodle_offline).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

import moodle_offline as mo

SITE = "https://iot.sevsu.ru"
AUTH = "https://auth.sevsu.ru"
REALM, CLIENT_ID = "portal", "iot"
ELECTIVES_DIR = "Элективы"
STATE_FILE = "iot.json"
TRUSTED_SUFFIX = ".sevsu.ru"
INSECURE_TEST = False          # только для проверок на своём компьютере: разрешает http и свои имена сайтов (см. tests)
EVENT_DAYS = 14                # на сколько дней вперёд показывать занятия
STATUS_TEXT = {"choose": "Доступен выбор и его изменение", "done": "Выбор завершён", "closed": "Выбор завершён",
               "wait": "Выбор ещё не открыт", "info": "Выбор недоступен"}
ATTENDANCE_TEXT = {"present": "Присутствовал", "attended": "Присутствовал", "absent": "Отсутствовал", "excused": "Уважительная причина"}
REG_STATUS_TEXT = {"registered": "Записан", "retracted": "Отписан", "cancelled": "Отменена", "canceled": "Отменена"}


class IotError(mo.MoodleError):
    pass


class IotNeedLogin(IotError):
    """Нужен пароль: единый вход не помнит вас, а сохранённого пароля нет."""


class IotHttpError(IotError):
    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status = status


# ════════════════════════════════════════════════════════════════════════════
#  МЕЛКИЕ ПОМОЩНИКИ
# ════════════════════════════════════════════════════════════════════════════
def _dt(v):
    """Строка даты из API → aware datetime (UTC). Пусто или мусор → None."""
    if not v or not isinstance(v, str):
        return None
    t = v.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def _loc(d):
    """Для показа: местное время устройства."""
    return d.astimezone() if d else None


def fmt_dt(d) -> str:
    d = _loc(d)
    return d.strftime("%d.%m.%Y %H:%M") if d else ""


def fmt_range(a, b) -> str:
    a, b = _loc(a), _loc(b)
    if not a:
        return ""
    s = a.strftime("%d.%m %H:%M")
    return s + (b.strftime("–%H:%M") if b and b.date() == a.date() else (b.strftime(" – %d.%m %H:%M") if b else ""))


def _text(html_or_text) -> str:
    if not html_or_text:
        return ""
    s = str(html_or_text)
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, mo.PARSER).get_text(" ")
    return mo.clean_text(s)


def _same(a, b) -> bool:
    return str(a) == str(b)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _trusted_host(host: str) -> bool:
    host = (host or "").lower()
    return host.endswith(TRUSTED_SUFFIX) or host == "sevsu.ru" or (INSECURE_TEST and host.endswith(".sevsu.test"))


def _trusted(url: str) -> bool:
    """Пароль можно отправлять только на https-страницы сайтов вуза."""
    p = urlparse(url)
    return (p.scheme == "https" or INSECURE_TEST) and _trusted_host(p.hostname or "")


def _error_text(r) -> str:
    try:
        j = r.json()
    except ValueError:
        return mo.short(mo.clean_text(r.text or ""), 200) or f"ошибка {r.status_code}"
    if not isinstance(j, dict):
        return f"ошибка {r.status_code}"
    msg = str(j.get("message") or "")
    errs = j.get("errors")
    if isinstance(errs, dict):
        extra = " ".join(" ".join(map(str, v)) if isinstance(v, list) else str(v) for v in errs.values())
        msg = (msg + " " + extra).strip()
    return mo.short(msg or f"ошибка {r.status_code}", 300)


# ════════════════════════════════════════════════════════════════════════════
#  РАЗБОР ДАННЫХ САЙТА → КОМПАКТНОЕ СОСТОЯНИЕ
# ════════════════════════════════════════════════════════════════════════════
def norm_option(o: dict) -> dict:
    cap, occ = o.get("capacity"), o.get("occupied")
    left = max(0, cap - (occ or 0)) if isinstance(cap, (int, float)) else None
    return {"id": o.get("id"), "name": o.get("fullName") or o.get("shortName") or "", "short": o.get("shortName") or "",
            "capacity": cap, "occupied": occ, "left": left,
            "filling": o.get("isFillingAllowed"), "backup_ok": o.get("isBackupAllowed"), "parent": o.get("parentId"),
            "requisites_ok": o.get("requisitesSatisfied"), "min_sub": o.get("subOptionsMinimum"),
            "max_sub": o.get("subOptionsMaximum"), "url": o.get("detailsUrl") or "",
            "desc": mo.short(_text(o.get("description")), 700)}


def block_status(e: dict, now: datetime) -> tuple:
    """Статус блока в тех же словах, что и на сайте: «Доступен выбор и его изменение» / «Выбор завершён»."""
    f = e.get("flow") or {}
    waves = sorted(x for x in (_dt(w.get("openedAt")) for w in f.get("choiceWaves") or []) if x)
    closed = _dt(f.get("choiceClosedAt"))
    if e.get("canChoose"):
        return "choose", STATUS_TEXT["choose"]
    if e.get("outcome"):
        return "done", STATUS_TEXT["done"]
    if waves and now < waves[0]:
        return "wait", f"Выбор откроется {fmt_dt(waves[0])}"
    if closed and now > closed:
        return "closed", STATUS_TEXT["closed"]
    return "info", STATUS_TEXT["info"]


def norm_block(e: dict, now: datetime, opt_names: dict, detail: dict | None = None, choice: dict | None = None) -> dict:
    f = (detail or e).get("flow") or e.get("flow") or {}
    status, status_text = block_status(detail or e, now)
    d = detail or {}
    opts = [norm_option(o) for o in ((d.get("_embedded") or {}).get("options") or [])]
    byid = {str(o["id"]): o for o in opts}
    for o in opts:
        opt_names[str(o["id"])] = o["name"]
    prim = f.get("primaryOptions")
    backup = f.get("backupOptions")
    kids = {str(o["parent"]) for o in opts if o["parent"] is not None}
    block = {
        "id": e.get("id"), "slug": e.get("slug") or "", "name": e.get("name") or "", "type": e.get("type") or "",
        "year": e.get("academicYear"), "term": e.get("academicTerm"),
        "status": status, "status_text": status_text,
        "can_choose": bool(e.get("canChoose")), "is_member": bool(e.get("isMember")),
        "primary_count": prim if isinstance(prim, int) else 1,
        "backup_count": backup if isinstance(backup, int) else 0,
        "opens": fmt_dt(min((x for x in (_dt(w.get("openedAt")) for w in f.get("choiceWaves") or []) if x), default=None)),
        "fixed_at": fmt_dt(_dt(f.get("choiceFixedAt"))), "closes": fmt_dt(_dt(f.get("choiceClosedAt"))),
        "final_at": fmt_dt(_dt(f.get("finalizedAt"))),
        "announcement": mo.short(_text(e.get("announcement") or d.get("announcement")), 1500),
        "outcome": [{"id": x.get("optionId"), "name": opt_names.get(str(x.get("optionId")), "")} for x in (e.get("outcome") or [])],
        "options": opts, "has_detail": detail is not None,
        "needs_site": any(str(o["id"]) in kids and (o["min_sub"] or 0) > 0 for o in opts),
    }
    if choice is not None:
        def pick(lst):
            return [{"id": x.get("optionId"), "name": (byid.get(str(x.get("optionId"))) or {}).get("name")
                     or opt_names.get(str(x.get("optionId")), ""), "sub": x.get("subOptionIds") or []} for x in lst or []]
        block["choice"] = {"id": choice.get("id"), "status": choice.get("status"), "primary": pick(choice.get("primaryOptions")),
                           "backup": pick(choice.get("backupOptions"))}
    return block


def norm_event(ev: dict) -> dict:
    emb = ev.get("_embedded") or {}
    reg = emb.get("registration") or {}
    loc = emb.get("location") or {}
    start, end = _dt(ev.get("startAt")), _dt(ev.get("endAt"))
    cap, occ = ev.get("capacity"), ev.get("occupied") or 0
    people = [" ".join(x for x in (p.get("lastName"), p.get("firstName"), p.get("middleName")) if x)
              for p in emb.get("organizers") or []]
    b = ((loc.get("_embedded") or {}).get("building") or {})
    where = " ".join(x for x in (loc.get("fullName") or loc.get("shortName"), b.get("fullName") or b.get("shortName")) if x)
    st, att = reg.get("status"), reg.get("attendance")
    return {"id": ev.get("id"), "name": ev.get("name") or "", "start": _iso(start), "end": _iso(end),
            "when": fmt_range(start, end), "elective_id": ev.get("electiveId"), "option_id": ev.get("optionId"),
            "capacity": cap, "left": max(0, cap - occ) if isinstance(cap, (int, float)) else None,
            "can_register": bool(ev.get("canRegister")), "cancelled": ev.get("status") == "cancelled",
            "cancel_reason": ev.get("cancellationReason") or "", "where": where, "people": [p for p in people if p],
            "reg_id": reg.get("id"), "reg_status": st, "reg_text": REG_STATUS_TEXT.get(st, st or ""),
            "attendance": att, "attendance_text": ATTENDANCE_TEXT.get(att, att or "")}


def diff_states(old: dict, new: dict) -> list:
    """Что изменилось с прошлого раза (для уведомления): новые блоки, открывшийся выбор, распределение, новые занятия."""
    if not old or not old.get("blocks"):
        return []
    out = []
    ob = {str(b["id"]): b for b in old["blocks"]}
    for b in new.get("blocks", []):
        o = ob.get(str(b["id"]))
        if o is None:
            out.append(f"Новый блок выбора: {b['name']}")
            continue
        if b["can_choose"] and not o.get("can_choose"):
            out.append(f"Открыт выбор: {b['name']}")
        if [x["id"] for x in b["outcome"]] != [x["id"] for x in o.get("outcome", [])] and b["outcome"]:
            out.append(f"Распределение в «{b['name']}»: " + ", ".join(x["name"] or str(x["id"]) for x in b["outcome"]))
    if old.get("events") is not None:
        seen = {str(x["id"]) for x in old.get("events", [])}
        fresh = [x for x in new.get("events", []) if str(x["id"]) not in seen and x["can_register"] and not x["cancelled"]]
        if fresh:
            out.append(f"Новых занятий для записи: {len(fresh)}")
    return out


def check_choice(block: dict, primary: list, backup: list) -> str:
    """То же правило, что у кнопки «Сохранить» на сайте (canBeSaved): число основных = flow.primaryOptions, число резервов =
    flow.backupOptions (или у какого-то основного варианта резервы не разрешены). Плюс очевидное: без повторов и только
    варианты этого блока. Места и условия здесь НЕ проверяются: что принять, решает сайт, и его ответ показывается как есть.
    Пустая строка — можно сохранять, иначе — что не так, по-русски."""
    if not block.get("can_choose"):
        return "в этом блоке сейчас нельзя выбирать"
    if block.get("needs_site"):
        return "в этом блоке нужно выбрать подварианты — сделайте выбор на сайте"
    byid = {str(o["id"]): o for o in block.get("options", [])}
    if len(primary) != block["primary_count"]:
        return f"нужно выбрать основных вариантов: {block['primary_count']}"
    ids = [str(x) for x in primary + backup]
    if len(set(ids)) != len(ids):
        return "один и тот же вариант нельзя выбрать дважды"
    if any(i not in byid for i in ids):
        return "такого варианта нет в блоке — обновите список"
    if len(backup) != block["backup_count"] and not any(byid[str(x)]["backup_ok"] is False for x in primary):
        return f"нужно выбрать резервных вариантов: {block['backup_count']}"
    return ""


# ════════════════════════════════════════════════════════════════════════════
#  КЛИЕНТ: ВХОД ЧЕРЕЗ ЕДИНЫЙ АККАУНТ И ЗАПРОСЫ К API
# ════════════════════════════════════════════════════════════════════════════
class Iot:
    def __init__(self, out_dir: Path, data_dir: Path, rep: mo.Reporter, control: mo.Control, creds=None, cookies=None,
                 use_system_proxy=False, page_delay=mo.PAGE_DELAY, file_delay=mo.FILE_DELAY):
        self.out = Path(out_dir) / ELECTIVES_DIR
        self.data_dir = Path(data_dir)
        self.rep = rep
        self.creds = creds
        self.cookies_out = None
        self.diag: list = []
        self.token = None
        self.token_until = 0.0
        p = urlparse(SITE)
        self.c = mo.MoodleClient(f"{p.scheme}://{p.netloc}", page_delay=page_delay, file_delay=file_delay, wait_minutes=15,
                                 control=control, reporter=rep, use_system_proxy=use_system_proxy)
        if cookies:
            self.c.import_cookies(cookies)

    # ── файлы ───────────────────────────────────────────────────────────────
    @property
    def state_path(self) -> Path:
        return self.out / STATE_FILE

    def load(self) -> dict:
        try:
            with mo.fopen(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_debug(self):
        try:
            mo.fmkdir(self.data_dir)
            mo.write_text_atomic(self.data_dir / "iot_debug.txt", "\n".join(self.diag) + "\n")
        except OSError:
            pass

    def cookies(self) -> list:
        """Cookie сайтов вуза из этой сессии (единый вход auth.*) — чтобы в следующий раз без пароля."""
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": bool(c.secure)}
                for c in self.c.s.cookies if _trusted_host((c.domain or "").lstrip("."))]

    # ── вход: Keycloak, «стандартный поток» с кодом, как у самого сайта ──────
    def _auth_url(self, redirect, state, nonce, challenge) -> str:
        q = {"client_id": CLIENT_ID, "redirect_uri": redirect, "state": state, "response_mode": "fragment",
             "response_type": "code", "scope": "openid", "nonce": nonce, "code_challenge": challenge,
             "code_challenge_method": "S256"}
        return f"{AUTH}/realms/{REALM}/protocol/openid-connect/auth?" + urlencode(q)

    @staticmethod
    def _redirect_result(url: str, redirect: str, state: str):
        """Адрес — это наш обратный адрес? → ('code', код) | ('error', текст) | None, если нет."""
        u, r = urlparse(url), urlparse(redirect)
        if (u.hostname or "").lower() != (r.hostname or "").lower() or (u.path or "/") != (r.path or "/"):
            return None
        q = {k: v[0] for k, v in parse_qs(u.fragment, keep_blank_values=True).items()}
        q.update({k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()})
        if q.get("error"):
            return "error", f"{q['error']}: {q.get('error_description', '')}".strip(": ")
        if q.get("state") not in (None, state):
            return "error", "ответ сайта не на наш запрос (state)"
        return ("code", q["code"]) if q.get("code") else None

    def _authorize(self, redirect: str) -> tuple:
        """Идём по цепочке переходов вручную: код приходит в адресе, на который сайт нас перенаправляет."""
        c = self.c
        verifier, challenge = _pkce()
        state, nonce = str(uuid.uuid4()), str(uuid.uuid4())
        r = c.request("GET", self._auth_url(redirect, state, nonce, challenge), kind="page", allow_redirects=False)
        sent = False
        for step in range(1, 13):
            loc = r.headers.get("Location") if 300 <= r.status_code < 400 else None
            self.diag.append(f"  вход {step}: {r.status_code} {c._safe_url(r.url)}" + (f" → {c._safe_url(urljoin(r.url, loc))}" if loc else ""))
            if loc:
                nxt = urljoin(r.url, loc)
                res = self._redirect_result(nxt, redirect, state)
                if res:
                    if res[0] == "error":
                        raise IotError(f"сайт входа ответил ошибкой: {res[1]}")
                    return res[1], verifier
                if not _trusted(nxt) and not nxt.startswith(AUTH) and not nxt.startswith(SITE):
                    raise IotError(f"вход ведёт на сторонний сайт {urlparse(nxt).hostname} — такой вход программа не поддерживает")
                r = c.request("GET", nxt, kind="page", allow_redirects=False, referer=r.url)
                continue
            text = c._text(r)
            soup = BeautifulSoup(text, mo.PARSER)
            self.diag.append(f"    {c._describe_forms(soup)}")
            if r.status_code == 400 and re.search(r"redirect_uri", text, re.I):
                raise IotRedirectRejected()
            form = c._pick_login_form(soup)
            if form is not None and not c._is_guest_form(form) and not sent:
                if not self.creds:
                    raise IotNeedLogin("нужен логин и пароль учётной записи СевГУ")
                action = urljoin(r.url, form.get("action") or r.url)
                if not _trusted(action):
                    raise IotError(f"страница входа {urlparse(action).hostname} не защищена или не принадлежит вузу — пароль туда не отправляю")
                data = {i["name"]: i.get("value", "") for i in form.select("input[name]")
                        if (i.get("type") or "text").lower() in ("hidden", "submit")}
                ui = (form.select_one("input[name=username], input[name=login], input[name=email]")
                      or form.select_one("input[type=text], input[type=email], input:not([type])"))
                pw = form.select_one("input[type=password]")
                data[(ui.get("name") if ui is not None and ui.get("name") else "username")] = self.creds[0]
                data[(pw.get("name") if pw is not None and pw.get("name") else "password")] = self.creds[1]
                if form.select_one("input[name=rememberMe]") is not None:
                    data["rememberMe"] = "on"
                self.diag.append(f"    ввожу логин и пароль на {urlparse(action).hostname} (поля {list(data)})")
                r = c.request((form.get("method") or "post").upper(), action, data=data, kind="page", referer=r.url,
                              allow_redirects=False)
                sent = True
                continue
            auto = next((f for f in soup.find_all("form") if f.select_one(
                "input[name=SAMLResponse], input[name=SAMLRequest], input[name=code], input[name=id_token], "
                "input[name=RelayState], input[name=state]")), None)
            if auto is not None:
                data = {i["name"]: i.get("value", "") for i in auto.select("input[name]")}
                r = c.request((auto.get("method") or "post").upper(), urljoin(r.url, auto.get("action") or r.url), data=data,
                              kind="page", referer=r.url, allow_redirects=False)
                continue
            if not sent:
                idps = c._find_idps(soup, r.url)
                if idps:
                    self.diag.append(f"    кнопка внешнего входа «{idps[0][0]}»")
                    r = c.request("GET", idps[0][1], kind="page", referer=r.url, allow_redirects=False)
                    continue
            if sent:
                err = soup.select_one("#input-error, .kc-feedback-text, .alert-danger, .alert-error, .invalid-feedback, "
                                      ".error, [class*=error]")
                msg = mo.clean_text(err.get_text(" ")) if err is not None else ""
                raise IotError(f"вход не удался{': ' + mo.short(msg, 200) if msg else ''}")
            raise IotNeedLogin("нужен логин и пароль учётной записи СевГУ") if not self.creds else IotError(
                "не нашёл, куда вводить логин и пароль на странице входа")
        raise IotError("вход не закончился за 12 переходов — что-то зациклилось")

    def login(self):
        """Ключ доступа (Bearer). Если единый вход помнит вас по cookie — без пароля."""
        if self.token and time.monotonic() < self.token_until:
            return
        redirects = [SITE + "/", SITE + "/silent-check-sso.html"]
        last = None
        for redirect in redirects:
            try:
                code, verifier = self._authorize(redirect)
                break
            except IotRedirectRejected as e:
                last = e
                self.diag.append(f"  сайт входа не принял обратный адрес {redirect}")
        else:
            raise IotError("сайт входа не принял наш запрос (redirect_uri)") from last
        r = self.c.request("POST", f"{AUTH}/realms/{REALM}/protocol/openid-connect/token", kind="ajax",
                           data={"grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code,
                                 "redirect_uri": redirect, "code_verifier": verifier}, headers={"Origin": SITE})
        if r.status_code >= 400:
            self.diag.append(f"  обмен кода на ключ: {r.status_code}")
            raise IotError(f"сайт входа не выдал ключ доступа ({r.status_code}): {_error_text(r)}")
        try:
            j = r.json()
            self.token = j["access_token"]
        except (ValueError, KeyError) as e:
            raise IotError("сайт входа вернул непонятный ответ вместо ключа доступа") from e
        self.token_until = time.monotonic() + max(60, int(j.get("expires_in") or 300) - 30)
        self.cookies_out = self.cookies()
        self.diag.append("  ключ доступа получен")

    # ── запросы к API ───────────────────────────────────────────────────────
    def api(self, method: str, path: str, params: dict | None = None, body=None, _retry=True):
        self.login()
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = self.c.request(method, SITE + "/api" + path, kind="ajax", headers=headers, params=params, data=data)
        if r.status_code == 401 and _retry:
            self.token = None
            return self.api(method, path, params, body, _retry=False)
        if r.status_code >= 400:
            raise IotHttpError(r.status_code, _error_text(r))
        if not r.content:
            return None, r
        try:
            return r.json(), r
        except ValueError:
            raise IotError("сайт вернул не JSON") from None

    def get_all(self, path: str, params: dict | None = None, limit: int = 100, pages: int = 30) -> list:
        out, offset = [], 0
        for _ in range(pages):
            p = dict(params or {})
            p["limit"], p["offset"] = limit, offset
            try:
                data, r = self.api("GET", path, params=p)
            except IotHttpError as e:
                if e.status == 400 and offset == 0:      # сайт не принял постраничность — берём как отдаёт
                    data, r = self.api("GET", path, params=params)
                    return data if isinstance(data, list) else []
                raise
            if not isinstance(data, list):
                return out
            out += data
            total = r.headers.get("Pagination-Total", "")
            if len(data) < limit or (total.isdigit() and len(out) >= int(total)):
                break
            offset += len(data)
        return out

    # ── чтение ──────────────────────────────────────────────────────────────
    @staticmethod
    def _want_detail(e: dict) -> bool:
        return bool(e.get("canChoose") or (e.get("isMember") and not e.get("outcome")) or e.get("type") == "calendar")

    def _detail(self, e: dict):
        ref = e.get("slug") or e.get("id")
        params = {"expand": "options.tags"}
        if e.get("slug"):
            params["slug"] = "true"
        data, _ = self.api("GET", f"/electives/{quote(str(ref), safe='')}", params=params)
        return data

    def refresh(self) -> dict:
        """Читает блоки, варианты, ваш выбор и занятия; сохраняет снимок. → состояние (+ «changes» — что нового)."""
        self.diag = []
        try:
            return self._refresh()
        except Exception:
            self._save_debug()
            raise

    def _refresh(self) -> dict:
        now = datetime.now(timezone.utc)
        self.rep.log("Элективы: вхожу на iot.sevsu.ru…")
        self.login()
        me = {}
        try:
            prof, _ = self.api("GET", "/profile")
            if isinstance(prof, dict):
                name = " ".join(x for x in (prof.get("lastName"), prof.get("firstName"), prof.get("middleName")) if x)
                me = {"name": name, "rating": prof.get("rating")}
        except IotError as e:
            self.diag.append(f"  профиль не прочитан: {e}")
        items = self.get_all("/electives")
        self.rep.log(f"Элективы: блоков выбора — {len(items)}")
        names: dict = {}
        details, choices = {}, {}
        for e in items:
            if self.c.control:
                self.c.control.checkpoint()
            if not self._want_detail(e):
                continue
            try:
                d = self._detail(e)
                details[str(e.get("id"))] = d
                if isinstance(d, dict):
                    for o in (d.get("_embedded") or {}).get("options") or []:
                        names[str(o.get("id"))] = o.get("fullName") or o.get("shortName") or ""
                if e.get("isMember"):
                    lst, _ = self.api("GET", "/choices", params={"filter": f"electiveId:{e.get('id')};status:active"})
                    choices[str(e.get("id"))] = (lst[0] if isinstance(lst, list) and lst else {})
            except IotHttpError as ex:
                self.diag.append(f"  блок «{e.get('name')}»: {ex.status} {ex}")
                self.rep.log(f"  ! блок «{mo.short(e.get('name') or '', 50)}»: {ex}", "warn")
        need = sorted({str(x.get("optionId")) for e in items for x in (e.get("outcome") or []) if str(x.get("optionId")) not in names})
        if need:
            try:
                lst, _ = self.api("GET", "/options", params={"filter": "id:" + ",".join(need)})
                for o in lst or []:
                    names[str(o.get("id"))] = o.get("fullName") or o.get("shortName") or ""
            except IotError as ex:
                self.diag.append(f"  названия вариантов не прочитаны: {ex}")
        blocks = [norm_block(e, now, names, details.get(str(e.get("id"))), choices.get(str(e.get("id")))) for e in items]
        events = None
        cal = [b for b in blocks if b["type"] == "calendar" and b["is_member"]]
        if cal:
            events = []
            start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=EVENT_DAYS)
            for b in cal:
                try:
                    lst = self.get_all("/calendar-events", {
                        "filter": f"electiveId:{b['id']};startAt:..{_iso(end)};endAt:{_iso(start)}..",
                        "expand": "location.building,organizers,registration"}, limit=1000, pages=2)
                except IotError as ex:
                    self.diag.append(f"  занятия «{b['name']}»: {ex}")
                    self.rep.log(f"  ! занятия блока «{mo.short(b['name'], 50)}» не прочитаны: {ex}", "warn")
                    continue
                for ev in lst:
                    n = norm_event(ev)
                    n["block"] = b["name"]
                    n["option"] = names.get(str(n["option_id"]), "")
                    events.append(n)
            events.sort(key=lambda x: x["start"] or "")
        blocks.sort(key=lambda b: (0 if b["status"] == "choose" else 1, -(b["year"] or 0) if isinstance(b["year"], int) else 0,
                                   str(b["term"] or ""), b["name"]))
        state = {"updated": datetime.now().strftime("%d.%m.%Y %H:%M"), "ts": time.time(), "me": me, "blocks": blocks}
        if events is not None:
            state["events"] = events
        old = self.load()
        state["changes"] = diff_states(old, state)
        mo.fmkdir(self.out)
        mo.write_text_atomic(self.state_path, json.dumps(state, ensure_ascii=False, indent=1))
        self.cookies_out = self.cookies()
        self.rep.log(f"Элективы: сохранено блоков {len(blocks)}" + (f", занятий {len(events)}" if events else "")
                     + (f"; новое: {'; '.join(state['changes'])}" if state["changes"] else ""))
        return state

    # ── действия (только после нажатия кнопки в окне) ───────────────────────
    def save_choice(self, block_id, primary: list, backup: list) -> dict:
        """Сохраняет выбор: основные варианты и резервы по порядку приоритета. → {"ok", "message", "state"}."""
        self.diag = []
        self.login()
        now = datetime.now(timezone.utc)
        cur = next((e for e in self.get_all("/electives") if _same(e.get("id"), block_id)), None)
        if cur is None:
            raise IotError("такого блока уже нет на сайте — обновите список")
        d = self._detail(cur)
        names: dict = {}
        block = norm_block(cur, now, names, d)
        why = check_choice(block, list(primary), list(backup))
        if why:
            raise IotError(why)
        body = {"electiveId": cur.get("id"), "primaryOptions": [{"optionId": x} for x in primary],
                "backupOptions": [{"optionId": x} for x in backup]}
        self.rep.log("Элективы: сохраняю выбор «" + mo.short(block["name"], 60) + "»: "
                     + "; ".join(f"{k} — {names.get(str(x), x)}" for k, x in
                                 [("основной", p) for p in primary] + [(f"резерв {i}", b) for i, b in enumerate(backup, 1)]))
        try:
            res, _ = self.api("POST", "/choices", body=body)
        except IotHttpError as e:
            raise IotError(f"сайт не принял выбор: {e}") from e
        state = self._refresh()
        return {"ok": True, "message": "Выбор сохранён на сайте", "state": state, "choice": res}

    def register(self, event_id) -> dict:
        self.diag = []
        self.login()
        try:
            self.api("POST", "/calendar-event-registrations", body={"calendarEventId": event_id, "autoRegister": True})
        except IotHttpError as e:
            raise IotError(f"сайт не принял запись: {e}") from e
        return {"ok": True, "message": "Вы записаны на занятие", "state": self._refresh()}

    def retract(self, registration_id) -> dict:
        self.diag = []
        self.login()
        try:
            self.api("DELETE", f"/calendar-event-registrations/{quote(str(registration_id), safe='')}")
        except IotHttpError as e:
            raise IotError(f"сайт не отменил запись: {e}") from e
        return {"ok": True, "message": "Запись на занятие отменена", "state": self._refresh()}


class IotRedirectRejected(IotError):
    pass

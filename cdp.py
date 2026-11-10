# -*- coding: utf-8 -*-
"""
Мини-управление фоновым Edge/Chrome по протоколу DevTools (CDP) — без сторонних библиотек.

Нужно для сайтов, которые строят страницу скриптом и переключают недели кнопками (личное расписание СевГУ):
программа открывает страницу в невидимом браузере с cookie вашей сессии, нажимает «неделю», ждёт и читает
итоговый текст — так же, как это сделали бы вы. Используется временный профиль браузера (ваш обычный не трогается).
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import moodle_offline as mo


class CDPError(Exception):
    pass


class _WS:
    """Минимальный клиент WebSocket (RFC 6455): текстовые кадры, ping/pong, фрагментация."""

    def __init__(self, url, timeout=30):
        p = urlparse(url)
        self.sock = socket.create_connection((p.hostname, p.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = p.path + (("?" + p.query) if p.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {p.hostname}:{p.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("браузер закрыл соединение")
            buf += chunk
        head, self._rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("браузер не принял подключение")

    def _read(self, n):
        out = self._rest[:n]
        self._rest = self._rest[n:]
        while len(out) < n:
            chunk = self.sock.recv(max(65536, n - len(out)))
            if not chunk:
                raise CDPError("соединение с браузером оборвалось")
            out += chunk
        if len(out) > n:
            self._rest = out[n:] + self._rest
            out = out[:n]
        return out

    def send(self, text: str):
        data = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b1, b2 = self._read(2)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 0x9:      # ping → pong
                self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if op == 0x8:
                raise CDPError("браузер закрыл соединение")
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """Фоновый браузер: открыть страницу, подождать, выполнить JS, прочитать HTML, напечатать в PDF."""

    def __init__(self, exe, cookies=None, timeout=60, user_agent=None, direct=True):
        self.timeout = timeout
        self._prof = tempfile.TemporaryDirectory(prefix="mo-cdp-")
        args = [str(exe), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--mute-audio", "--no-sandbox", "--remote-debugging-port=0",
                f"--user-data-dir={self._prof.name}", f"--user-agent={user_agent or mo.USER_AGENT}", "--lang=ru-RU"]
        if direct:          # как и остальная программа: напрямую, мимо системного прокси (если не включено иное)
            args.append("--no-proxy-server")
        if os.environ.get("MOODLE_BROWSER_ARGS"):
            args += os.environ["MOODLE_BROWSER_ARGS"].split()
        args.append("about:blank")
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        port_file = Path(self._prof.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if port_file.exists():
                lines = port_file.read_text().split("\n")
                if len(lines) >= 2 and lines[0].strip().isdigit():
                    break
            if self.proc.poll() is not None:
                raise CDPError("браузер не запустился")
            time.sleep(0.2)
        else:
            self.close()
            raise CDPError("браузер не ответил")
        port, path = lines[0].strip(), lines[1].strip()
        self.ws = _WS(f"ws://127.0.0.1:{port}{path}", timeout=timeout)
        self._id = 0
        self.events = []
        tid = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.sid = self.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        for m in ("Page.enable", "Network.enable", "Runtime.enable"):
            self.call(m, session=True)
        if cookies:
            self.set_cookies(cookies)

    # ── протокол ──
    def call(self, method, params=None, session=False, timeout=None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = self.sid
        self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            data = json.loads(self.ws.recv())
            if data.get("id") == mid:
                if "error" in data:
                    raise CDPError(f"{method}: {data['error'].get('message')}")
                return data.get("result", {})
            if "method" in data:
                self.events.append(data)
                del self.events[:-200]
        raise CDPError(f"{method}: нет ответа")

    def set_cookies(self, cookies):
        cs = []
        for c in cookies:
            dom = (c.get("domain") or "").lstrip(".")
            if not dom:
                continue
            sec = bool(c.get("secure", True))   # сайты вуза — https; флаг сохраняем как пришёл
            cs.append({"name": c["name"], "value": c["value"], "domain": c.get("domain"), "path": c.get("path") or "/",
                       "secure": sec, "url": f"{'https' if sec else 'http'}://{dom}{c.get('path') or '/'}"})
        if cs:
            self.call("Network.setCookies", {"cookies": cs}, session=True)

    def eval(self, js, await_promise=False):
        r = self.call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": await_promise},
                      session=True)
        if r.get("exceptionDetails"):
            raise CDPError("ошибка скрипта на странице")
        return (r.get("result") or {}).get("value")

    def try_eval(self, js, default=None):
        """Как eval, но во время перехода на другую страницу (контекст скрипта меняется) просто вернёт default."""
        try:
            v = self.eval(js)
            return default if v is None else v
        except CDPError:
            return default

    def cookies(self) -> list:
        """Все cookie фонового браузера — чтобы в следующий раз не входить заново."""
        try:
            cs = self.call("Network.getAllCookies", session=True).get("cookies", [])
        except CDPError:
            cs = self.call("Storage.getCookies").get("cookies", [])
        return [{"name": c["name"], "value": c["value"], "domain": c.get("domain", ""), "path": c.get("path", "/"),
                 "secure": bool(c.get("secure"))} for c in cs]

    def goto(self, url, settle=10):
        self.events.clear()
        self.call("Page.navigate", {"url": url}, session=True)
        self.wait_loaded()
        self.settle(settle)

    def wait_loaded(self, timeout=None):
        time.sleep(0.4)
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:   # ждём загрузку документа
            if self.try_eval("document.readyState") == "complete":
                return True
            time.sleep(0.3)
        return False

    def settle(self, max_wait=10, quiet=1.5):
        """Ждём, пока скрипты сайта дорисуют страницу: текст перестаёт меняться ~1,5 с."""
        last, stable_since, t0 = None, time.monotonic(), time.monotonic()
        while time.monotonic() - t0 < max_wait:
            n = self.try_eval("document.body ? document.body.innerText.length : 0", 0)
            if n != last:
                last, stable_since = n, time.monotonic()
            elif time.monotonic() - stable_since >= quiet and n:
                return
            time.sleep(0.3)

    def html(self) -> str:
        return self.try_eval("document.documentElement.outerHTML", "")

    def url(self) -> str:
        return self.try_eval("location.href", "")

    def text(self) -> str:
        return self.try_eval("document.body ? document.body.innerText : ''", "")

    def has_password(self) -> bool:
        return bool(self.try_eval("!!document.querySelector('input[type=password]')", False))

    def annotate(self) -> int:
        """Нумерует все кнопки и ссылки страницы (атрибут data-mo-idx), чтобы потом нажать именно ту."""
        return self.try_eval(JS_ANNOTATE, 0)

    def click_idx(self, idx) -> bool:
        return bool(self.try_eval(f"(() => {{ const e = document.querySelector('[data-mo-idx=\"{int(idx)}\"]');"
                                  " if (!e) return false; e.scrollIntoView({block: 'center'}); e.click(); return true; })()",
                                  False))

    def wait_for(self, js, timeout=20) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.try_eval(js, False):
                return True
            time.sleep(0.5)
        return False

    def screenshot(self, path: Path) -> bool:
        try:
            r = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, session=True)
            with mo.fopen(path, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            return True
        except Exception:
            return False

    def page_info(self) -> dict:
        return self.try_eval(JS_PAGE_INFO, {}) or {}

    def confirm_dialog(self) -> str:
        """Если сайт после нажатия спросил «Вы уверены?» — подтверждаем (вы уже подтвердили в программе)."""
        return self.try_eval(JS_CONFIRM, "")

    def pdf(self, path: Path) -> bool:
        try:
            r = self.call("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True}, session=True)
            tmp = Path(str(path) + ".tmp")
            with mo.fopen(tmp, "wb") as f:
                f.write(base64.b64decode(r["data"]))
            mo.freplace(tmp, path)
            return True
        except Exception:
            return False

    def close(self):
        try:
            if hasattr(self, "ws"):
                try:
                    self.call("Browser.close", timeout=5)
                except Exception:
                    pass
                self.ws.close()
        finally:
            if getattr(self, "proc", None) and self.proc.poll() is None:
                mo._kill_tree(self.proc)
            try:
                self._prof.cleanup()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# JS-помощники: найти переключатель недель и выбрать нужную неделю (выпадающий список или кнопки «7 8 9 …»)
JS_WEEKS = r"""(() => {
  const out = [];
  for (const s of document.querySelectorAll('select')) {
    for (const o of s.options) { const m = o.textContent.match(/недел[яи]\s*(\d{1,2})\s*\((\d{1,2})\.(\d{1,2})/i);
      if (m) out.push({how: 'select', n: +m[1], d: +m[2], m: +m[3], value: o.value, selected: o.selected}); }
  }
  if (!out.length) {
    const cands = [...document.querySelectorAll('a,button,[role=button],li,span')].filter(e => /^\d{1,2}$/.test(e.textContent.trim()));
    for (const e of cands) { const box = e.closest('nav,ul,div,footer,section'); if (box && /недел/i.test(box.textContent.slice(0, 200)))
      out.push({how: 'click', n: +e.textContent.trim(), selected: /active|current|selected/i.test(e.className + ' ' + (e.parentElement ? e.parentElement.className : '')) || e.getAttribute('aria-current') !== null}); }
  }
  const cur = (document.body.innerText.match(/(\d{1,2})\s*недел/i) || document.body.innerText.match(/недел[яи]\s*(\d{1,2})/i) || [])[1];
  return {weeks: out, current: cur ? +cur : null};
})()"""

JS_PICK = r"""((n) => {
  for (const s of document.querySelectorAll('select')) {
    for (const o of s.options) { const m = o.textContent.match(/недел[яи]\s*(\d{1,2})\s*\(/i);
      if (m && +m[1] === n) { s.value = o.value; s.dispatchEvent(new Event('input', {bubbles: true}));
        s.dispatchEvent(new Event('change', {bubbles: true})); return 'select'; } } }
  const cands = [...document.querySelectorAll('a,button,[role=button],li,span')].filter(e => e.textContent.trim() === String(n));
  for (const e of cands) { const box = e.closest('nav,ul,div,footer,section'); if (box && /недел/i.test(box.textContent.slice(0, 200))) { e.click(); return 'click'; } }
  return '';
})"""

JS_ANNOTATE = r"""(() => { let i = 0;
  for (const e of document.querySelectorAll('a,button,input[type=submit],input[type=button],[role=button],.btn,.v-btn,.ant-btn,.el-button'))
    e.setAttribute('data-mo-idx', String(i++));
  // кнопки, сделанные из <div>/<span>: курсор-«рука» и короткая надпись про запись
  const RX = /запис|отпис|отказ|выбра|участв|бронир|заявк|подать|регистр|мест нет|нет мест|enrol|join|sign ?up|register/i;
  for (const e of document.querySelectorAll('body *')) {
    if (e.hasAttribute('data-mo-idx') || e.closest('[data-mo-click],a,button')) continue;
    const t = (e.innerText || '').trim();
    if (!t || t.length > 60 || !RX.test(t)) continue;
    const cs = getComputedStyle(e);
    if (cs.cursor === 'pointer' || e.onclick || e.hasAttribute('tabindex') || /btn|button/i.test(e.className || '')
        || (/мест нет|нет мест|заполнен/i.test(t) && e.children.length === 0)) {
      e.setAttribute('data-mo-idx', String(i++)); e.setAttribute('data-mo-click', '1'); }
  }
  return i; })()"""

JS_PAGE_INFO = r"""(() => { let shadow = 0; for (const e of document.querySelectorAll('*')) if (e.shadowRoot) shadow++;
  return {iframes: [...document.querySelectorAll('iframe')].map(f => f.src).filter(Boolean).slice(0, 5), shadow,
          text: document.body ? document.body.innerText.length : 0, title: document.title}; })()"""

JS_CONFIRM = r"""(() => {
  const dlg = [...document.querySelectorAll('.modal.show,[role=dialog],[role=alertdialog],.swal2-popup,.v-dialog--active,.el-message-box,.ant-modal')]
    .find(d => d.offsetParent !== null || getComputedStyle(d).display !== 'none');
  if (!dlg) return '';
  const b = [...dlg.querySelectorAll('button,a,[role=button]')].find(b => /^(да|ок|ok|подтвердить|подтверждаю|продолжить|записаться|отписаться|выбрать|yes|confirm)$/i.test(b.textContent.trim()));
  if (!b) return '';
  b.click(); return b.textContent.trim(); })()"""

JS_LOGIN = r"""(([u, p]) => {
  const pw = document.querySelector('input[type=password]'); if (!pw) return 'nopw';
  const f = pw.form || document;
  const user = f.querySelector('input[name=username],input[name=login],input[name=UserName],input[type=email],input[type=text]:not([type=hidden])');
  const set = (el, v) => { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value'); d.set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); };
  if (user) set(user, u); set(pw, p);
  const rm = f.querySelector('input[name=rememberMe]'); if (rm && !rm.checked) rm.click();
  const btn = f.querySelector('[type=submit],button:not([type=button])');
  if (btn) btn.click(); else if (pw.form) pw.form.submit(); else return 'nosubmit';
  return 'sent'; })"""

JS_LOGIN_ERROR = r"""(() => { const e = document.querySelector('#input-error,#input-error-username,.kc-feedback-text,.alert-error,.alert-danger,.pf-c-alert__title,.pf-v5-c-alert__title,.invalid-feedback,[class*=error]');
  return e ? e.textContent.trim().slice(0, 200) : ''; })()"""


def _site(host: str) -> str:
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:])


def open_authed(exe, url, creds=None, cookies=None, rep=None, diag=None, timeout=60, settle=12, direct=True):
    """Открывает страницу в фоновом браузере под учётной записью вуза.

    Сначала подставляет сохранённые cookie (единый вход СевГУ — Keycloak — тогда пускает без пароля).
    Если сайт всё же показал форму входа — один раз вводит логин и пароль (только на https и только на сайтах
    того же вуза, что и страница), ждёт возврата и даёт скриптам сайта дорисовать страницу.
    Повторно неверный пароль не вводится — чтобы не заблокировать учётную запись."""
    diag = diag if diag is not None else []
    target = urlparse(url).hostname or ""
    b = Browser(exe, cookies=cookies, timeout=timeout, direct=direct)
    try:
        b.goto(url, settle=settle)
        sent = False
        for _ in range(3):
            cur = urlparse(b.url())
            diag.append(f"браузер: {cur.scheme}://{cur.netloc}{cur.path} · форма входа: {'да' if b.has_password() else 'нет'}")
            if not b.has_password():
                break
            if sent:
                err = b.try_eval(JS_LOGIN_ERROR, "")
                raise CDPLoginError("вход не удался" + (f": {err}" if err else " — проверьте логин и пароль на сайте"))
            if not creds:
                raise CDPLoginError("сайт просит войти — нужен логин и пароль учётной записи СевГУ")
            if (cur.scheme != "https" and urlparse(url).scheme == "https") or _site(cur.hostname) != _site(target):
                raise CDPLoginError(f"форма входа на чужом сайте ({cur.hostname}) — пароль туда не отправляю")
            if rep:
                rep.log(f"  вход в учётную запись СевГУ ({cur.hostname})…", "debug")
            res = b.try_eval(f"({JS_LOGIN})({json.dumps(list(creds), ensure_ascii=False)})", "")
            diag.append(f"  ввод логина/пароля: {res}")
            sent = True
            b.wait_loaded()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and b.has_password() and not b.try_eval(JS_LOGIN_ERROR, ""):
                time.sleep(0.5)
            b.wait_loaded()
            b.settle(settle)
        if urlparse(b.url()).hostname != target:   # единый вход ещё возвращает нас на сайт
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and urlparse(b.url()).hostname != target:
                time.sleep(0.5)
            b.wait_loaded()
            b.settle(settle)
        return b
    except Exception:
        b.close()
        raise


class CDPLoginError(CDPError):
    pass

"""Макет auth.sevsu.ru (Keycloak, код + PKCE) и iot.sevsu.ru/api (элективы) — для проверок iotapi.py без настоящего сайта.
Хосты: auth.sevsu.test, iot.sevsu.test (в /etc/hosts → 127.0.0.1). Порт — 1-й аргумент (по умолчанию 8766)."""
import _root  # noqa: F401  (быстрый запуск сервера без обратного DNS, см. _root.py)
import base64, hashlib, json, secrets, sys, threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode, unquote

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
LOCK = threading.Lock()
STATS = {"login_posts": 0, "auth_calls": 0, "token_calls": 0, "api_calls": 0, "choice_posts": 0, "reg_posts": 0, "reg_deletes": 0,
         "bad_pkce": 0, "last_ua": "", "auth_header_seen": 0}
SESS, CODES, TOKENS = set(), {}, {}
NOW = lambda: datetime.now(timezone.utc)
Z = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")

OPTS = {  # id → параметры
    101: dict(fullName="Волейбол (юноши и девушки)", shortName="Волейбол", capacity=25, occupied=22, filling=True, backup=True),
    102: dict(fullName="Плавание", shortName="Плавание", capacity=20, occupied=20, filling=True, backup=True),
    103: dict(fullName="Атлетическая гимнастика", shortName="Атлетическая гимнастика", capacity=30, occupied=10, filling=True, backup=True),
    104: dict(fullName="Футбол", shortName="Футбол", capacity=30, occupied=5, filling=True, backup=True),
    105: dict(fullName="Настольный теннис", shortName="Настольный теннис", capacity=16, occupied=16, filling=False, backup=True),
    201: dict(fullName="Общая физическая подготовка", shortName="ОФП", capacity=60, occupied=60, filling=False, backup=False),
    301: dict(fullName="Секция «Лёгкая атлетика»", shortName="Лёгкая атлетика", capacity=40, occupied=12, filling=True, backup=False),
}


def option_json(i, d):
    return {"id": i, "fullName": d["fullName"], "shortName": d["shortName"], "capacity": d["capacity"], "occupied": d["occupied"],
            "isFillingAllowed": d["filling"], "isBackupAllowed": d["backup"], "parentId": None, "requisitesSatisfied": True,
            "subOptionsMinimum": 0, "subOptionsMaximum": 0, "detailsUrl": "", "description": "<p>Занятия по расписанию, форма — спортивная.</p>"}


STATE = {"choice11": None, "events": [], "regs": {}}


def reset():
    n = NOW()
    ev = []
    base = (n + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    spec = [("Лёгкая атлетика: кросс", 0, 30, 12, None, False), ("Лёгкая атлетика: спринт", 1, 30, 40, None, False),
            ("Лёгкая атлетика: эстафета", 2, 16, 16, None, False), ("Лёгкая атлетика: отменено", 3, 30, 0, None, True)]
    for k, (name, dd, cap, occ, _r, canc) in enumerate(spec):
        s = base + timedelta(days=dd)
        ev.append({"id": 9000 + k, "electiveId": 13, "optionId": 301, "name": name, "startAt": Z(s), "endAt": Z(s + timedelta(hours=1, minutes=30)),
                   "capacity": cap, "occupied": occ, "status": "cancelled" if canc else "active", "cancellationReason": "Болезнь преподавателя" if canc else "",
                   "canRegister": (occ < cap) and not canc})
    STATE.update(choice11=None, events=ev, regs={})
    STATE.pop("force_new_block", None)
    OPTS[103]["occupied"] = 10


reset()


def electives_list():
    n = NOW()
    return [
        {"id": 11, "slug": "elektivnye-kursy-po-fizicheskoi-kulture-i-sportu", "name": "Элективные курсы по физической культуре и спорту",
         "type": "choice", "academicYear": 2026, "academicTerm": 1, "canChoose": True, "isMember": True, "outcome": [],
         "announcement": "<p>Выберите <b>один основной</b> вариант и два резерва по приоритету.</p>",
         "flow": {"primaryOptions": 1, "backupOptions": 2, "choiceWaves": [{"openedAt": Z(n - timedelta(days=3))}],
                  "choiceFixedAt": Z(n + timedelta(days=2)), "choiceClosedAt": Z(n + timedelta(days=5)), "finalizedAt": Z(n + timedelta(days=7))}},
        {"id": 12, "slug": "vybor-inostrannogo-yazyka", "name": "Выбор иностранного языка", "type": "choice", "academicYear": 2025, "academicTerm": 2,
         "canChoose": False, "isMember": True, "outcome": [{"optionId": 201}], "announcement": "",
         "flow": {"primaryOptions": 1, "backupOptions": 0, "choiceWaves": [{"openedAt": Z(n - timedelta(days=200))}],
                  "choiceClosedAt": Z(n - timedelta(days=150))}},
        {"id": 13, "slug": "sektsiya-legkoi-atletiki", "name": "Секция лёгкой атлетики", "type": "calendar", "academicYear": 2026, "academicTerm": 1,
         "canChoose": False, "isMember": True, "outcome": [{"optionId": 301}], "announcement": "",
         "flow": {"primaryOptions": 1, "backupOptions": 0, "choiceWaves": [{"openedAt": Z(n - timedelta(days=30))}],
                  "choiceClosedAt": Z(n - timedelta(days=10))}},
    ] + [{"id": 20 + i, "slug": f"arkhiv-{i}", "name": f"Архивный блок {i}", "type": "choice", "academicYear": 2020, "academicTerm": 1,
          "canChoose": False, "isMember": False, "outcome": [], "announcement": "",
          "flow": {"primaryOptions": 1, "backupOptions": 0, "choiceWaves": [], "choiceClosedAt": Z(n - timedelta(days=900))}} for i in range(1, 4)]


def detail(e):
    d = dict(e)
    ids = {11: [101, 102, 103, 104, 105], 12: [201], 13: [301]}.get(e["id"], [])
    d["_embedded"] = {"options": [option_json(i, OPTS[i]) for i in ids]}
    return d


def events_json(eid):
    out = []
    for ev in STATE["events"]:
        if ev["electiveId"] != eid:
            continue
        j = dict(ev)
        reg = STATE["regs"].get(ev["id"])
        j["_embedded"] = {"registration": reg or {}, "organizers": [{"lastName": "Петров", "firstName": "Пётр", "middleName": "Петрович"}],
                          "location": {"fullName": "Стадион", "_embedded": {"building": {"fullName": "Корпус Д"}}}}
        out.append(j)
    return out


def check_choice(body):
    """Правила сайта: 1 основной + 2 резерва, без повторов, места есть."""
    p = [x["optionId"] for x in body.get("primaryOptions", [])]
    b = [x["optionId"] for x in body.get("backupOptions", [])]
    if len(p) != 1:
        return {"message": "Некорректные данные", "errors": {"primaryOptions": ["Нужно выбрать ровно 1 основной вариант"]}}
    if len(b) != 2:
        return {"message": "Некорректные данные", "errors": {"backupOptions": ["Нужно выбрать 2 резервных варианта"]}}
    if len(set(p + b)) != 3:
        return {"message": "Некорректные данные", "errors": {"options": ["Варианты не должны повторяться"]}}
    for i in p + b:
        o = OPTS.get(i)
        if not o or i not in (101, 102, 103, 104, 105):
            return {"message": "Некорректные данные", "errors": {"options": [f"Нет варианта {i}"]}}
        if not o["filling"] or o["occupied"] >= o["capacity"]:
            return {"message": "Некорректные данные", "errors": {"options": [f"В варианте «{o['shortName']}» нет мест"]}}
    return None


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
            ctype = "application/json; charset=utf-8"
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_DELETE(self): self.route("DELETE")

    def route(self, method):
        with LOCK:
            try:
                self._route(method)
            except Exception as e:
                import traceback; traceback.print_exc()
                self.send(500, {"message": str(e)})

    def _route(self, method):
        host = (self.headers.get("Host") or "").split(":")[0]
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method in ("POST", "DELETE") else b""
        STATS["last_ua"] = self.headers.get("User-Agent", "")[:40]
        if u.path == "/__stats":
            return self.send(200, STATS)
        if u.path == "/__reset":
            reset(); [STATS.__setitem__(k, 0) for k in STATS if k != "last_ua"]; return self.send(200, {"ok": True})
        if u.path == "/__expire":
            TOKENS.clear(); return self.send(200, {"ok": True})
        if u.path == "/__logout":
            SESS.clear(); TOKENS.clear(); return self.send(200, {"ok": True})
        if u.path == "/__setfull":
            OPTS[103]["occupied"] = OPTS[103]["capacity"]; return self.send(200, {"ok": True})
        if u.path == "/__opened":                                  # открыть выбор у «закрытого» блока — для проверки уведомления
            STATE["force_new_block"] = True; return self.send(200, {"ok": True})
        if host.startswith("auth."):
            return self.auth(method, u, q, raw)
        return self.iot(method, u, q, raw)

    # ── Keycloak ──
    def auth(self, method, u, q, raw):
        if u.path.endswith("/openid-connect/auth"):
            STATS["auth_calls"] += 1
            if q.get("client_id") != "iot" or q.get("response_type") != "code":
                return self.send(400, "bad client")
            if not (q.get("redirect_uri") or "").startswith("http://iot.sevsu.test"):
                return self.send(400, "<h1>Invalid parameter: redirect_uri</h1>")
            if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
                STATS["bad_pkce"] += 1
            sep = "#" if q.get("response_mode") == "fragment" else "?"
            ctx = {"r": q["redirect_uri"], "mode": q.get("response_mode", "query"), "state": q.get("state", ""), "cc": q.get("code_challenge", "")}
            if self.cookies().get("KC") in SESS:
                code = "c" + secrets.token_hex(6)
                CODES[code] = (q.get("code_challenge", ""), q["redirect_uri"])
                return self.send(302, b"", headers=[("Location", q["redirect_uri"] + sep + urlencode({"state": q.get("state", ""), "session_state": "s", "code": code}))])
            return self.send(200, FORM % ("", urlencode(ctx)))
        if u.path.endswith("/login-actions/authenticate") and method == "POST":
            STATS["login_posts"] += 1
            f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if f.get("username") == "student" and f.get("password") == "pass123":
                sid = secrets.token_hex(8)
                SESS.add(sid)
                code = "c" + secrets.token_hex(6)
                CODES[code] = (q.get("cc", ""), q["r"])
                sep = "#" if q.get("mode") == "fragment" else "?"
                return self.send(302, b"", headers=[("Set-Cookie", f"KC={sid}; Path=/; HttpOnly"),
                                                    ("Location", q["r"] + sep + urlencode({"state": q.get("state", ""), "session_state": "s", "code": code}))])
            return self.send(200, FORM % ('<span id="input-error" class="kc-feedback-text">Неверное имя пользователя или пароль.</span>', u.query))
        if u.path.endswith("/openid-connect/token") and method == "POST":
            STATS["token_calls"] += 1
            f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            ent = CODES.pop(f.get("code", ""), None)
            if not ent or f.get("client_id") != "iot" or f.get("redirect_uri") != ent[1]:
                return self.send(400, {"error": "invalid_grant", "error_description": "Code not valid"})
            chal = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if ent[0] and chal != ent[0]:
                STATS["bad_pkce"] += 1
                return self.send(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
            tok = "tok" + secrets.token_hex(8)
            TOKENS[tok] = 1
            return self.send(200, {"access_token": tok, "expires_in": 300, "token_type": "Bearer"})
        self.send(404, "not found")

    # ── API ──
    def iot(self, method, u, q, raw):
        if not u.path.startswith("/api/"):
            return self.send(200, "<html><body><div id=app></div></body></html>")
        STATS["api_calls"] += 1
        auth = (self.headers.get("Authorization") or "")
        if auth.startswith("Bearer "):
            STATS["auth_header_seen"] += 1
        if auth.replace("Bearer ", "") not in TOKENS:
            return self.send(401, {"message": "Unauthorized"})
        path = u.path[4:]
        body = json.loads(raw.decode() or "null") if raw else None
        if path == "/profile":
            return self.send(200, {"lastName": "Иванов", "firstName": "Иван", "middleName": "Иванович", "rating": 4.2})
        lim, off = int(q.get("limit", 1000)), int(q.get("offset", 0))

        def page(lst):
            return self.send(200, lst[off:off + lim], headers=[("Pagination-Total", str(len(lst)))])
        if path == "/electives":
            lst = electives_list()
            if STATE.get("force_new_block"):
                lst.append({"id": 77, "slug": "novyi", "name": "Новый блок выбора", "type": "choice", "academicYear": 2026, "academicTerm": 1,
                            "canChoose": True, "isMember": False, "outcome": [], "announcement": "", "flow": {"primaryOptions": 1, "backupOptions": 0, "choiceWaves": []}})
            return page(lst)
        if path.startswith("/electives/"):
            ref = unquote(path.split("/", 2)[2])
            e = next((x for x in electives_list() if x["slug"] == ref or str(x["id"]) == ref), None)
            return self.send(200, detail(e)) if e else self.send(404, {"message": "Not found"})
        if path == "/options":
            ids = [int(x) for x in q.get("filter", "id:").split(":", 1)[1].split(",") if x]
            return page([option_json(i, OPTS[i]) for i in ids if i in OPTS])
        if path == "/choices" and method == "GET":
            flt = dict(p.split(":", 1) for p in q.get("filter", "").split(";") if ":" in p)
            c = STATE["choice11"] if flt.get("electiveId") == "11" else None
            return page([c] if c else [])
        if path == "/choices" and method == "POST":
            STATS["choice_posts"] += 1
            err = check_choice(body or {})
            if err:
                return self.send(422, err)
            STATE["choice11"] = {"id": 5001, "electiveId": body["electiveId"], "status": "active", "primaryOptions": body["primaryOptions"],
                                 "backupOptions": body["backupOptions"]}
            return self.send(201, STATE["choice11"])
        if path == "/calendar-events":
            flt = dict(p.split(":", 1) for p in q.get("filter", "").split(";") if ":" in p)
            return page(events_json(int(flt.get("electiveId", 0))))
        if path == "/calendar-event-registrations" and method == "POST":
            STATS["reg_posts"] += 1
            ev = next((x for x in STATE["events"] if x["id"] == (body or {}).get("calendarEventId")), None)
            if not ev or not ev["canRegister"]:
                return self.send(422, {"message": "Запись невозможна", "errors": {"event": ["Нет мест или занятие отменено"]}})
            reg = {"id": 7000 + ev["id"], "status": "registered", "attendance": None}
            STATE["regs"][ev["id"]] = reg
            ev["occupied"] += 1
            ev["canRegister"] = False
            return self.send(201, reg)
        if path.startswith("/calendar-event-registrations/") and method == "DELETE":
            STATS["reg_deletes"] += 1
            rid = int(path.rsplit("/", 1)[1])
            ev = next((x for x in STATE["events"] if STATE["regs"].get(x["id"], {}).get("id") == rid), None)
            if not ev:
                return self.send(404, {"message": "Not found"})
            STATE["regs"].pop(ev["id"])
            ev["occupied"] -= 1
            ev["canRegister"] = ev["occupied"] < ev["capacity"]
            return self.send(204, b"")
        self.send(404, {"message": "no route " + path})


FORM = """<!DOCTYPE html><html><head><title>Войти в СевГУ.Аккаунт</title></head><body>%s
<form id="kc-form-login" action="/realms/portal/login-actions/authenticate?%s" method="post">
<input name="username" type="text"><input name="password" type="password"><input type="checkbox" name="rememberMe">
<input type="hidden" name="credentialId" value=""><input type="submit" name="login" value="Вход"></form></body></html>"""

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

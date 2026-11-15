"""Макет schedule.sevsu.ru для тестов: страница группы, список групп отдела, пары по неделям (тот же JSON, что у сайта).
Пары у каждой группы подписаны её названием («Матан · ИБ/б-25-2-о»), чтобы в тестах было видно, чья пара попала в расписание.
Запуск отдельно: python tests/mock_schedule.py 8766 (или MockSchedule().start() из теста)."""
import _root
import json, sys, threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from httpbind import QuickHTTPServer

GROUPS = {"ib-b-25-2-o": "ИБ/б-25-2-о", "ib-b-25-1-o": "ИБ/б-25-1-о", "pi-b-24-1-o": "ПИ/б-24-1-о"}
DEPT = "7"
START_YEAR = 2026
WEEKS = list(range(1, 21))


def week1() -> date:
    d = date(START_YEAR, 9, 1)
    return d - timedelta(days=d.weekday())


class MockSchedule:
    def __init__(self, port: int = 0):
        self.port = port
        self.log = []
        self.srv = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ct="application/json; charset=utf-8"):
                b = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.log.append(u.path + ("?" + u.query if u.query else ""))
                if u.path.startswith("/groups/"):
                    slug = u.path.split("/")[2]
                    if slug not in GROUPS:
                        return self._send(404, "<h1>404</h1>", "text/html; charset=utf-8")
                    return self._send(200, f"""<html><body><select id="department-select"><option value="{DEPT}">Институт ИТ</option></select>
<script>var PERIOD_START_YEAR = {START_YEAR}; var SCHEDULE_SECTION = 0; new App({{ group: '{GROUPS[slug]}', department: '{DEPT}' }});</script></body></html>""",
                                      "text/html; charset=utf-8")
                if u.path == "/api/groups":
                    return self._send(200, {"ok": True, "groups": [{"group_name": n, "group_slug": s, "weeks": WEEKS} for s, n in GROUPS.items()]})
                if u.path == "/api/subgroups":
                    return self._send(200, {"ok": True, "subgroups": []})
                if u.path == "/api/schedule":
                    name, wk = q.get("group", ""), int(q.get("week", "0") or 0)
                    if name not in GROUPS.values():
                        return self._send(200, {"ok": False, "error": "группа не найдена"})
                    mon = week1() + timedelta(days=7 * (wk - 1))
                    ls = [{"date": (mon + timedelta(days=d)).isoformat() + "T00:00:00", "n": n, "lesson": f"{subj} · {name}", "type": 0,
                           "teacher": "Иванов И.И.", "location": "А-101", "subgroup": 0}
                          for d, n, subj in ((0, 2, "Матан"), (2, 3, "Физика"))]
                    return self._send(200, {"ok": True, "schedule": ls, "last_update": "2026-11-01T10:00:00Z"})
                return self._send(404, {"error": "not found"})

        self.srv = QuickHTTPServer(("127.0.0.1", self.port), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.srv:
            self.srv.shutdown()


def patch(schedule_module, mock: MockSchedule):
    """Направить программу на макет вместо настоящего сайта."""
    schedule_module.SEVSU_BASE = mock.url
    schedule_module.SOURCES["schedule"]["url"] = mock.url + "/groups/{group}"
    schedule_module.SOURCES["schedule"]["site"] = mock.url + "/groups/{group}"


if __name__ == "__main__":
    m = MockSchedule(int(sys.argv[1]) if len(sys.argv) > 1 else 8766).start()
    print("макет schedule.sevsu.ru:", m.url, flush=True)
    threading.Event().wait()

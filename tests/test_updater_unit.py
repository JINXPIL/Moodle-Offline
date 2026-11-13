"""Проверка updater.py без интернета: сравнение версий, разбор ответа GitHub, расписание проверок, безопасные ссылки,
ошибки сети, макет GitHub на localhost (200 / 404 / 403 / мусор / нет связи)."""
import http.server, json, sys, threading, time
import _root
import updater as u

fails = []
def ok(c, msg):
    print(("PASS " if c else "FAIL ") + msg)
    if not c: fails.append(msg)

# версии
ok(u.parse_version("v1.12.1") == (1, 12, 1), "parse v1.12.1")
ok(u.parse_version("1.13.0-beta") == (1, 13, 0), "parse суффикс")
ok(u.parse_version("мусор") is None and u.parse_version(None) is None, "parse мусор")
ok(u.is_newer("v1.13.0", "1.12.1"), "1.13.0 новее 1.12.1")
ok(u.is_newer("1.12.10", "1.12.9"), "1.12.10 новее 1.12.9 (не строкой)")
ok(not u.is_newer("1.12.1", "1.12.1") and not u.is_newer("1.12", "1.12.0"), "равные не новее")
ok(not u.is_newer("1.11.0", "1.12.1"), "старая не новее")
ok(not u.is_newer("мусор", "1.0") and not u.is_newer("1.0", "мусор"), "мусор не новее")
ok(u.is_newer("2", "1.99.9"), "2 новее 1.99.9")

# где запущено
ok(u.kind(apk=True, android=True) == "apk" and u.kind(android=True) == "termux"
   and u.kind(frozen=True) == "exe" and u.kind() == "source", "kind")
ok("apk" in u.how_to("apk").lower() and "git pull" in u.how_to("termux") and "Обновить сейчас" in u.how_to("exe") and "(1)" in u.how_to("exe"), "how_to")

# настройки
n = u.normalize(None)
ok(n["auto"] is True and n["every"] == 24 and n["last_check"] == 0, "defaults: автопроверка включена, раз в сутки")
n = u.normalize({"auto": False, "every": 5, "last_check": "x", "skip": None})
ok(n["auto"] is False and n["every"] == 24 and n["last_check"] == 0 and n["skip"] == "", "мусор в настройках")
ok(u.normalize({"every": 168})["every"] == 168, "every=168 допустим")

# due
now = 1_000_000.0
ok(u.due({"auto": True, "every": 24, "last_check": 0}, now), "due: ни разу не проверяли")
ok(not u.due({"auto": True, "every": 24, "last_check": now - 3600}, now), "due: час назад — рано")
ok(u.due({"auto": True, "every": 24, "last_check": now - 24 * 3600}, now), "due: сутки прошли")
ok(not u.due({"auto": False, "every": 24, "last_check": 0}, now), "due: выключено — никогда")
ok(u.due({"auto": True, "every": 24, "last_check": now + 99999}, now), "due: часы переведены назад")

# разбор релиза
REL = {"tag_name": "v1.13.0", "html_url": "https://github.com/JINXPIL/Moodle-Offline/releases/tag/v1.13.0",
       "published_at": "2026-11-12T08:00:00Z", "body": "Что нового\n" + "x" * 900,
       "assets": [{"name": "MoodleOffline.exe", "size": 12345678,
                   "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.13.0/MoodleOffline.exe"},
                  {"name": "MoodleOffline.apk", "size": 999,
                   "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.13.0/MoodleOffline.apk"},
                  {"name": "MoodleOffline.exe.sha256", "size": 90, "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/x"}]}
s = u.summarize(REL, "exe")
ok(s["version"] == "1.13.0" and s["asset_name"] == "MoodleOffline.exe" and s["size"] == 12345678, "summarize exe")
ok(u.summarize(REL, "apk")["asset_name"] == "MoodleOffline.apk", "summarize apk")
t = u.summarize(REL, "termux")
ok(t["asset_url"] == "" and t["page"].startswith(u.SAFE_PREFIX), "termux: файла нет, есть страница")
ok(len(s["notes"]) <= 701 and s["notes"].endswith("…"), "описание обрезано")
evil = json.loads(json.dumps(REL))
evil["html_url"] = "https://evil.example/x"
evil["assets"][0]["browser_download_url"] = "https://evil.example/MoodleOffline.exe"
e = u.summarize(evil, "exe")
ok(e["asset_url"] == "" and e["page"] == u.PAGE_URL and e["size"] == 0, "чужие ссылки выброшены")
try:
    u.summarize({"tag_name": "nightly"}, "exe"); ok(False, "нет версии в теге → ошибка")
except u.UpdateError:
    ok(True, "нет версии в теге → ошибка")

# run_check / status / should_notify
cfg = u.normalize(None)
u.run_check(cfg, "1.12.1", "exe", now, fetch=lambda cur: REL)
st = u.status(cfg, "1.12.1", "exe")
ok(st["newer"] and not st["skipped"] and cfg["last_check"] == now and not cfg["error"], "run_check нашёл новую")
ok(u.should_notify(cfg, st), "уведомить о новой")
cfg["notified"] = "v1.13.0"
ok(not u.should_notify(cfg, st), "второй раз не уведомлять")
cfg["skip"] = "v1.13.0"
ok(u.status(cfg, "1.12.1", "exe")["skipped"], "пропущенная версия помечена")
cfg["skip"] = ""; cfg["notified"] = ""
ok(not u.status(cfg, "1.13.0", "exe")["newer"], "та же версия — не новее")
ok("1.13.0" in u.notice_text(st) and "1.12.1" in u.notice_text(st), "текст уведомления")

def boom(cur): raise u.UpdateError("нет связи")
cfg2 = u.normalize({"every": 24})
u.run_check(cfg2, "1.12.1", "exe", now, fetch=boom)
ok(cfg2["error"] == "нет связи" and not u.due(cfg2, now + 3000) and u.due(cfg2, now + 3700), "ошибка → повтор через час")
cfg2["latest"] = s
u.run_check(cfg2, "1.12.1", "exe", now, fetch=boom)
ok(cfg2["latest"] == s, "ошибка не стирает прошлый результат")

# макет GitHub на localhost
MODE = {"code": 200, "body": json.dumps(REL)}
HITS = []
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        HITS.append((self.path, self.headers.get("User-Agent", ""), self.headers.get("Authorization"), self.headers.get("Cookie")))
        self.send_response(MODE["code"]); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(MODE["body"].encode())
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/repos/x/releases/latest"
r = u.fetch_latest("1.12.1", url=URL)
ok(r["tag_name"] == "v1.13.0", "fetch 200")
ok(HITS[-1][1] == "MoodleOffline/1.12.1" and HITS[-1][2] is None and HITS[-1][3] is None, "в запросе нет ни cookie, ни токена")
for code, word in ((404, "нет ни одного"), (403, "слишком много"), (500, "500")):
    MODE.update(code=code, body="{}")
    try:
        u.fetch_latest("1.12.1", url=URL); ok(False, f"fetch {code} → ошибка")
    except u.UpdateError as ex:
        ok(word in str(ex), f"fetch {code}: {ex}")
MODE.update(code=200, body="<html>не json")
try:
    u.fetch_latest("1.12.1", url=URL); ok(False, "не-JSON → ошибка")
except u.UpdateError as ex:
    ok("непонятный" in str(ex), "не-JSON → ошибка")
srv.shutdown(); srv.server_close()
try:
    u.fetch_latest("1.12.1", url=URL, timeout=2); ok(False, "нет связи → ошибка")
except u.UpdateError as ex:
    ok("нет связи" in str(ex), "нет связи → понятная ошибка")

print("\nИТОГО:", "всё хорошо" if not fails else f"ПРОВАЛЕНО {len(fails)}: {fails}")
sys.exit(1 if fails else 0)

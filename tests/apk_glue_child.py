# Запускается из test_apk_unit.py в отдельном процессе: Python-часть приложения .apk на компьютере с подделкой моста.
import json, os, sys, tempfile, threading, time, urllib.request, urllib.parse
import _root; sys.path.insert(0, str(_root.FAKE_JAVA))
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""), flush=True); ok = ok and bool(cond)

home = tempfile.mkdtemp(prefix="apkhome_")
import apk_main
class Host:
    url = None
    def onServerReady(self, url): Host.url = url
h = Host()
t = threading.Thread(target=apk_main.run, args=(h, home, str(_root.ROOT / "ui"), False), daemon=True); t.start()
for _ in range(100):
    if Host.url: break
    time.sleep(0.1)
check("сервер поднялся и сообщил адрес", bool(Host.url), Host.url)
base = Host.url.split("/?t=")[0]; tok = Host.url.split("?t=")[1]

def get(path):
    sep = "&" if "?" in path else "?"
    with urllib.request.urlopen(f"{base}{path}{sep}t={tok}", timeout=10) as r: return json.loads(r.read())
def post(path, body):
    req = urllib.request.Request(f"{base}{path}", data=json.dumps(body).encode(), headers={"X-Token": tok, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())

with urllib.request.urlopen(f"{base}/", timeout=10) as r: page = r.read().decode()
check("окно отдаётся из папки MOODLE_OFFLINE_UI", "Moodle Offline" in page and len(page) > 10000)
st = get("/api/state")
check("state: android=True", st.get("android") is True)
ai = st.get("android_info") or {}
check("android_info: kind=apk", ai.get("kind") == "apk", ai)
check("материалы сначала в папке приложения, не видны «Файлам»", ai.get("private_out") is True and ai.get("shared") is False, ai)
check("данные в закрытой папке приложения", home in (st.get("data") or {}).get("dir", "") or home in json.dumps(st.get("data") or {}), st.get("data"))

r = post("/api/android/action", {"name": "allfiles"}); check("действие allfiles → мост", r.get("ok") is True, r)
r = post("/api/android/action", {"name": "battery"}); check("действие battery → мост", r.get("ok") is True, r)
r = post("/api/android/action", {"name": "xxx"}); check("неизвестное действие отклоняется", r.get("ok") is False, r)
r = post("/api/watch/autostart", {"enabled": True}); check("автозапуск включён через мост", r.get("ok") is True and r.get("on") is True and r.get("kind") == "apk-boot", r)
r = post("/api/watch/autostart", {"enabled": False}); check("автозапуск выключен", r.get("ok") is True and r.get("on") is False, r)
r = post("/api/open_url", {"url": "https://do.sevsu.ru"}); check("open_url → мост", r.get("ok") is True)
r = post("/api/settings", {"android_wakelock": True}); time.sleep(0.5)
calls = [json.loads(l) for l in open(os.environ["FAKE_BRIDGE_LOG"], encoding="utf-8")]
names = [c[0] for c in calls]
check("мост получил openUrl с адресом", ["openUrl", "https://do.sevsu.ru"] in calls, calls)
check("мост получил setWakeLock(true)", ["setWakeLock", True] in calls, calls)
check("мост получил requestAllFilesAccess и requestBatteryExemption", "requestAllFilesAccess" in names and "requestBatteryExemption" in names, names)

# разрешили «доступ ко всем файлам» → материалы можно положить в «Документы»
import java; java.STATE["all_files"] = True
ai = get("/api/state")["android_info"]
check("после выдачи доступа: shared=True и предложена папка в Документах", ai.get("shared") is True and ai.get("suggest", "").endswith("Documents/Материалы Moodle"), ai)

apk_main.stop()
t.join(timeout=15)
check("stop() останавливает программу, run() возвращается", not t.is_alive())
print("ALL PASS" if ok else "SOME FAIL")

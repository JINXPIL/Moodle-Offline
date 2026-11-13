import os, sys, subprocess, shutil, json
from pathlib import Path
import _root; sys.path.insert(0, str(_root.FAKE_JAVA))
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else "")); ok = ok and bool(cond)

RUN = _root.APK_RUN; RUN.mkdir(parents=True, exist_ok=True)
SD = str(RUN / "sdcard")
LOG = str(RUN / "calls.log")
os.environ["FAKE_BRIDGE_LOG"] = LOG; os.environ["FAKE_SHARED"] = SD
open(LOG, "w").close(); shutil.rmtree(SD, ignore_errors=True)
import android, java
env = {"MOODLE_OFFLINE_APK": "1"}
check("is_apk по переменной", android.is_apk(env) and not android.is_apk({}))
check("is_android включает .apk", android.is_android(env, platform="linux") and not android.is_android({}, platform="linux"))
os.environ["MOODLE_OFFLINE_APK"] = "1"
check("open_url → Bridge.openUrl", android.open_url("https://x.ru") is True and not android.open_url("ftp://x"))
check("open_file → Bridge.openFile", android.open_file("/tmp/a.pdf") is True)
check("share_file → Bridge.shareFile", android.share_file("/tmp/r.zip") is True and "shareFile" in open(LOG).read())
check("notify → Bridge.notify", android.notify("T", "X") is True)
check("wake_lock → Bridge.setWakeLock", android.wake_lock(True) and android.wake_lock(False))
home = RUN / "home"
check("без доступа: общая память = None", android.shared_storage(home) is None)
d = android.default_materials(home, "Материалы Moodle")
check("без доступа: материалы в папке приложения", "Android/data" in str(d) and str(d).endswith("Материалы Moodle"), d)
check("без доступа: папка считается закрытой", android.in_private_storage(d, home) is True)
java.STATE["all_files"] = True
check("с доступом: общая память — корень", str(android.shared_storage(home)) == SD)
d2 = android.default_materials(home, "Материалы Moodle")
check("с доступом: материалы в Documents", str(d2).endswith("Documents/Материалы Moodle"), d2)
check("с доступом: Documents — не закрытая", android.in_private_storage(d2, home) is False)
check("с доступом: Android/data — всё равно закрытая", android.in_private_storage(Path(SD + "/Android/data/x/files/Материалы"), home) is True)
check("снаружи общей памяти — закрытая", android.in_private_storage(Path("/data/user/0/app/files/x"), home) is True)
check("автозапуск туда-обратно", android.apk_autostart_set(True) and android.apk_autostart_get() and android.apk_autostart_set(False) and not android.apk_autostart_get())
check("разрешения → мост", android.request_all_files() and android.request_battery() and not android.battery_exempt())
# без моста (нет модуля java) ничего не падает
sys.modules.pop("java"); sys.path.remove(str(_root.FAKE_JAVA))
check("без моста: open_url=False, notify=False, wake_lock=False", android.open_url("https://x.ru") is False and android.notify("a", "b") is False and android.wake_lock(True) is False and android.share_file("/tmp/r.zip") is False)
check("без моста: default_materials не падает", isinstance(android.default_materials(home, "М"), Path))
check("без моста: shared_storage=None", android.shared_storage(home) is None)
check("вне .apk запросы разрешений отклоняются", (os.environ.pop("MOODLE_OFFLINE_APK"), not android.request_all_files() and not android.apk_autostart_get())[1])

# Python-часть приложения целиком (apk_main.run) — в отдельном процессе
open(LOG, "w").close()
e = dict(os.environ); e["FAKE_BRIDGE_LOG"] = LOG; e["PYTHONPATH"] = str(_root.FAKE_JAVA); e.pop("MOODLE_OFFLINE_APK", None)
e.pop("TERMUX_VERSION", None)
r = subprocess.run([sys.executable, str(_root.TESTS / "apk_glue_child.py")], env=e, capture_output=True, text=True, timeout=120, cwd="/tmp")
out = r.stdout + r.stderr
for line in out.splitlines():
    if line.startswith(("PASS", "FAIL")): print(line)
check("apk_main.run: все проверки дочернего процесса", "ALL PASS" in out and r.returncode == 0, out[-1500:])
# самопроверка (её запускают в эмуляторе) на компьютере: все пункты кроме сети должны пройти
CODE = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import tempfile, os; h = tempfile.mkdtemp(); import apk_main; "
        "apk_main._prepare_env(h, %r); apk_main._selftest()" % (str(_root.ROOT), str(_root.FAKE_JAVA), str(_root.ROOT / "ui")))
e2 = dict(e); e2["FAKE_SHARED"] = SD
r = subprocess.run([sys.executable, "-c", CODE],
                   env=e2, capture_output=True, text=True, timeout=120, cwd="/tmp")
st = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith("SELFTEST")]
for l in st: print("   ", l[:200])
fails = [l for l in st if l.startswith("SELFTEST FAIL")]
check("самопроверка: пункты выполняются, кроме сети проваленных нет", st and st[-1].startswith("SELFTEST DONE") and all(" https" in l for l in fails), "\n".join(fails))
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)

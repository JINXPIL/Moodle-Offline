"""«Открыть папку» на телефоне: приложение .apk открывает папку в «Файлах» (как проводник на компьютере).
Мост Android подделан (tests/apkfake/java.py); сам Intent проверяет самопроверка apk в эмуляторе (apk_main._selftest)."""
import os, sys, shutil, json
from pathlib import Path
import _root
sys.path.insert(0, str(_root.FAKE_JAVA))
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

RUN = _root.APK_RUN / "folder"; shutil.rmtree(RUN, ignore_errors=True)
SD = RUN / "sdcard"; LOG = RUN / "calls.log"
(SD / "Documents" / "Материалы Moodle" / "Курс 1").mkdir(parents=True)
(SD / "Android" / "data" / "ru.sevsu.moodleoffline" / "files" / "Материалы Moodle").mkdir(parents=True)
(SD / "Documents" / "Материалы Moodle" / "Курс 1" / "лекция.pdf").write_bytes(b"%PDF-1.4")
(RUN / "home").mkdir()
os.environ["FAKE_BRIDGE_LOG"] = str(LOG); os.environ["FAKE_SHARED"] = str(SD)
LOG.write_text("")
import java
import android
import gui

def calls(): return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
DOCS = SD / "Documents" / "Материалы Moodle"
PRIV = SD / "Android" / "data" / "ru.sevsu.moodleoffline" / "files" / "Материалы Moodle"

# ── android.py ──
os.environ["MOODLE_OFFLINE_APK"] = "1"
check("open_folder → Bridge.openFolder", android.open_folder(DOCS) is True and calls()[-1] == ["openFolder", str(DOCS)])
check("can_open_folder: папка в общей памяти — да", android.can_open_folder(DOCS) is True)
check("open_folder: нет такой папки — False", android.open_folder(SD / "Documents" / "нет") is False)
check("open_folder: вне общей памяти — False", android.open_folder(RUN / "home") is False)
check("can_open_folder: файл — нет", android.can_open_folder(DOCS / "Курс 1" / "лекция.pdf") is False)
del os.environ["MOODLE_OFFLINE_APK"]
LOG.write_text("")
check("в Termux (не .apk) open_folder = False, мост не трогается", android.open_folder(DOCS) is False and calls() == [])

# ── gui.open_path на Android ──
java.STATE["all_files"] = True
os.environ["MOODLE_OFFLINE_APK"] = "1"
gui.ANDROID = True
home_real = Path.home
Path.home = classmethod(lambda cls: RUN / "home")      # «домашняя» папка для проверки закрытой памяти
def err(fn):
    try: fn(); return ""
    except OSError as e: return str(e)
try:
    LOG.write_text("")
    check("папка в «Документах»: открывается в «Файлах»", err(lambda: gui.open_path(DOCS)) == "" and calls()[-1] == ["openFolder", str(DOCS)])
    LOG.write_text("")
    check("«Показать в папке» для файла: открывается папка файла", err(lambda: gui.open_path(DOCS / "Курс 1" / "лекция.pdf", reveal=True)) == ""
          and calls()[-1] == ["openFolder", str(DOCS / "Курс 1")], str(calls()))
    LOG.write_text("")
    check("обычный файл: открывается приложением, не папкой", err(lambda: gui.open_path(DOCS / "Курс 1" / "лекция.pdf")) == ""
          and [c[0] for c in calls()] == ["openFile"], str(calls()))
    LOG.write_text("")
    e = err(lambda: gui.open_path(PRIV))
    check("память приложения: понятный отказ, «Документы» в совете, мост не вызывался", "Документы" in e and "памяти приложения" in e and calls() == [], e)
    LOG.write_text("")
    java.Bridge.openFolder = staticmethod(lambda p: False)
    e = err(lambda: gui.open_path(DOCS))
    check("«Файлы» не открылись: ошибка с путём к папке", "«Файлы»" in e and str(DOCS) in e, e)
    del java.Bridge.openFolder
    os.environ.pop("MOODLE_OFFLINE_APK")
    e = err(lambda: gui.open_path(DOCS))
    check("Termux: папку не открываем, показываем путь", str(DOCS) in e and "«Файлы»" in e, e)
finally:
    Path.home = home_real
    gui.ANDROID = False

# ── окно: кнопка папки на телефоне ходит на сервер только в .apk ──
html = (_root.ROOT / "ui" / "index.html").read_text(encoding="utf-8")
check("окно: для .apk папка открывается через сервер", "async function androidOpen" in html and "IS_APK()" in html and 'apiRaw("/api/open", b)' in html)
check("окно: файлы по-прежнему открываются в самом окне", "window.open(fileUrl(p)" in html)
print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

import sys, os, json, time, shutil, zipfile, hashlib, threading, urllib.request, urllib.error
import _root
from pathlib import Path
import moodle_offline as mo
import transfer as T

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

class Rep(mo.Reporter):
    def __init__(self): super().__init__(); self.lines=[]
    def emit(self, msg, level): self.lines.append((level, msg))
rep = Rep(); ctl = mo.Control()

BASE = Path("/tmp/tr_test"); shutil.rmtree(BASE, ignore_errors=True)
pc_out, pc_data, ph_out, ph_data = (BASE/n for n in ("pc_out","pc_data","ph_out","ph_data"))
def w(p, data=b"x", m=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    if m: os.utime(p, (m, m))
M0 = 1_760_000_000
# --- ПК ---
w(pc_out/"Высшая математика"/"Лекции"/"Лекция 1.pdf", os.urandom(3_000_000), M0)       # большой (> SMALL)
w(pc_out/"Высшая математика"/"index.html", "<html>курс</html>", M0)
w(pc_out/"Основы ИБ"/"Объявления"/"a.txt", "привет", M0)
w(pc_out/"Расписание"/"index.html", "<html>rasp</html>", M0); w(pc_out/"Расписание"/"data"/"schedule.json", '{"x":1}', M0)
w(pc_out/"Календарь"/"moodle_calendar.ics", "BEGIN:VCALENDAR", M0); w(pc_out/".moodle_deadlines.json", '{"d":1}', M0)
w(pc_out/".moodle_news.json", '{"n":1}', M0)
w(pc_out/".moodle_grades.json", '{"updated":"x","courses":{"1":{"name":"Курс","grade":"5","rank":""}}}', M0)
w(pc_out/"Элективы"/"state.json", '{"e":1}', M0)
for secret_like in ("_журнал.log", ".moodle_state.json", ".moodle_search.sqlite", "_бэкапы/x.zip", "_не_скачано.txt", "Курс/файл.part", "Курс/tmp.tmp"):
    w(pc_out/secret_like, "SECRET-ish service")
w(pc_data/"offline.json", '{"user":"Иван","username":"user-login","courses":[1]}', M0); w(pc_data/"structure"/"5.json", '{"s":1}', M0); w(pc_data/"covers"/"5.png", b"png", M0)
w(pc_data/"sevsu_groups.json", '{"g":1}', M0)
w(pc_data/"session.bin", "COOKIE-SECRET"); w(pc_data/"notify.bin", "TELEGRAM-TOKEN"); w(pc_data/"settings.json", "{}")
SETTINGS = {"out_dir": "C:/Users/x/Материалы", "username": "student", "password": "pw!", "use_system_proxy": True,
            "schedule_group": "ib-b-25-2-o", "schedule_auto_range": "semester", "course_ids": [1, 2],
            "rules": {"skip": ["*.apk"]}, "news": {"enabled": True, "telegram": True, "telegram_chat": "123456", "webhook": True,
                                                   "track": {"files": True}},
            "remember": True, "notify_token": "abc"}

# 1. состав посылки
man = T.build_manifest(pc_out, pc_data, T.CAT_IDS, SETTINGS)
paths = {f["p"] for f in man["files"]}
check("в посылке нет пароля/сессии/токенов/служебных файлов",
      not any(x in p for p in paths for x in ("session.bin","notify.bin","settings.json",".moodle_state",".moodle_search","_журнал","_бэкапы","_не_скачано",".part",".tmp")), sorted(paths))
check("в посылке есть всё нужное", {"offline.json","structure/5.json","covers/5.png","sevsu_groups.json","Расписание/index.html","Расписание/data/schedule.json",
      "Календарь/moodle_calendar.ics",".moodle_deadlines.json",".moodle_news.json","Элективы/state.json","Высшая математика/Лекции/Лекция 1.pdf",
      "Высшая математика/index.html","Основы ИБ/Объявления/a.txt"} <= paths, sorted(paths))
check("оценки (.moodle_grades.json) переносятся вместе с курсами", ".moodle_grades.json" in paths, sorted(paths))
check(".moodle_grades.json не в посылке без вида «курсы»", ".moodle_grades.json" not in {f["p"] for f in T.build_manifest(pc_out, pc_data, ["schedule"])["files"]})
check("настройка save_grades переносится", T.safe_settings({"save_grades": False, "password": "x"}).get("save_grades") is False and "password" not in T.safe_settings({"save_grades": False, "password": "x"}))
st = man["settings"]
check("настройки: только разрешённые, без секретов", set(st) <= set(T.SETTINGS_KEYS) and "password" not in json.dumps(st) and "username" not in st
      and "out_dir" not in st and "telegram_chat" not in json.dumps(st) and "webhook" not in json.dumps(st) and st["news"]["track"] == {"files": True}, st)
check("дефолт — все категории", set(T.DEFAULT_CATS) == set(T.CAT_IDS) and len(T.DEFAULT_CATS) == 7)

# 2. архив → телефон
arc = BASE/"перенос.zip"
res = T.export_archive(arc, pc_out, pc_data, T.CAT_IDS, SETTINGS, rep, ctl)
check("архив создан", arc.exists() and res["files"] == len(man["files"]), res)
names = zipfile.ZipFile(arc).namelist()
check("в архиве только файлы из списка", set(names) == {"manifest.json"} | {f"{f['r']}/{f['p']}" for f in man["files"]}, names)
r = T.import_archive(arc, T.CAT_IDS, ph_out, ph_data, rep, ctl)
check("импорт: всё скопировано, ошибок нет", r["copied"] == len(man["files"]) and r["failed"] == 0 and r["bad"] == 0, r)
check("импорт: содержимое и даты совпали", (ph_out/"Высшая математика"/"Лекции"/"Лекция 1.pdf").read_bytes() == (pc_out/"Высшая математика"/"Лекции"/"Лекция 1.pdf").read_bytes()
      and int((ph_out/"Основы ИБ"/"Объявления"/"a.txt").stat().st_mtime) == M0 and json.loads((ph_data/"offline.json").read_text()) == {"user":"Иван","courses":[1]})
check("логин из offline.json не передаётся", "user-login" not in (ph_data/"offline.json").read_text() and "user-login" not in json.dumps(man))
check("импорт: секреты не появились", not (ph_data/"session.bin").exists() and not (ph_data/"notify.bin").exists() and not (ph_out/"_журнал.log").exists())
cur = {"out_dir": "/sdcard/Документы", "username": "kept"}
changed = T.apply_settings(r["settings"], cur)
check("настройки применены, свои пути/логин не тронуты", cur["out_dir"] == "/sdcard/Документы" and cur["username"] == "kept" and cur["schedule_group"] == "ib-b-25-2-o" and "password" not in cur, cur)
r2 = T.import_archive(arc, T.CAT_IDS, ph_out, ph_data, rep, ctl)
check("повторный импорт ничего не копирует", r2["copied"] == 0 and r2["failed"] == 0, r2)

# 3. «на телефоне новее» не затирается; force — затирает
w(ph_out/"Расписание"/"data"/"schedule.json", '{"x":2}', M0 + 5000)
w(pc_out/"Расписание"/"data"/"schedule.json", '{"x":3}', M0 + 10)           # у ПК изменился, но старее телефона
man_new = T.build_manifest(pc_out, pc_data, ["schedule"])
arc2 = BASE/"п2.zip"; T.export_archive(arc2, pc_out, pc_data, ["schedule"], {}, rep, ctl)
r3 = T.import_archive(arc2, ["schedule"], ph_out, ph_data, rep, ctl)
check("свежее на телефоне не затёрто", (ph_out/"Расписание"/"data"/"schedule.json").read_text() == '{"x":2}' and any("свежее" in n or "такие же" in n for n in r3["notes"]), r3)
r4 = T.import_archive(arc2, ["schedule"], ph_out, ph_data, rep, ctl, force=True)
check("force затирает", (ph_out/"Расписание"/"data"/"schedule.json").read_text() == '{"x":3}', r4)
w(pc_out/"Расписание"/"data"/"schedule.json", '{"x":4}', M0 + 99999)
arc3 = BASE/"п3.zip"; T.export_archive(arc3, pc_out, pc_data, ["schedule"], {}, rep, ctl)
r5 = T.import_archive(arc3, ["schedule"], ph_out, ph_data, rep, ctl)
check("свежее у ПК принято", (ph_out/"Расписание"/"data"/"schedule.json").read_text() == '{"x":4}', r5)

# 4. выбор видов: только материалы → расписание не трогается
shutil.rmtree(ph_out); shutil.rmtree(ph_data)
r6 = T.import_archive(arc, ["materials"], ph_out, ph_data, rep, ctl)
check("выбрали только материалы", (ph_out/"Основы ИБ"/"Объявления"/"a.txt").exists() and not (ph_out/"Расписание").exists() and r6["settings"] is None, r6)

# 5. злонамеренный архив: «..», абсолютные пути, чужие root, подделка размера
evil = BASE/"evil.zip"
manifest = {"format": 1, "app": mo.APP_NAME, "files": [
    {"c": "materials", "r": "out", "p": "../evil.txt", "s": 3, "m": M0, "h": ""},
    {"c": "materials", "r": "out", "p": "/etc/evil2", "s": 3, "m": M0, "h": ""},
    {"c": "materials", "r": "out", "p": "a/../../evil3", "s": 3, "m": M0, "h": ""},
    {"c": "materials", "r": "etc", "p": "evil4", "s": 3, "m": M0, "h": ""},
    {"c": "materials", "r": "out", "p": "ok/файл.txt", "s": 99, "m": M0, "h": ""},       # размер не совпадает
    {"c": "materials", "r": "out", "p": "good/файл.txt", "s": 3, "m": M0, "h": hashlib.sha256(b"abc").hexdigest()}]}
with zipfile.ZipFile(evil, "w") as z:
    z.writestr("manifest.json", json.dumps(manifest))
    for n in ("../evil.txt", "/etc/evil2", "out/../evil.txt", "out/ok/файл.txt"): z.writestr(n, "abc")
    z.writestr("out/good/файл.txt", "abc")
sand = BASE/"sand"; shutil.rmtree(sand, ignore_errors=True)
r7 = T.import_archive(evil, ["materials"], sand/"out", sand/"data", rep, ctl)
check("архив-ловушка: за пределы ничего не записано", not (BASE/"evil.txt").exists() and not (sand/"evil.txt").exists() and not Path("/etc/evil2").exists()
      and r7["bad"] == 4, r7)
check("архив-ловушка: честный файл принят, с неверным размером — ошибка", (sand/"out"/"good"/"файл.txt").exists() and r7["failed"] == 1 and not (sand/"out"/"ok"/"файл.txt").exists(), r7)
bad_zip = BASE/"bad.zip"; bad_zip.write_bytes(b"not a zip")
try: T.read_archive_manifest(bad_zip); check("не-архив отвергается", False)
except T.TransferError: check("не-архив отвергается", True)
foreign = BASE/"foreign.zip"
with zipfile.ZipFile(foreign, "w") as z: z.writestr("manifest.json", json.dumps({"app": "Other", "files": []}))
try: T.read_archive_manifest(foreign); check("чужой архив отвергается", False)
except T.TransferError: check("чужой архив отвергается", True)
newer = BASE/"newer.zip"
with zipfile.ZipFile(newer, "w") as z: z.writestr("manifest.json", json.dumps({"app": mo.APP_NAME, "format": 99, "files": []}))
try: T.read_archive_manifest(newer); check("архив новой версии — просим обновить программу", False)
except T.TransferError as e: check("архив новой версии — просим обновить программу", "обновите" in str(e))

# 6. место на диске
real_du = shutil.disk_usage
shutil.disk_usage = lambda p: shutil._ntuple_diskusage(100, 99, 1 << 20)
try:
    T.import_archive(arc, ["materials"], BASE/"full_out", BASE/"full_data", rep, ctl); check("нехватка места замечена", False)
except T.TransferError as e: check("нехватка места замечена", "не хватает места" in str(e), e)
finally: shutil.disk_usage = real_du

# 7. Wi-Fi
shutil.rmtree(ph_out, ignore_errors=True); shutil.rmtree(ph_data, ignore_errors=True)
snd = T.Sender(pc_out, pc_data, T.CAT_IDS, SETTINGS, rep, ttl=60, bind="127.0.0.1")
info = snd.start()
check("раздача запущена, код 8 символов", info["active"] and len(info["code"].replace("-", "")) == 8 and info["port"] > 0, info)
base, code = T.parse_address(f"127.0.0.1:{info['port']} {info['code']}")
check("адрес разобран (с пробелом и дефисом)", base.endswith(f":{info['port']}/{snd.code}"), base)
base2, _ = T.parse_address(f"http://127.0.0.1:{info['port']}/{snd.code}/")
check("адрес разобран (ссылкой)", base2 == base)
for bad in ("мусор", "127.0.0.1:8000", "127.0.0.1:8000/AB"):
    try: T.parse_address(bad); check("плохой адрес отвергнут: " + bad, False)
    except T.TransferError: check("плохой адрес отвергнут: " + bad, True)
try: T.parse_address("8.8.8.8:80 ABCDEFGH"); check("публичный адрес запрещён", False)
except T.TransferError as e: check("публичный адрес запрещён", "локальной сети" in str(e), e)
page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
check("страница для браузера телефона отдаётся", "archive.zip" in page and info["code"] in page)
r8 = T.pull(base, T.CAT_IDS, ph_out, ph_data, rep, ctl)
check("Wi-Fi: всё получено", r8["copied"] == len(man["files"]) and r8["failed"] == 0 and (ph_data/"offline.json").exists(), r8)
check("Wi-Fi: секретов нет, даты те же", not (ph_data/"session.bin").exists() and int((ph_out/".moodle_news.json").stat().st_mtime) == M0)
r9 = T.pull(base, T.CAT_IDS, ph_out, ph_data, rep, ctl)
check("Wi-Fi: второй раз — ничего не качает", r9["copied"] == 0 and r9["total"] == 0, r9)
# изменилось на ПК → докачивается только оно
w(pc_out/"Основы ИБ"/"Объявления"/"b.txt", "новое", M0 + 777)
w(pc_out/"Основы ИБ"/"Объявления"/"a.txt", "привет!!", M0 + 778)
snd2 = T.Sender(pc_out, pc_data, ["materials"], {}, rep, ttl=60, bind="127.0.0.1"); i2 = snd2.start()
b2, _ = T.parse_address(f"http://127.0.0.1:{i2['port']}/{snd2.code}/")
r10 = T.pull(b2, ["materials"], ph_out, ph_data, rep, ctl)
check("Wi-Fi: только новое и изменённое", r10["copied"] == 2 and (ph_out/"Основы ИБ"/"Объявления"/"a.txt").read_text() == "привет!!", r10)
# докачка большого файла с места обрыва
big = ph_out/"Высшая математика"/"Лекции"/"Лекция 1.pdf"; src_big = pc_out/"Высшая математика"/"Лекции"/"Лекция 1.pdf"
w(src_big, os.urandom(3_000_000), M0 + 5000)
part = big.with_name(big.name + ".part"); part.write_bytes(b"")
# оставим в .part настоящее начало нового файла
part.write_bytes(src_big.read_bytes()[:1_000_000])
snd2.stop(); snd2 = T.Sender(pc_out, pc_data, ["materials"], {}, rep, ttl=60, bind="127.0.0.1"); i2 = snd2.start()
b3, _ = T.parse_address(f"http://127.0.0.1:{i2['port']}/{snd2.code}/")
r11 = T.pull(b3, ["materials"], ph_out, ph_data, rep, ctl)
check("докачка с места обрыва даёт целый файл", big.read_bytes() == src_big.read_bytes() and not part.exists(), r11)
# архив по Wi-Fi целиком (как скачала бы его страница в браузере телефона)
data_zip = urllib.request.urlopen(base + "/archive.zip", timeout=30).read()
(BASE/"stream.zip").write_bytes(data_zip)
check("archive.zip по Wi-Fi открывается и читается", T.read_archive_manifest(BASE/"stream.zip")["app"] == mo.APP_NAME)
shutil.rmtree(sand, ignore_errors=True)
r12 = T.import_archive(BASE/"stream.zip", [c for c in T.DEFAULT_CATS if c != "materials"], sand/"out", sand/"data", rep, ctl)
check("архив, пришедший потоком, импортируется", r12["failed"] == 0 and (sand/"data"/"offline.json").exists(), r12)
# защита: неверный код, ограничение попыток, остановка, срок
def code_status(url):
    try: return urllib.request.urlopen(url, timeout=5).status
    except urllib.error.HTTPError as e: return e.code
    except OSError: return 0
snd3 = T.Sender(pc_out, pc_data, ["news"], {}, rep, ttl=60, bind="127.0.0.1"); i3 = snd3.start()
bad_url = f"http://127.0.0.1:{i3['port']}/ZZZZZZZZ/manifest.json"
codes = [code_status(bad_url) for _ in range(T.MAX_BAD_TRIES)]
check("неверный код → 404", codes[0] == 404, codes)
time.sleep(0.4)
check("после 10 неверных кодов раздача выключается даже для верного", code_status(f"http://127.0.0.1:{i3['port']}/{snd3.code}/manifest.json") in (403, 0) or snd3.locked)
try:
    T.fetch_manifest(f"http://127.0.0.1:{i3['port']}/{snd3.code}"); check("после блокировки не отдаёт", False)
except T.TransferError: check("после блокировки не отдаёт", True)
snd4 = T.Sender(pc_out, pc_data, ["news"], {}, rep, ttl=1, bind="127.0.0.1"); i4 = snd4.start()
time.sleep(1.5)
try:
    T.fetch_manifest(f"http://127.0.0.1:{i4['port']}/{snd4.code}"); check("после срока не отдаёт", False)
except T.TransferError: check("после срока не отдаёт", True)
check("is_lan: частные да, публичные нет", T.is_lan("192.168.1.5") and T.is_lan("10.1.2.3") and T.is_lan("172.20.0.1") and T.is_lan("127.0.0.1")
      and not T.is_lan("8.8.8.8") and not T.is_lan("93.184.216.34") and not T.is_lan("не-ip"))
check("local_ips не возвращает 127.*", not any(i.startswith("127.") for i in T.local_ips()))
snd.stop(); snd2.stop()
check("raз-код различимые символы", set(T.CODE_ALPHABET).isdisjoint("0O1IL"))
print("ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)

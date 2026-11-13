"""Проверки iotapi.py на макете (mock_iot.py на 8766). Запуск: python test_iot_unit.py"""
import sys, os, json, shutil, urllib.request, time
import _root
import moodle_offline as mo, iotapi
iotapi.SITE = "http://iot.sevsu.test:8766"; iotapi.AUTH = "http://auth.sevsu.test:8766"; iotapi.INSECURE_TEST = True
OK = BAD = 0
def chk(name, cond, extra=""):
    global OK, BAD
    if cond: OK += 1; print("  ok  ", name)
    else: BAD += 1; print("  FAIL", name, extra)
def ctl(path):
    return json.load(urllib.request.urlopen("http://iot.sevsu.test:8766" + path))
class Rep(mo.Reporter):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, msg, level): self.lines.append(msg)
rep = Rep()
def mk(**kw):
    return iotapi.Iot("/tmp/iot_out", "/tmp/iot_data", rep, mo.Control(), page_delay=(0, 0), file_delay=(0, 0), **kw)
shutil.rmtree("/tmp/iot_out", ignore_errors=True); shutil.rmtree("/tmp/iot_data", ignore_errors=True)
ctl("/__reset"); ctl("/__logout")

print("— вход и чтение")
a = mk(creds=("student", "pass123"))
st = a.refresh()
S = ctl("/__stats")
chk("вход паролем: 1 отправка формы, 1 обмен кода", S["login_posts"] == 1 and S["token_calls"] == 1, S)
chk("PKCE сошёлся (bad_pkce=0)", S["bad_pkce"] == 0, S)
chk("все запросы к API с Bearer", S["auth_header_seen"] == S["api_calls"] and S["api_calls"] > 5, S)
chk("блоков 6, первый — доступный выбор", len(st["blocks"]) == 6 and st["blocks"][0]["can_choose"])
b = st["blocks"][0]
chk("основных 1, резервов 2", (b["primary_count"], b["backup_count"]) == (1, 2))
chk("свободные места посчитаны", [o["left"] for o in b["options"]] == [3, 0, 20, 25, 0], [o["left"] for o in b["options"]])
chk("статус как на сайте", b["status_text"] == "Доступен выбор и его изменение")
done = next(x for x in st["blocks"] if x["slug"] == "vybor-inostrannogo-yazyka")
chk("итог распределения с названием", done["status"] == "done" and done["outcome"][0]["name"] == "Общая физическая подготовка", done["outcome"])
chk("ФИО из профиля", st["me"]["name"] == "Иванов Иван Иванович")
chk("занятия прочитаны (4) и отсортированы", len(st["events"]) == 4 and st["events"][0]["name"].endswith("кросс"))
chk("у занятия есть место, организатор, где", st["events"][0]["left"] == 18 and "Петров" in st["events"][0]["people"][0] and "Корпус Д" in st["events"][0]["where"])
chk("отменённое занятие помечено", st["events"][3]["cancelled"] and st["events"][3]["cancel_reason"] == "Болезнь преподавателя")
chk("файл iot.json на диске", os.path.exists("/tmp/iot_out/Элективы/iot.json") and json.load(open("/tmp/iot_out/Элективы/iot.json", encoding="utf-8"))["blocks"])
chk("в файле нет пароля и ключа", "pass123" not in open("/tmp/iot_out/Элективы/iot.json", encoding="utf-8").read() and "tok" not in open("/tmp/iot_out/Элективы/iot.json", encoding="utf-8").read().replace("token", ""))
chk("cookie единого входа получены (KC)", any(c["name"] == "KC" for c in a.cookies_out), a.cookies_out)
chk("в журнале нет пароля/ключа", not any("pass123" in l or "tok" in l.replace("token", "") for l in rep.lines))
chk("2-й вызов — ключ тот же, повторного входа нет", (a.refresh(), ctl("/__stats")["token_calls"])[1] == 1)

print("— вход по cookie без пароля")
kc = [c for c in a.cookies_out if c["name"] == "KC"]
b2 = mk(cookies=kc)
st2 = b2.refresh()
S = ctl("/__stats")
chk("без пароля: форма не отправлялась", S["login_posts"] == 1 and S["token_calls"] == 2, S)
chk("данные те же", len(st2["blocks"]) == 6)

print("— нет пароля и cookie")
ctl("/__logout")
c = mk()
try: c.refresh(); chk("нужен пароль → исключение", False)
except iotapi.IotNeedLogin: chk("нужен пароль → IotNeedLogin", True)
c = mk(creds=("student", "wrong"))
try: c.refresh(); chk("неверный пароль → ошибка", False)
except iotapi.IotError as e: chk("неверный пароль → понятная ошибка", "вход не удался" in str(e) and "Неверное имя" in str(e), str(e))
chk("пароль неверный не сохранён в debug", "wrong" not in open("/tmp/iot_data/iot_debug.txt", encoding="utf-8").read() if os.path.exists("/tmp/iot_data/iot_debug.txt") else True)
chk("iot_debug.txt без паролей/кодов", os.path.exists("/tmp/iot_data/iot_debug.txt") and "pass123" not in open("/tmp/iot_data/iot_debug.txt", encoding="utf-8").read())
dbg = open("/tmp/iot_data/iot_debug.txt", encoding="utf-8").read()
import re
chk("в debug нет значений code/state", not re.search(r"code=[A-Za-z0-9]{4,}", dbg) and "state=" not in dbg.replace("state=…", ""), dbg[:400])

print("— пароль только на доверенные адреса")
iotapi.INSECURE_TEST = False
d = mk(creds=("student", "pass123"))
try: d.refresh(); chk("http-адрес входа отвергнут", False)
except iotapi.IotError as e: chk("пароль на http не отправлен", "пароль туда не отправляю" in str(e), str(e))
iotapi.INSECURE_TEST = True
chk("пароль не ушёл на сервер (форм отправлено всего 2: вход и «wrong»)", ctl("/__stats")["login_posts"] == 2, ctl("/__stats"))

print("— истёкший ключ: 401 → новый вход")
ctl("/__logout")
e = mk(creds=("student", "pass123")); e.refresh()
ctl("/__expire")
st3 = e.refresh() if False else None
r = e.api("GET", "/profile")
chk("после /__expire запрос прошёл (новый ключ)", r[0]["lastName"] == "Иванов")

print("— сохранение выбора")
f = mk(creds=("student", "pass123")); st = f.refresh()
ids = {o["short"]: o["id"] for o in st["blocks"][0]["options"]}
bid = st["blocks"][0]["id"]
def err(fn, *a):
    try: fn(*a); return None
    except iotapi.IotError as ex: return str(ex)
chk("только основной без резервов — отказ до запроса", "резервных вариантов: 2" in (err(f.save_choice, bid, [ids["Волейбол"]], []) or ""))
chk("повтор варианта — отказ", "дважды" in (err(f.save_choice, bid, [ids["Волейбол"]], [ids["Волейбол"], ids["Футбол"]]) or ""))
chk("полный вариант: решает сайт, его ответ показан как есть", "сайт не принял выбор" in (err(f.save_choice, bid, [ids["Плавание"]], [ids["Волейбол"], ids["Футбол"]]) or "") and ctl("/__stats")["choice_posts"] == 1)
chk("закрытый набор: ответ сайта", "нет мест" in (err(f.save_choice, bid, [ids["Волейбол"]], [ids["Настольный теннис"], ids["Футбол"]]) or ""))
chk("два основных — отказ", "основных вариантов: 1" in (err(f.save_choice, bid, [ids["Волейбол"], ids["Футбол"]], [ids["Атлетическая гимнастика"]]) or ""))
chk("структурные отказы до запроса, на сайт ушли только 2 спорных", ctl("/__stats")["choice_posts"] == 2, ctl("/__stats"))
res = f.save_choice(bid, [ids["Волейбол"]], [ids["Атлетическая гимнастика"], ids["Футбол"]])
chk("выбор сохранён", res["ok"] and ctl("/__stats")["choice_posts"] == 3)
ch = res["state"]["blocks"][0]["choice"]
chk("после сохранения в состоянии выбор: основной + 2 резерва по порядку",
    [x["name"] for x in ch["primary"]] == ["Волейбол (юноши и девушки)"] and [x["name"] for x in ch["backup"]] == ["Атлетическая гимнастика", "Футбол"], ch)
ctl("/__setfull")
chk("место закончилось на сайте → понятная ошибка", "нет мест" in (err(f.save_choice, bid, [ids["Волейбол"]], [ids["Атлетическая гимнастика"], ids["Футбол"]]) or ""))
chk("блока нет → ошибка", "такого блока" in (err(f.save_choice, 99999, [1], []) or ""))

print("— занятия: запись и отписка")
evs = f.refresh()["events"]
ev0 = evs[0]
res = f.register(ev0["id"])
e0 = next(x for x in res["state"]["events"] if x["id"] == ev0["id"])
chk("записан", e0["reg_status"] == "registered" and e0["reg_text"] == "Записан" and e0["reg_id"], e0)
chk("мест стало на 1 меньше", e0["left"] == ev0["left"] - 1)
res = f.retract(e0["reg_id"])
e0 = next(x for x in res["state"]["events"] if x["id"] == ev0["id"])
chk("запись снята", not e0["reg_id"] and e0["can_register"])
chk("запись на занятие без мест → понятная ошибка", "сайт не принял запись" in (err(f.register, evs[1]["id"]) or ""))

print("— что нового")
g = mk(creds=("student", "pass123")); g.refresh()
ctl("/__opened")
st = g.refresh()
chk("новый блок попал в changes", any("Новый блок выбора: Новый блок выбора" in x for x in st["changes"]) and any("Открыт" not in x for x in st["changes"]), st["changes"])
st = g.refresh()
chk("повторное чтение без изменений — пусто", st["changes"] == [], st["changes"])

print("— разбор по мелочам")
n = datetime = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
chk("_dt Z", iotapi._dt("2026-11-12T07:00:00Z").hour == 7 and iotapi._dt("мусор") is None and iotapi._dt(None) is None)
chk("PKCE длины", len(iotapi._pkce()[0]) >= 43 and len(iotapi._pkce()[1]) == 43)
chk("_redirect_result: код во фрагменте", iotapi.Iot._redirect_result("http://iot.sevsu.test:8766/#state=s&code=abc", "http://iot.sevsu.test:8766/", "s") == ("code", "abc"))
chk("_redirect_result: чужой state", iotapi.Iot._redirect_result("http://iot.sevsu.test:8766/#state=x&code=abc", "http://iot.sevsu.test:8766/", "s")[0] == "error")
chk("_redirect_result: ошибка", iotapi.Iot._redirect_result("http://iot.sevsu.test:8766/?error=access_denied", "http://iot.sevsu.test:8766/", "s")[0] == "error")
chk("_redirect_result: чужой путь → None", iotapi.Iot._redirect_result("http://auth.sevsu.test:8766/x#code=1", "http://iot.sevsu.test:8766/", "s") is None)
print(f"\nИТОГО: {OK} ок, {BAD} ошибок")
sys.exit(1 if BAD else 0)

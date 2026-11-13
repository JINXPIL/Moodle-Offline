"""Календарь Moodle: записи «Посещаемость (Группа …)» — это время занятий из журнала посещаемости, а не сроки сдачи.
Они не должны попадать в «дедлайны», напоминания и .ics, пока их не включили отдельно. Без сети: клиент подставной."""
import sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path
import _root
import deadlines as dl
import moodle_offline as mo

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=dl.MSK).timestamp()
COURSE = {"id": 3594, "fullname": "Курс про сети", "shortname": "Сети"}

def ev(i, name, ts, etype, mod, action=True, dur=0):
    d = {"id": i, "name": name, "description": "", "modulename": mod, "eventtype": etype, "timestart": int(ts), "timesort": int(ts),
         "timeduration": dur, "course": COURSE, "normalisedeventtype": etype, "url": "https://do.sevsu.ru/calendar/view.php?view=day"}
    if action:
        d["action"] = {"name": "Добавить ответ на задание", "url": f"https://do.sevsu.ru/mod/{mod}/view.php?id={i}", "actionable": True}
    return d

TASK = ev(7001, "Практическая работа №5 должен быть выполнен", NOW + 2 * 86400, "due", "assign")
SES = [ev(7100 + k, "Посещаемость (Группа ИБ/6-25-2-о 1 подгруппа)", NOW + 3600 * k, "attendance", "attendance", action=False, dur=5400) for k in (22, 46, 70)]

class Rep:
    def __init__(self): self.lines = []
    def log(self, m, level="info"): self.lines.append((level, m))

class Client:
    """Подставной клиент: «График» (только события с действием) и месячный вид (все события)."""
    def __init__(self, timeline=True, month=None, boom=None):
        self.timeline, self.month, self.boom = timeline, month if month is not None else [TASK] + SES, boom
    def get_action_events(self, a, b):
        if not self.timeline: raise RuntimeError("Сервис недоступен")
        return [e for e in [TASK] + SES if e.get("action")], True
    def get_month_events(self, a, b): return list(self.month)

def run(client, cfg_calendar=None):
    out = Path(tempfile.mkdtemp(prefix="cal_"))
    settings = {"calendar": dict(cfg_calendar or {})}
    r = mo.calendar_sync(client, out, settings, Rep(), now=NOW)
    cfg = dl.normalize(settings["calendar"])
    store = dl.Store(out / dl.DEADLINES_FILE)
    return out, r, store, cfg

# 1. разбор
e = dl.from_ajax(SES[0])
check("запись «Посещаемость» — отдельный вид «занятия», не срок сдачи", e and e["kind"] == "sessions" and e["label"] != "срок сдачи", str(e and (e["kind"], e["label"])))
check("время и длительность занятия сохранены", e and e["due"] == SES[0]["timestart"] and e["end"] == SES[0]["timestart"] + 5400)
check("задание по-прежнему срок сдачи", dl.from_ajax(TASK)["kind"] == "deadline")
check("вид «занятия» есть среди видов событий, но по умолчанию выключен", "sessions" in dl.GROUPS and "sessions" not in dl.DEFAULTS["kinds"])
check("настройки принимают вид «занятия»", dl.normalize({"kinds": ["deadline", "sessions"]})["kinds"] == ["deadline", "sessions"])

# 2. «График» недоступен — читаем только месячный вид (так было бы и у вас, если сервис «График» отключат)
out, r, store, cfg = run(Client(timeline=False))
v = dl.view(store, [], cfg, NOW)
check("месячный вид: занятия не считаются сроками (остаётся одно задание)", r["ok"] and v["counts"]["total"] == 1, str(v["counts"]))
ics = (out / dl.CALENDAR_DIR / dl.ICS_FILE).read_text(encoding="utf-8") if (out / dl.CALENDAR_DIR / dl.ICS_FILE).exists() else ""
check("в .ics занятий из «Посещаемости» нет, задание есть", "Посещаемость" not in ics and "Практическая работа №5" in ics, ics[:200])
check("напоминания не придут про занятия", all("Посещаемость" not in (x.get("title", "") + x.get("text", "")) for x in dl.soon_events(store, cfg, NOW + 22 * 3600 - 100)))
check("пока вид «занятия» выключен, их не копим в хранилище", not any(x["kind"] == "sessions" for x in store.events.values()))

# 3. «График» работает — занятия из месячного вида тоже отдельно
out, r, store, cfg = run(Client(timeline=True))
v = dl.view(store, [], cfg, NOW)
check("«График» работает: сроков столько же (1), занятия не мешают", v["counts"]["total"] == 1, str(v["counts"]))

# 4. включили «занятия» — они появились в списке и в .ics
out, r, store, cfg = run(Client(timeline=True), {"kinds": ["deadline", "events", "sessions"]})
v = dl.view(store, [], cfg, NOW)
check("«занятия» включены: показаны вместе со сроком", v["counts"]["total"] == 4, str(v["counts"]))
ics = (out / dl.CALENDAR_DIR / dl.ICS_FILE).read_text(encoding="utf-8")
check("«занятия» включены: они в .ics", ics.count("Посещаемость") >= 3, str(ics.count("Посещаемость")))

# 5. вид включили, потом выключили, а на сайте занятия пропали: «убрано с сайта» про них не пишем
out = Path(tempfile.mkdtemp(prefix="cal_"))
st = {"calendar": {"kinds": ["deadline", "events", "sessions"]}}
mo.calendar_sync(Client(timeline=True), out, st, Rep(), now=NOW)
st = {"calendar": {}}
r = mo.calendar_sync(Client(timeline=True, month=[TASK]), out, st, Rep(), now=NOW + 60)
check("занятия пропали с сайта при выключенном виде — не «убрано с сайта»", r["ok"] and r["gone"] == 0, str(r))
st = {"calendar": {"kinds": ["deadline", "events", "sessions"]}}
r = mo.calendar_sync(Client(timeline=True, month=[TASK]), out, st, Rep(), now=NOW + 120)
check("а при включённом виде пропавшие занятия учитываются", r["ok"] and r["gone"] == 3, str(r))
# 6. включён только вид «занятия» (без «событий») — месяц всё равно читается
out, r, store, cfg = run(Client(timeline=True), {"kinds": ["deadline", "sessions"]})
check("включён только вид «занятия»: месячный вид читается, занятия на месте", sum(1 for x in store.events.values() if x["kind"] == "sessions") == 3, str(sorted({x["kind"] for x in store.events.values()})))

print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

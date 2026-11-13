"""Таймер проверки Moodle и автообновление: настройки, окна, упал/ожил, «пора ли», замок, сверка диска."""
import json, os, sys, tempfile, time
from datetime import datetime
from pathlib import Path
import _root
import watch as w, news
import moodle_offline as mo

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {extra}"))
    if not cond: FAILS.append(name)

# ── настройки ──
d = w.normalize(None)
check("по умолчанию: проверка включена, автообновление выключено", d["check"]["enabled"] and not d["auto"]["enabled"])
check("по умолчанию интервал 30 с, окно круглосуточно", d["check"]["interval"] == 30 and w.describe_window(d["check"]["window"]) == "каждый день, круглосуточно")
check("уведомление «ожил» вкл, «упал» выкл", d["check"]["notify_up"] and not d["check"]["notify_down"])
d = w.normalize({"check": {"interval": 5, "down_after": 99}, "auto": {"every": 1, "retry": 1, "scope": "zzz", "courses": ["3", 3, "x", 5, None]}})
check("интервал не быстрее 30 с, down_after ≤ 10", d["check"]["interval"] == 30 and d["check"]["down_after"] == 10)
check("every ≥ 15 мин, retry ≥ 5 мин, неизвестный scope → all", d["auto"]["every"] == 15 and d["auto"]["retry"] == 5 and d["auto"]["scope"] == "all")
check("список курсов: числа, без повторов и мусора", d["auto"]["courses"] == [3, 5], str(d["auto"]["courses"]))
check("слишком большой интервал обрезается", w.normalize({"check": {"interval": 10**9}})["check"]["interval"] == w.MAX_INTERVAL)
check("мусор вместо настроек не ломает", w.normalize("строка") == w.normalize({}) and w.normalize({"check": 5, "auto": [1]}) == w.normalize({}))
win = w.norm_window({"days": ["1", 2, 9, -1, "x"], "from": "8:5", "to": "24:00"})
check("окно: дни в 0..6, время HH:MM, 24:00 для конца", win == {"days": [1, 2], "from": "08:05", "to": "24:00"}, str(win))
check("окно: 24:00 как начало не принимается", w.norm_window({"from": "24:00"})["from"] == "00:00")
check("окно: одинаковые границы = весь день", w.norm_window({"from": "08:00", "to": "08:00"}) == {"days": list(range(7)), "from": "00:00", "to": "24:00"})
check("окно: пустые дни = все дни", w.norm_window({"days": []})["days"] == list(range(7)))
check("normalize идемпотентна", w.normalize(w.normalize({"check": {"interval": 120, "window": {"days": [0, 1], "from": "09:00", "to": "18:00"}}}))["check"]["interval"] == 120)

# ── in_window / next_open ──
def dt(s): return datetime.strptime(s, "%Y-%m-%d %H:%M")      # 2026-11-09 — понедельник
mon, tue, sat = "2026-11-09", "2026-11-10", "2026-11-14"
day = {"days": [0, 1, 2, 3, 4], "from": "09:00", "to": "18:00"}
check("в окне: будний день, 12:00", w.in_window(day, dt(f"{mon} 12:00")))
check("граница: начало включено, конец нет", w.in_window(day, dt(f"{mon} 09:00")) and not w.in_window(day, dt(f"{mon} 18:00")))
check("вне окна: суббота", not w.in_window(day, dt(f"{sat} 12:00")))
night = {"days": [0], "from": "22:00", "to": "06:00"}      # только ночь с понедельника на вторник
check("ночное окно: пн 23:00 — да", w.in_window(night, dt(f"{mon} 23:00")))
check("ночное окно: вт 03:00 — да (хвост понедельника)", w.in_window(night, dt(f"{tue} 03:00")))
check("ночное окно: пн 03:00 — нет (это хвост воскресенья)", not w.in_window(night, dt(f"{mon} 03:00")))
check("ночное окно: вт 23:00 — нет", not w.in_window(night, dt(f"{tue} 23:00")))
check("next_open внутри окна = сейчас", w.next_open(day, dt(f"{mon} 10:00")) == dt(f"{mon} 10:00"))
check("next_open в субботу = понедельник 09:00", w.next_open(day, dt(f"{sat} 12:00")) == dt("2026-11-16 09:00"))
check("next_open вечером = завтра 09:00", w.next_open(day, dt(f"{mon} 20:00")) == dt(f"{tue} 09:00"))
check("next_open для ночного окна", w.next_open(night, dt(f"{mon} 12:00")) == dt(f"{mon} 22:00"))
check("describe_window", w.describe_window(day) == "по будням, с 09:00 до 18:00" and w.describe_window({"days": [5, 6], "from": "00:00", "to": "24:00"}) == "по выходным, круглосуточно"
      and w.describe_window({"days": [0, 2], "from": "10:00", "to": "11:00"}) == "пн, ср, с 10:00 до 11:00")
check("describe_interval / every", w.describe_interval(30) == "30 с" and w.describe_interval(300) == "5 мин" and w.describe_interval(10800) == "3 ч"
      and w.describe_every(30) == "30 мин" and w.describe_every(360) == "6 ч" and w.describe_every(1440) == "1 дн." and w.describe_every(90) == "1.5 ч")

# ── упал / ожил ──
t = w.Tracker(2)
check("одиночный сбой — не падение", t.feed("down", 100) == [] and t.status == "unknown")
check("ok после одиночного сбоя — тихо", t.feed("ok", 130) == [] and t.status == "ok")
t = w.Tracker(2)
t.feed("down", 100)
ev = t.feed("down", 130)
check("два сбоя подряд — «упал» с началом с первого сбоя", ev == [{"type": "down", "since": 100}] and t.status == "down", str(ev))
check("третий сбой — повторно не объявляем", t.feed("down", 160) == [])
ev = t.feed("slow", 700)
check("«медленно» тоже считается живым → «ожил» с длительностью", ev and ev[0]["type"] == "up" and ev[0]["outage"] == 600 and t.status == "slow", str(ev))
check("после «ожил» дальше тихо", t.feed("ok", 730) == [])
t = w.Tracker(1)
check("down_after=1: падение сразу", t.feed("down", 1) and t.status == "down")
check("fmt_outage", w.fmt_outage(45) == "45 с" and w.fmt_outage(600) == "10 мин" and w.fmt_outage(7260) == "2 ч 01 мин" and w.fmt_outage(7200) == "2 ч")

# ── события ленты ──
evs = [{"type": "down", "since": 1000.0}, {"type": "up", "outage": 700.0, "since": 1000.0}]
m = w.make_events(evs, "do.sevsu.ru", True, True, 0)
check("события: два вида, id стабилен", [e["kind"] for e in m] == ["moodle_down", "moodle_up"] and m[0]["id"] == w.make_events(evs, "do.sevsu.ru", True, True, 5)[0]["id"])
check("события: тексты", m[1]["title"] == "do.sevsu.ru" and "не работал 12 мин" in m[1]["detail"], str(m[1]))
check("события: только «ожил» по умолчанию", [e["kind"] for e in w.make_events(evs, "h", False, True, 0)] == ["moodle_up"])
check("события: всё выключено — пусто", w.make_events(evs, "h", False, False, 0) == [])
check("виды есть в news.KINDS и в настройках по умолчанию", "moodle_up" in news.KINDS and news.normalize({})["track"]["moodle_down"] and news.normalize({})["push"]["moodle_up"])
check("текст уведомления", "Moodle снова работает: do.sevsu.ru — не работал 12 мин" in news.event_line(m[1]), news.event_line(m[1]))

# ── автообновление: пора ли ──
A = w.normalize({"auto": {"enabled": True, "every": 60, "retry": 30}})["auto"]
now = dt(f"{mon} 12:00")
def due(cfg=A, st=None, now=now, **kw):
    kw.setdefault("moodle_ok", True); kw.setdefault("busy", False)
    return w.auto_due(cfg, st or {}, now, **kw)
check("выключено → нет", due(w.normalize({})["auto"])[0] is False and due(w.normalize({})["auto"])[1] == "выключено")
check("первый запуск → да", due() == (True, "первый запуск"))
check("занято → нет", due(busy=True) == (False, "идёт другая работа"))
check("нет входа → нет", due(can_login=False)[0] is False and "нужен вход" in due(can_login=False)[1])
check("Moodle лежит → нет", due(moodle_ok=False) == (False, "Moodle не отвечает"))
check("Moodle неизвестен (None) — не блокирует", due(moodle_ok=None)[0] is True)
st = {"last_attempt": now.timestamp() - 30 * 60}
check("прошло 30 мин из 60 → рано", due(st=st)[0] is False and "ещё рано" in due(st=st)[1])
st = {"last_attempt": now.timestamp() - 61 * 60}
check("прошло 61 мин → пора", due(st=st) == (True, "по таймеру"))
st = {"last_attempt": now.timestamp() - 31 * 60, "last_failed": True}
check("после неудачи повтор через retry (30 мин)", due(st=st)[0] is True)
st = {"last_attempt": now.timestamp() - 20 * 60, "last_failed": True}
check("после неудачи раньше retry — рано", due(st=st)[0] is False)
Aw = w.normalize({"auto": {"enabled": True, "window": {"days": [0], "from": "22:00", "to": "23:00"}}})["auto"]
r = due(Aw)
check("вне окна → нет, с подсказкой когда откроется", r[0] is False and "вне разрешённого" in r[1] and "09.11 22:00" in r[1], str(r))
check("в окне → да", due(Aw, now=dt(f"{mon} 22:30"))[0] is True)
na = w.next_auto(A, {"last_attempt": now.timestamp() - 10 * 60}, now)
check("next_auto = последний запуск + every", na == datetime.fromtimestamp(now.timestamp() - 600 + 3600), str(na))
check("next_auto выключено → None, никогда не запускалось → сейчас", w.next_auto(w.normalize({})["auto"], {}, now) is None and w.next_auto(A, {}, now) == now)
cfg = w.normalize({"auto": {"enabled": True}})
s = w.summary(cfg, {"auto": {}, "check": {}}, now)
check("summary: подписи", "каждые 30 с" in s["check"] and "раз в 6 ч" in s["auto"] and "следующее" in s["auto"], str(s))
check("summary: всё выключено", "выключена" in w.summary(w.normalize({"check": {"enabled": False}}), {"auto": {}, "check": {}}, now)["check"])

# ── состояние и замок ──
tmp = Path(tempfile.mkdtemp(prefix="wt_"))
w.record_attempt(tmp, 1000.0)
st = w.load_state(tmp)
check("record_attempt", st["auto"]["last_attempt"] == 1000.0 and st["auto"]["running"] is True)
w.record_result(tmp, False, "ошибка", 1100.0)
st = w.load_state(tmp)
check("record_result: неудача", st["auto"]["last_failed"] is True and "last_ok" not in st["auto"] and st["auto"]["running"] is False and st["auto"]["runs"] == 1)
w.record_result(tmp, True, "ок", 1200.0)
st = w.load_state(tmp)
check("record_result: успех", st["auto"]["last_failed"] is False and st["auto"]["last_ok"] == 1200.0 and st["auto"]["runs"] == 2 and st["auto"]["last_text"] == "ок")
w.record_check(tmp, "ok", 50.0); w.record_check(tmp, "ok", 80.0); w.record_check(tmp, "down", 90.0)
c = w.load_state(tmp)["check"]
check("record_check: since меняется только при смене статуса", c["status"] == "down" and c["since"] == 90.0 and c["last"] == 90.0)
(tmp / w.WATCH_FILE).write_text("{битый", encoding="utf-8")
check("битый watch.json → чистое состояние", w.load_state(tmp) == {"auto": {}, "check": {}})
a, b = w.Lock(tmp, "окно"), w.Lock(tmp, "консоль")
check("замок: первый берёт", a.acquire(1000.0) is True)
check("замок: второй не может", b.acquire(1010.0) is False and b.holder()["who"] == "окно")
check("замок: хозяин обновляет", a.acquire(1050.0) is True)
check("замок: брошенный (старше LOCK_STALE) забирается", b.acquire(1050.0 + w.LOCK_STALE + 1) is True and b.holder()["who"] == "консоль")
check("замок: release снимает только свой", (a.release(), b.holder() is not None)[1] and (b.release(), b.holder() is None)[1])

# ── сверка диска с памятью программы ──
out = Path(tempfile.mkdtemp(prefix="au_"))
def mk(rel, data=b"x"):
    p = out / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p
a1 = mk("Курс1/a.pdf"); a2 = mk("Курс1/b.pdf"); b1 = mk("Курс2/c.docx")
state = {"version": 1, "files": {}, "courses": {"1": {"name": "Курс 1", "dir": "Курс1"}, "2": {"name": "Курс 2", "dir": "Курс2"}}, "news": {},
         "items": {
            "1:1:it1": {"complete": True, "files": [{"path": "a.pdf"}, {"path": "b.pdf"}]},
            "1:1:it2": {"complete": True, "files": []},
            "1:2:it3": {"complete": True, "files": [{"path": "c.docx"}]},
            "2:1:other": {"complete": True, "files": [{"path": "нет/в/режиме2"}]}}}
(out / mo.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
a = mo.audit_downloads(out, None, mode=1)
check("аудит: всё на месте", a["has_state"] and a["items"] == 3 and a["files"] == 3 and a["missing_files"] == 0 and not a["empty"], str(a))
state["items"]["1:9:nodir"] = {"complete": True, "files": [{"path": "zzz.pdf"}]}
(out / mo.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
a = mo.audit_downloads(out, None, mode=1)
check("аудит: курс без записанной папки (не дошёл до конца) пропускается", "1:9:nodir" not in a["missing_items"] and a["items"] == 3, str(a))
del state["items"]["1:9:nodir"]
(out / mo.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
check("аудит: другой режим раскладки не считается", "other" not in str(a["missing_items"]))
a2.unlink()
a = mo.audit_downloads(out, None, mode=1)
check("аудит: пропавший файл найден", a["missing_files"] == 1 and a["missing_items"] == ["1:1:it1"] and a["courses"]["1"]["missing_files"] == 1, str(a))
check("аудит по выбранным курсам", mo.audit_downloads(out, [2], mode=1)["missing_files"] == 0 and mo.audit_downloads(out, [1], mode=1)["missing_files"] == 1)
n = mo.invalidate_missing(out, a)
st2 = json.loads((out / mo.STATE_FILE).read_text(encoding="utf-8"))
check("невыполненные элементы помечены, остальные нет", n == 1 and st2["items"]["1:1:it1"]["complete"] is False and st2["items"]["1:2:it3"]["complete"] is True)
check("invalidate без пропавших ничего не трогает", mo.invalidate_missing(out, {"missing_items": []}) == 0)
a = mo.audit_downloads(out / "нет_такой", None, 1)
check("нет памяти программы → empty", a["has_state"] is False and a["empty"] is True)
b1.unlink(); a1.unlink()
a = mo.audit_downloads(out, None, mode=1)
check("файлов нет вообще: все пропали, но память есть", a["files"] == 3 and a["missing_files"] == 3)
(out / mo.STATE_FILE).write_text(json.dumps({**state, "items": {"1:1:x": {"complete": True, "files": []}}}), encoding="utf-8")
check("в памяти элементы без файлов → empty (скачивать всё)", mo.audit_downloads(out, [1], 1)["empty"] is True)

class Cr:  # курс-заглушка
    def __init__(self, i, past=False, hidden=False): self.id, self.past, self.hidden = i, past, hidden
cs = [Cr(1), Cr(2, past=True), Cr(3, hidden=True), Cr(4)]
acf = lambda **k: w.normalize({"auto": k})["auto"]
check("курсы: все (без прошлых и скрытых)", mo.pick_course_ids(acf(scope="all"), cs, {}) == [1, 4])
check("курсы: все + прошлые", mo.pick_course_ids(acf(scope="all", include_past=True), cs, {}) == [1, 2, 4])
check("курсы: выбранные", mo.pick_course_ids(acf(scope="selected", courses=[4, 9]), cs, {}) == [4, 9])
check("курсы: только скачанные", mo.pick_course_ids(acf(scope="existing"), cs, {"1": {}, "7": {}, "abc": {}}) == [1, 7])

class Rep:
    def __init__(self): self.lines = []
    def log(self, m, l="info"): self.lines.append(m)
rp = Rep()
(out / mo.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
mk("Курс1/a.pdf"); mk("Курс2/c.docx")                       # b.pdf всё ещё нет
mo.prepare_auto_update(out, [1, 2], 1, rp)
check("подготовка: пишет «не хватает», помечает к докачке", any("не хватает 1" in l for l in rp.lines), str(rp.lines))
rp = Rep(); (out / "Курс1" / "b.pdf").write_bytes(b"x"); (out / mo.STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
mo.prepare_auto_update(out, [1, 2], 1, rp)
check("подготовка: всё на месте", any("всё скачанное на месте" in l for l in rp.lines), str(rp.lines))
rp = Rep(); mo.prepare_auto_update(out / "пусто", [1], 1, rp)
check("подготовка: пусто → скачаю всё", any("скачаю всё выбранное" in l for l in rp.lines), str(rp.lines))

print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}")
sys.exit(1 if FAILS else 0)

import sys, time
from types import SimpleNamespace as NS
import _root
import gui

ok = bad = 0
def check(name, cond, extra=""):
    global ok, bad
    if cond: ok += 1; print("PASS", name)
    else: bad += 1; print("FAIL", name, extra)

app = NS(job_state="running", job_hint="running", set_net=lambda *a: None)
rep = gui.GuiReporter(app)
C = lambda i, n: NS(id=i, fullname=n)
courses = [C(1, "Курс А"), C(2, "Курс Б"), C(3, "Курс В"), C(4, "Курс Г")]
rep.reset(); rep.plan(courses)
snap = rep.snapshot(0)
check("до начала: очередь из 4 курсов, процента ещё нет", len(snap["plan"]) == 4 and all(p["state"] == "wait" for p in snap["plan"]) and snap["overall"] is None)
rep.course(1, 4, courses[0])
snap = rep.snapshot(0)
check("первый курс «сейчас», остальные ждут", [p["state"] for p in snap["plan"]] == ["now", "wait", "wait", "wait"])
check("процент в начале курса = 0", snap["overall"]["pct"] == 0 and snap["overall"]["cn"] == 4 and snap["overall"]["ci"] == 1)
sec = NS(name="Раздел")
rep.item(2, 4, sec, NS(name="Лекция", kind="Файл"))        # второй элемент из четырёх → четверть курса пройдена
snap = rep.snapshot(0)
check("процент: 1 курс из 4 × 1/4 элементов = 6.2%", abs(snap["overall"]["pct"] - 6.2) < 0.1, snap["overall"])
rep.file_start("a.pdf", 1000, 0)
rep.file_progress(500)
snap = rep.snapshot(0)
check("процент растёт вместе с файлом (0.25 + 0.5/4 → 9.4%)", abs(snap["overall"]["pct"] - 9.4) < 0.1, snap["overall"])
check("скачанные байты копятся", snap["overall"]["bytes"] == 500)
rep.file_progress(1000); rep.saved("Курс А/a.pdf", "a.pdf", 1000, "new"); rep.file_end("new")
check("байты не считаются дважды", rep.snapshot(0)["overall"]["bytes"] == 1000)
# скорость: два замера с интервалом
rep.file_start("b.bin", 10_000_000, 0)
rep.samples.clear(); rep.tot_bytes = 0
t0 = time.monotonic()
rep.samples.append((t0 - 4.0, 0)); rep.tot_bytes = 4_000_000; rep.samples.append((t0, 4_000_000))
sp = rep.overall()["speed"]
check("скорость за последние секунды ≈ 1 МБ/с", 0.8e6 < sp < 1.3e6, sp)
rep.file_end("new")
rep.saved("Курс А/b.html", "b.html", 2000, "page"); rep.saved("Курс А/c.pdf", "c.pdf", 0, "skipped"); rep.saved("Курс А/d.pdf", "d.pdf", 5, "copied")
rep.failed({"item": "x", "error": "404", "url": "u", "course": "Курс А"})
pa = rep.snapshot(0)["plan"][0]
check("счётчики курса: новых 2 (3000 байт), было 2, ошибок 1", (pa["new"], pa["bytes"], pa["skip"], pa["fail"]) == (2, 3000, 2, 1), pa)
rep.course(2, 4, courses[1])
st = [p["state"] for p in rep.snapshot(0)["plan"]]
check("следующий курс: предыдущий — «готово»", st == ["done", "now", "wait", "wait"], st)
rep.saved("Курс Б/z.pdf", "z.pdf", 10, "new")
by = {}
for d in rep.snapshot(0)["downloads"]: by.setdefault(d["cid"], []).append(d["title"])
check("у загрузки есть id курса — по нему окно группирует", by.get(1) and by.get(2) == ["z.pdf"], by)
# окно просит только новое
last = rep.dseq
rep.saved("Курс Б/y.pdf", "y.pdf", 10, "new")
s2 = rep.snapshot(0, last)
check("snapshot(dsince): только новые загрузки", [d["title"] for d in s2["downloads"]] == ["y.pdf"] and s2["dseq"] == rep.dseq, s2["downloads"])
check("snapshot без dsince: последние (как раньше)", len(rep.snapshot(0)["downloads"]) >= 5)
for i in range(300): rep.saved(f"Курс Б/f{i}.pdf", f"f{i}.pdf", 1, "new")
check("без dsince — не больше 150 (быстрый первый ответ)", len(rep.snapshot(0)["downloads"]) == 150)
check("с dsince — все новые, а не 150", len(rep.snapshot(0, last)["downloads"]) == 301)
app.job_state = "idle"
st = [p["state"] for p in rep.snapshot(0)["plan"]]
check("после окончания «сейчас» превращается в «готово»", st[1] == "done" and st[2] == "wait", st)
rep.reset()
check("reset очищает очередь, байты и процент", rep.snapshot(0)["plan"] == [] and rep.snapshot(0)["overall"] is None and rep.tot_bytes == 0)
print(f"\nИтого: {ok} PASS, {bad} FAIL"); sys.exit(1 if bad else 0)

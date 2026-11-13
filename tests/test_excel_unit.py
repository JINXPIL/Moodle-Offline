import sys, json, tempfile, shutil
from pathlib import Path
import _root
import xlsx_schedule as xs
import schedule as sch
import moodle_offline as mo
import mkxlsx

ok = bad = 0
def check(name, cond, extra=""):
    global ok, bad
    if cond: ok += 1; print("PASS", name)
    else: bad += 1; print("FAIL", name, extra)

ME, OTHER = "ИБ/б-25-2-о", "ИИ/б-25-1-о"
L1 = "Высшая математика, Сидорова Анна Петровна (внутр. совм.) (I-Г-303)\n"
days = [("26.10.2026", {
    2: {ME: ("whole", "Теория информации, Кузнецова Мария Ивановна (I-В-411)\n", "Л\n", "I-В-411\n")},
    3: {ME: ("both", "подгр.:1(из 2),Мат. логика, Маслова Мария Александровна\n", "ПЗ\n", "I-Г-507\n",
                       "подгр.:2(из 2),Теория информации, Лагуткина Татьяна Владимировна\n", "ЛЗ\n", "I-В-503\n")},
    4: {ME: ("s2", "Защита данных. Часть 1, Егорова Анастасия Олеговна (II-2.7)(Уч.гр. ЗПД/б-25-2-о)\n", "ЛЗ\n", "Д\n")},
    5: {ME: ("whole", "Веб, Дрозин Андрей Юрьевич (I-422)(Уч.гр. ВДиР1/б-25-3-о)\nIT-решения, Виноградов Евгений Викторович (внеш.совм.) (I-416)(Уч.гр. IT-РБ1/б-25-2-о)\n", "Л\nЛЗ\n", "I-422\nI-416\n")},
    6: {OTHER: ("whole", "Чужая пара, Иванов Иван Иванович (I-101)", "Л", "I-101")},
}), ("27.10.2026", {1: {ME: ("whole", "Физика, Петров П. П. (I-А-101)", "Л", "I-А-101")}})]
data = mkxlsx.build([ME, OTHER], [("уч.н. 9", days)])

les, info = xs.parse_workbook(data, "ib-b-25-2-o")
check("группа найдена по коду из адреса", info["group"] == ME and info["count"] == len(les), str(info))
by = {(l["date"], l["pair"], l["subject"][:8]): l for l in les}
l = by[("2026-10-26", 2, "Теория и")]
check("целая группа: тип, аудитория, преподаватель, время", (l["type"], l["room"], l["teacher"], l["start"], l["end"], l["subgroup"]) ==
      ("Лекция", "I-В-411", "Кузнецова Мария Ивановна", "10:10", "11:40", ""), str(l))
check("адрес корпуса по справке", l["address"].startswith("Учебный корпус по ул. Университетская, 33"))
l = by[("2026-10-26", 3, "Мат. лог")]
check("подгруппа 1: префикс «подгр.» убран", (l["subgroup"], l["subject"], l["type"], l["room"]) == ("1", "Мат. логика", "Практика (ПЗ)", "I-Г-507"), str(l))
l = by[("2026-10-26", 3, "Теория и")]
check("подгруппа 2 в той же строке", (l["subgroup"], l["type"], l["room"]) == ("2", "Лабораторная (ЛЗ)", "I-В-503"), str(l))
l = by[("2026-10-26", 4, "Защита д")]
check("«Д» → Дистанционно, адрес пуст; поток из «Уч.гр.»", (l["room"], l["address"], l["stream"], l["subject"]) == ("Дистанционно", "", "ЗПД/б-25-2-о", "Защита данных. Часть 1"), str(l))
multi = [x for x in les if x["pair"] == 5]
check("несколько занятий в одной ячейке → отдельные пары со своими типами и аудиториями",
      len(multi) == 2 and {(m["stream"], m["type"], m["room"]) for m in multi} == {("ВДиР1/б-25-3-о", "Лекция", "I-422"), ("IT-РБ1/б-25-2-о", "Лабораторная (ЛЗ)", "I-416")}, str(multi))
check("преподаватель с инициалами", by[("2026-10-27", 1, "Физика")]["teacher"] == "Петров П. П.")
check("чужая группа не попала", not any("Чужая" in x["subject"] for x in les))
try:
    xs.parse_workbook(data, "ИБ/б-25-9-о"); check("нет группы → понятная ошибка", False)
except xs.XlsxError as e:
    check("нет группы → понятная ошибка со списком групп", "ИБ/б-25-2-о" in str(e) and "ИИ/б-25-1-о" in str(e), str(e))
try:
    xs.parse_workbook(b"not a zip", ME); check("не xlsx", False)
except xs.XlsxError as e:
    check("не xlsx → понятная ошибка", "не файл Excel" in str(e), str(e))
check("ключи групп", xs.group_key("ИБ/б-25-2о") == xs.group_key("ib-b-25-2-o") == xs.group_key("Группа : ИБ/б-25-2-о"))

# ── ссылки на странице ──
ACC = """<div class="acc"><div class="item"><div class="head">Институт информационных технологий</div><div class="body">
<div class="sub"><div class="head">I семестр</div><div class="body"><ul>
<li><a href="/upload/fit_25_b_o11.11.xlsx">2 курс Бакалавриат</a></li><li><a href="/upload/fit_24_b_o09.11.xlsx">3 курс Бакалавриат</a></li></ul></div></div>
<div class="sub"><div class="head">II семестр</div><div class="body"><ul><li><a href="/upload/fit_25_b_o_s2.xlsx">2 курс Бакалавриат</a></li></ul></div></div></div></div>
<div class="item"><div class="head">Факультет Б</div><div class="body"><div class="sub"><div class="head">I семестр</div><div class="body"><ul>
<li><a href="https://www.sevsu.ru/upload/fb_25_b.xlsx">2 курс Бакалавриат</a></li></ul></div></div></div></div></div>"""
links = sch.find_excel_links(ACC, "https://www.sevsu.ru/univers/shedule/")
check("аккордеон: найдены все 4 ссылки", len(links) == 4, str(links))
a = links[0]
check("аккордеон: заголовки сверху вниз, имя файла, абсолютный адрес", a["file"] == "fit_25_b_o11.11.xlsx" and a["url"] == "https://www.sevsu.ru/upload/fit_25_b_o11.11.xlsx"
      and a["ctx"] == ["Институт информационных технологий", "I семестр"], str(a))
rk = sch.rank_excel(links, "ib-b-25-2-o", sch.date(2026, 11, 11))
check("подбор: осенью первым идёт «2 курс Бакалавриат, I семестр»", rk[0]["file"] in ("fit_25_b_o11.11.xlsx", "fb_25_b.xlsx") and rk[0]["score"] > rk[-1]["score"], str([(x["file"], x["score"]) for x in rk]))
check("подбор: «3 курс» ниже «2 курса»", [x["file"] for x in rk].index("fit_24_b_o09.11.xlsx") >= 2)
check("курс по группе", sch.excel_course("ИБ/б-25-2-о", sch.date(2026, 11, 11)) == (2, "бакалавр") and sch.excel_course("ИБ/б-25-2-о", sch.date(2027, 3, 1)) == (2, "бакалавр"))
sig = sch.link_sig(a)
new = [dict(x, url=x["url"].replace("11.11", "25.11"), file=x["file"].replace("11.11", "25.11")) for x in links]
m = sch.match_sig(new, sig)
check("выбор находится снова, даже когда в имени файла другая дата", m and m["file"] == "fit_25_b_o25.11.xlsx", str(m))
TAB = """<table><tr><th>Курс</th><th>Учебные занятия ОФО/ОЗФО</th><th>Сессия ЗФО</th></tr>
<tr><td>2 курс Бакалавриат</td><td><a href="a.xlsx">XLSX</a></td><td><a href="s.xlsx">XLSX</a></td></tr></table>"""
t = sch.find_excel_links(TAB, "https://www.sevsu.ru/univers/shedule/")
check("таблица: название строки и столбца", len(t) == 2 and t[0]["text"] == "2 курс Бакалавриат" and "Учебные" in t[0]["col"] and "Сессия" in t[1]["col"], str(t))
check("таблица: сессия ниже учебных занятий", sch.rank_excel(t, "ib-b-25-2-o")[0]["file"] == "a.xlsx")
FLAT = '<h3>Институт</h3><h4>I семестр</h4><ul><li><a href="x.xls">2 курс Бакалавриат</a></li></ul>'
f = sch.find_excel_links(FLAT, "https://www.sevsu.ru/p/")
check("плоская страница: заголовки", f and f[0]["ctx"] == ["Институт", "I семестр"] and f[0]["url"] == "https://www.sevsu.ru/p/x.xls", str(f))
check("не ссылки на Excel игнорируются", sch.find_excel_links('<a href="a.pdf">x</a><a href="b.xlsx?x=1">y</a>', "https://www.sevsu.ru/")[0]["file"] == "b.xlsx")
check("только sevsu.ru по https", sch.is_sevsu_url("https://www.sevsu.ru/a.xlsx") and sch.is_sevsu_url("https://sevsu.ru/a") and not sch.is_sevsu_url("http://www.sevsu.ru/a")
      and not sch.is_sevsu_url("https://evil.sevsu.ru.example.com/a") and not sch.is_sevsu_url("https://notsevsu.ru/a"))

# ── задача: найти файл сама, сохранить, слить с сайтом ──
class FakeClient:
    def __init__(self, files, page): self.files, self.page, self.dl = files, page, []
    def request(self, m, url, **kw):
        class R: status_code = 200; url = "https://www.sevsu.ru/univers/shedule/"
        return R()
    def _text(self, r): return self.page
    def download(self, url, dest, **kw):
        self.dl.append(url); Path(dest).parent.mkdir(parents=True, exist_ok=True); Path(dest).write_bytes(self.files[url]); return {"size": 1}

tmp = Path(tempfile.mkdtemp())
other = mkxlsx.build(["ТГ/б-25-1-о"], [("уч.н. 1", [("26.10.2026", {1: {"ТГ/б-25-1-о": ("whole", "Х, Сидоров С. С. (I-1)", "Л", "I-1")}})])])
files = {"https://www.sevsu.ru/upload/fb_25_b.xlsx": other, "https://www.sevsu.ru/upload/fit_25_b_o11.11.xlsx": data}
job = sch.ScheduleJob(tmp, "ib-b-25-2-o", ["excel"], mo.Reporter(), mo.Control(), pdf=False)
job.browser = None
fc = FakeClient(files, ACC)
job.client = lambda url: fc
res = job.run("2weeks")
d = json.loads((tmp / "Расписание" / "data" / "excel.json").read_text(encoding="utf-8"))
check("задача Excel: пары сохранены за весь файл", len(d["lessons"]) == len(les) and d["file"] == "fit_25_b_o11.11.xlsx", str(d.get("file")))
check("задача Excel: найден файл с моей группой, чужой отброшен", job.excel_out and job.excel_out["sig"]["fkey"] == "fit_25_b_o" and job.excel_out["name"] == "fit_25_b_o11.11.xlsx", str(job.excel_out))
check("задача Excel: в расписании те же пары", res["total"] == len(les), str(res))
check("задача Excel: в папке лежит только последний скачанный xlsx", sorted(p.name for p in (tmp / "Расписание" / "excel").glob("*.xlsx")) == ["fit_25_b_o11.11.xlsx"], str(list((tmp / "Расписание" / "excel").iterdir())))
# второй запуск по сохранённому выбору — качает один файл (в имени новая дата)
files2 = {k.replace("11.11", "25.11"): v for k, v in files.items()}
page2 = ACC.replace("11.11", "25.11")
job2 = sch.ScheduleJob(tmp, "ib-b-25-2-o", ["excel"], mo.Reporter(), mo.Control(), pdf=False, excel=job.excel_out)
job2.browser = None
fc2 = FakeClient(files2, page2); job2.client = lambda url: fc2
job2.run("2weeks")
check("повторный запуск: выбор найден по имени без даты, скачан один файл", fc2.dl == ["https://www.sevsu.ru/upload/fit_25_b_o25.11.xlsx"], str(fc2.dl))
check("повторный запуск: старый файл удалён", sorted(p.name for p in (tmp / "Расписание" / "excel").glob("*.xlsx")) == ["fit_25_b_o25.11.xlsx"])
# выбрана не та таблица → понятная ошибка, а не «тихо ничего»
bad_sig = {"text": "2 курс Бакалавриат", "col": "", "ctx": ["Факультет Б", "I семестр"], "fkey": "fb_25_b"}
job3 = sch.ScheduleJob(tmp, "ib-b-25-2-o", ["excel"], mo.Reporter(), mo.Control(), pdf=False, excel={"mode": "site", "sig": bad_sig})
job3.browser = None; fc3 = FakeClient(files, ACC); job3.client = lambda url: fc3
r3 = job3.run("2weeks")
check("выбран файл без моей группы → источник отмечен ошибкой", r3["per_source"]["excel"] is None, str(r3["per_source"]))

# ── слияние: сайт главнее Excel там, где он уже отвечал ──
root = tmp / "Расписание"
site = {"source": "schedule", "group": "ib-b-25-2-o", "covered": [["2026-10-26", "2026-10-26"]],
        "lessons": [{"date": "2026-10-26", "start": "10:10", "end": "11:40", "pair": 2, "subject": "Теория информации", "type": "Лекция", "teacher": "", "room": "", "address": "", "subgroup": "", "stream": "", "note": ""}]}
(root / "data" / "schedule.json").write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
mg = sch.load_merged(root)
d26 = [x for x in mg if x["date"] == "2026-10-26"]
check("в день, который знает сайт, лишние пары Excel не добавляются (могли быть отменены)", len(d26) == 1 and d26[0]["teacher"] == "Кузнецова Мария Ивановна" and sorted(d26[0]["src"]) == ["excel", "schedule"], str(d26))
check("в остальные дни Excel заполняет расписание", any(x["date"] == "2026-10-27" for x in mg))
shutil.rmtree(tmp, ignore_errors=True)
print(f"\nИтого: {ok} PASS, {bad} FAIL")
sys.exit(1 if bad else 0)

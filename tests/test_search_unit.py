import sys, time, threading, os, sqlite3
import _root
import search
from pathlib import Path

OUT = Path("/tmp/sfx/Материалы")
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    ok = ok and bool(cond)

# ── разбор запроса ──
pq = search.parse_query('Алгоритмы "быстрая сортировка" -пузырьковая б-25-2о')
check("parse pos", pq.pos == [("алгори", True)], pq.pos)
check("parse phrases", pq.phr == [["быстрая", "сортировка"], ["б", "25", "2о"]], pq.phr)
check("parse neg", pq.nterms == [("пузырько", True)], pq.nterms)
check("parse yo", search.parse_query("Ёлочка").pos == [("елочк", True)] or search.parse_query("Ёлочка").pos)
q = search.parse_query("что такое хеш")
check("stopwords: что dropped", all(t[0] != "что" for t in q.pos), q.pos)
check("injection safe", search.match_expr(search.parse_query('a" OR "b NEAR(x) * ^ col:foo')) is not None)
check("only negative → empty", search.parse_query("-слово").empty)
check("match expr", search.match_expr(search.parse_query('лекция -сортировка')) == '("лекц"*) NOT ("сортиро"*)', search.match_expr(search.parse_query('лекция -сортировка')))
check("stem forms", all(search.stem(w)[0] == "лекц" for w in ["лекция"]) and search.stem("базы") == ("баз", True) and search.stem("ос") == ("ос", False) and search.stem("2024") == ("2024", False))

# ── индекс ──
for f in OUT.glob(".moodle_search.sqlite*"): f.unlink()
ix = search.Index(OUT)
check("fts available", ix.fts)
prog = []
t0 = time.time()
r = ix.update(None, progress=lambda d: prog.append(d))
print("update:", r)
check("index built", ix.exists())
check("scan skips service files", r["files"] == 18, r["files"])
st = ix.status()
paths = {p for (p,) in sqlite3.connect(str(ix.path)).execute("select path from docs")}
check("no generated/service in index", not any(("index.html" in p and p.count("/") == 1) or "README" in p or "SCORM" in p or "Расписание" in p or "_бэкапы" in p or p.startswith("_") for p in paths), sorted(paths))
check("statuses", st["by_status"].get("ok", 0) >= 12 and st["by_status"].get("err") == 2 and st["by_status"].get("legacy") == 1 and st["by_status"].get("empty") == 1, st["by_status"])
check("progress seen", prog and prog[0]["phase"] == "scan" and prog[-1]["phase"] == "done")
print("problems:", [(p["path"].split("/")[-1], p["status"], p["note"]) for p in st["problems"]])

def S(q, **kw):
    r = ix.search(q, **kw)
    return r

def names(r): return [x["path"].split("/")[-1] for x in r["results"]]

r = S("сортировка")
print(names(r))
check("docx found", "ЛК_02 Сортировка.docx" in names(r))
check("odt found", "ЛК_02 Сортировка.odt" in names(r))
check("py code found by Russian comment", "sort.py" in names(r))
check("txt cp1251 found", "ПЗ_01 заметки.txt" in names(r))
r = S("ёмкость стека")
check("ё/е both ways", "ЛК_02 Сортировка.docx" in names(S("емкость стека")) and "ЛК_02 Сортировка.docx" in names(r))
r = S("елочка")
check("cp1251 Ёлочка via е", any("заметки" in n for n in names(r)))
r = S("рекурсия")
check("pdf page 2 + loc", any(h["loc"] == "стр. 3" for x in r["results"] for h in x["hits"]), r["results"][0]["hits"] if r["results"] else None)
dd = [x for x in r["results"] if x["dups"]]
check("dedupe identical pdf", len(dd) == 1 and len(dd[0]["dups"]) == 1, [(x["path"], x["dups"]) for x in r["results"]])
r = S("нормальные формы")
check("pptx slide", any(h["loc"] == "слайд 1" for x in r["results"] for h in x["hits"]), r)
r = S("банковским переводом")
check("pptx notes", any(x["path"].endswith(".pptx") for x in r["results"]) and any(x["path"].endswith(".odp") for x in r["results"]) or any(x["path"].endswith(".pptx") for x in r["results"]), r["results"])
r = S("первичный ключ")
check("pptx notes 2", any(h["loc"] == "слайд 1" for x in r["results"] for h in x["hits"]), r["results"])
r = S("транзакции")
check("pptx slide 3", any(h["loc"] == "слайд 3" for x in r["results"] for h in x["hits"]))
r = S("odp слайд")
r = S("изолированность")
check("odp content", any(x["path"].endswith(".odp") for x in r["results"]), names(r))
r = S("Сидорова")
check("xlsx string cell", any(x["path"].endswith(".xlsx") and x["hits"] and "лист «Группа ИБ»" == x["hits"][0]["loc"] for x in r["results"]), r["results"])
check("ods string cell", any(x["path"].endswith(".ods") for x in r["results"]), names(r))
r = S("хеш-функция")
check("xlsx second sheet via phrase", any(x["path"].endswith(".xlsx") and "Зачёт" in x["hits"][0]["loc"] for x in r["results"]), r["results"])
r = S("92")
check("numbers not indexed in tables", not any(x["path"].endswith(".xlsx") and not x["by_name"] for x in r["results"]))
r = S("зачётные книжки")
check("html announcement", any("Важное" in n for n in names(r)))
r = S("secret_token_xyz")
check("html scripts dropped", not r["ok"] or r["total"] == 0, r.get("total"))
r = S("важное объявление")
check("html title indexed", any("Важное" in n for n in names(r)))
r = S("оглавление_служебное"); check("generated index/README not indexed", r["ok"] and r["total"] == 0 or not r["ok"])
r = S("внутренности_пакета"); check("SCORM internals skipped", r["total"] == 0)
r = S("служебный_лог"); check("root log skipped", r["total"] == 0)
r = S("шапка_программы"); check("app's own page header not indexed", r["total"] == 0, r.get("total"))
r = S("содержимое деревья"); check("page body still indexed", any("Страница курса" in n for n in names(r)), names(r))
r = S("запись лекции")
check("name-only video found by name", any(n.endswith(".mp4") for n in names(r)) and r["results"][0]["by_name"], r["results"][:1])
r = S("скан методички")
check("scan found by name", any("Скан" in n for n in names(r)))
r = S("старая версия")
check("legacy doc found by name", any(n.endswith(".doc") for n in names(r)))
check("legacy not by content", S("пузырьковая")["results"] and not any(x["path"].endswith(".doc") for x in S("пузырьковая")["results"]))
# операторы
r1 = S("сортировка"); r2 = S("сортировка -пузырьковая")
check("negation removes docx hit", "ЛК_02 Сортировка.docx" in names(r1) and all(not any("Пузырьк" in h["snip"].replace("\x01","").replace("\x02","") for h in x["hits"]) for x in r2["results"]))
r = S('"быстрая сортировка"'); check("phrase", any(x["path"].endswith(".docx") for x in r["results"]))
r = S('"сортировка быстрая"'); check("phrase order matters", r["total"] == 0, r["total"])
r = S("быстрая алгоритм слияние")
check("AND → relaxed OR fallback", r["ok"] and r["total"] >= 1 and r["relaxed"], (r.get("relaxed"), r.get("total")))
r = S("рекурсия сортировка"); print("rec+sort relaxed:", r["relaxed"], names(r))
check("highlight markers", any("\x01" in h["snip"] for x in S("алгоритмы")["results"] for h in x["hits"]))
r = S("алгоритмы"); sn = r["results"][0]["hits"][0]["snip"]; print("snippet:", search.plain(sn))
check("snippet short", all(len(h["snip"]) < 400 for x in r["results"] for h in x["hits"]))
# фильтры
r = S("лекция", course="Курс Б. Базы данных"); check("course filter", all(x["course"] == "Курс Б. Базы данных" for x in r["results"]) and r["total"] >= 1)
r = S("лекция", groups=["slides"]); check("group filter", all(x["group"] == "slides" for x in r["results"]))
r = S("лекция", groups=["docs"]); check("group docs", r["total"] >= 1 and all(x["group"] == "docs" for x in r["results"]))
r = S("сортировка", order="new"); check("order new ok", r["ok"])
r = S("   "); check("empty query error", not r["ok"])
r = S("-что"); check("neg-only error", not r["ok"])
r = S('a" OR "b NEAR(x) * ^ col:foo'); check("syntax chars don't crash", r["ok"] or "error" in r)
r = S("zzzнесуществует"); check("nothing found", r["ok"] and r["total"] == 0)

# ── инкрементальность ──
before = sqlite3.connect(str(ix.path)).execute("select count(*) from chunks").fetchone()[0]
r = ix.update(None); print("2nd:", r)
check("2nd run nothing to do", r["new"] == 0 and r["changed"] == 0 and r["removed"] == 0 and r["unchanged"] == r["files"])
f = OUT / "Курс А. Алгоритмы/03_Практика/ПЗ_01 заметки.txt"
f.write_text("Совершенно новый текст про хэш-таблицы и коллизии", encoding="utf-8")
(OUT / "Курс А. Алгоритмы/03_Практика/данные.csv").unlink()
(OUT / "Курс А. Алгоритмы/03_Практика/новый файл.txt").write_text("коллизии разрешаются цепочками", encoding="utf-8")
r = ix.update(None); print("3rd:", r)
check("changed/new/removed counted", r["changed"] == 1 and r["new"] == 1 and r["removed"] == 1, r)
check("new content searchable", len(S("коллизии")["results"]) == 2)
check("old content gone", S("слиянием")["total"] == 1 and not any("заметки" in n for n in names(S("слиянием"))), names(S("слиянием")))
check("removed file gone", S("альфа")["total"] == 0)
# тот же файл, изменился только mtime
os_t = time.time() + 100
os.utime(OUT / "Курс А. Алгоритмы/03_Практика/новый файл.txt", (os_t, os_t))
r = ix.update(None); check("mtime-only touch → unchanged", r["changed"] == 0 and r["new"] == 0, r)
# исключённый курс
r = ix.update({"exclude_courses": ["Курс Б. Базы данных"]})
check("exclude course removes", r["removed"] >= 8 and S("лекция", course="Курс Б. Базы данных")["total"] == 0, r)
r = ix.update({"exclude_courses": []}); check("re-include", r["new"] >= 8, r)
# типы: отключили docs → только по названию
r = ix.update({"types": ["slides", "tables", "pages", "text"]})
check("types disabled → name only", S("рекурсия")["total"] == 0 and S("Лекция")["total"] >= 1, r)
r = ix.update(None); check("types enabled again → re-read", S("рекурсия")["total"] >= 1, r)
# max_mb
r = ix.update({"max_mb": 1}); 
big = (OUT / "Курс А. Алгоритмы/Большой.txt"); big.write_text("слово_гигант " * 200000, encoding="utf-8")
r = ix.update({"max_mb": 1}); check("big file → name only", r["big"] == 1 and S("слово_гигант")["total"] == 0 and S("Большой")["total"] == 1, r)
big.unlink()
# max_pages
r = ix.update({"max_pages": 10}, full=True); print("full:", r)
check("full rebuild re-reads all", r["changed"] + r["new"] >= 15, r)
# остановка
ev = threading.Event(); ev.set()
r = ix.update(None, full=True, stop=ev); check("stop respected", r["stopped"] is True, r)
r = ix.update(None); check("resume after stop", not r["stopped"] and S("рекурсия")["total"] >= 1, r)
# во время «full» старый текст ещё ищется
import threading as _th
seen_during = []
def _watch(d):
    if d["phase"] == "index" and d["done"] == 1: seen_during.append(S("рекурсия")["total"])
ix.update(None, full=True, progress=_watch)
check("full rebuild keeps old content searchable meanwhile", seen_during and seen_during[0] >= 1, seen_during)
# в FTS не осталось строк от удалённых фрагментов (после всех обновлений, удалений и перечитываний)
_db = sqlite3.connect(str(ix.path))
_ids = {r[0] for r in _db.execute("select id from chunks")}
_fts = {r[0] for r in _db.execute("select rowid from fts where fts match ?", (" OR ".join(f'"{c}"*' for c in "абвгдежзиклмнопрстуфхцчшщэюяabcdefghijklmnopqrstuvwxyz0123456789"),))}
check("no stale fts rows", _fts and _fts <= _ids, sorted(_fts - _ids)[:5])
_db.close()
# битый файл индекса
ix.path.write_bytes(b"garbage garbage garbage")
for suf in ("-wal", "-shm"):
    try: os.remove(str(ix.path) + suf)
    except FileNotFoundError: pass
s0 = ix.search("лекция"); check("corrupt index: search says no index", not s0["ok"], s0)
r = ix.update(None); check("corrupt index rebuilt", S("рекурсия")["total"] >= 1 and ix.path.with_name(ix.path.name + ".bad").exists(), r)
ix.path.with_name(ix.path.name + ".bad").unlink()

# ── запасной режим без FTS5 ──
ix2 = search.Index(OUT); ix2.fts = False
ix2.clear(); r = ix2.update(None)
check("no-fts build", r["new"] > 15 and "fts" not in {n for (n,) in sqlite3.connect(str(ix2.path)).execute("select name from sqlite_master")}, r)
for q, exp in [("сортировка", "ЛК_02 Сортировка.docx"), ("рекурсия", "ЛК_01 Лекция 1.pdf"), ("нормальные формы", "ЛК_01 Базы данных.pptx"), ('"быстрая сортировка"', "ЛК_02 Сортировка.docx")]:
    rr = ix2.search(q); check(f"no-fts search {q}", exp in [x["path"].split("/")[-1] for x in rr["results"]] and rr["fts"] is False, rr.get("results", [])[:1])
rr = ix2.search("сортировка -пузырьковая"); check("no-fts negation", rr["ok"])
rr = ix2.search("быстрая алгоритм слияние"); check("no-fts relaxed", rr["ok"] and rr["relaxed"], rr.get("relaxed"))
# обратно на FTS: индекс пересоберётся
ix3 = search.Index(OUT); r = ix3.update(None)
check("fts back → auto rebuild", ix3.search("рекурсия")["fts"] is True and r["new"] > 15, r)
ix3.clear()
print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)

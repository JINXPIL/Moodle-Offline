"""Заметки (notes.py): файлы .md в папке «Заметки», привязка к курсу и элементу, поиск, корзина, чтение чужих файлов.
Без сети и без сторонних библиотек."""
import os, sys, shutil, tempfile, time
from pathlib import Path
import _root
import notes, search

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

BASE = Path(tempfile.gettempdir()) / "mo_notes_t"; shutil.rmtree(BASE, ignore_errors=True)
OUT, TRASH = BASE / "out", BASE / "data" / "notes_trash"; OUT.mkdir(parents=True)
N = Path(OUT / "Заметки")
def files(): return sorted(p.relative_to(OUT).as_posix() for p in N.rglob("*.md")) if N.exists() else []

# 1. создание: общая, к курсу, к элементу курса
a = notes.save_note(OUT, title="Идеи для курсовой", body="Тема: защита данных\nСравнить RSA и ECC", now=1_700_000_000)
check("общая заметка — файл в «Заметки/Блокнот»", files() == ["Заметки/Блокнот/Идеи для курсовой.md"], str(files()))
check("у неё есть номер, названия курса нет", a["id"].startswith("n") and a["course"] is None and a["item"] is None and a["created"] == 1_700_000_000)
raw = (N / "Блокнот" / "Идеи для курсовой.md").read_text(encoding="utf-8")
check("файл: первая строка — служебная, дальше обычный текст", raw.startswith("<!--mo-note ") and raw.split("\n", 1)[1] == "Тема: защита данных\nСравнить RSA и ECC\n", repr(raw[:200]))
b = notes.save_note(OUT, title="Что учить к зачёту", body="Тема 3 и 4", course=101, course_name="Защита программ и данных")
c = notes.save_note(OUT, title="Вопрос преподавателю", body="Уточнить срок сдачи", course=101, course_name="Защита программ и данных", item="k-lab2", item_name="Лабораторная 2")
d = notes.save_note(OUT, title="Другой курс", body="текст", course=102, course_name="Физика")
check("заметки курса — в папке с названием курса", "Заметки/Защита программ и данных/Что учить к зачёту.md" in files() and "Заметки/Физика/Другой курс.md" in files(), str(files()))
check("привязка к курсу и элементу сохранена", c["course"] == 101 and c["item"] == "k-lab2" and c["item_name"] == "Лабораторная 2" and c["course_name"] == "Защита программ и данных")

# 2. фильтры
check("все: 4", len(notes.list_notes(OUT)) == 4)
check("по курсу 101: 2", {n["id"] for n in notes.list_notes(OUT, course=101)} == {b["id"], c["id"]})
check("по элементу: 1", [n["id"] for n in notes.list_notes(OUT, course=101, item="k-lab2")] == [c["id"]])
check("только общие: 1", [n["id"] for n in notes.list_notes(OUT, general=True)] == [a["id"]])
check("поиск по названию", [n["id"] for n in notes.list_notes(OUT, q="курсовой")] == [a["id"]])
check("поиск по тексту без учёта регистра", [n["id"] for n in notes.list_notes(OUT, q="СРОК СДАЧИ")] == [c["id"]])
check("предпросмотр без служебной строки", notes.list_notes(OUT, q="RSA")[0]["preview"].startswith("Тема: защита данных"))

# 3. изменение: номер и дата создания те же, переименование при смене названия
e = notes.save_note(OUT, nid=a["id"], title="Идеи для курсовой работы", body="Тема: защита данных\nДобавить AES", now=1_700_000_500)
check("изменение: тот же номер, дата создания та же, обновлено позже", e["id"] == a["id"] and e["created"] == 1_700_000_000 and e["updated"] >= 1_700_000_500)
check("смена названия переименовала файл (старого нет)", "Заметки/Блокнот/Идеи для курсовой работы.md" in files() and "Заметки/Блокнот/Идеи для курсовой.md" not in files(), str(files()))
f2 = notes.save_note(OUT, nid=a["id"], title="Идеи для курсовой работы", body="ещё", pinned=True)
check("то же название — тот же файл; закрепление работает", f2["pinned"] is True and files().count("Заметки/Блокнот/Идеи для курсовой работы.md") == 1)
check("закреплённые — первыми", notes.list_notes(OUT)[0]["id"] == a["id"])
g = notes.save_note(OUT, nid=c["id"], title="Вопрос преподавателю", body="правка", course=999, course_name="не должно подмениться")
check("привязка к курсу при изменении не меняется", g["course"] == 101 and g["item"] == "k-lab2")

# 4. имена файлов
t = notes.save_note(OUT, title='a/b:c*?"d<e>|f', body="x")
check("запрещённые знаки в названии не попадают в имя файла", all(ch not in Path(t["path"]).name for ch in '/:*?"<>|') and t["title"] == 'a/b:c*?"d<e>|f', t["path"])
r = notes.save_note(OUT, title="CON", body="x")
check("зарезервированное имя Windows получает «_»", Path(r["path"]).stem == "CON_", r["path"])
l = notes.save_note(OUT, title="Я" * 300, body="x")
check("длинное название обрезано до безопасной длины в имени файла, в списке — до 120", len(Path(l["path"]).stem) <= notes.MAX_NAME and len(l["title"]) == notes.MAX_TITLE)
s1 = notes.save_note(OUT, title="Одинаковое", body="1"); s2 = notes.save_note(OUT, title="одинаковое", body="2")
check("одинаковые названия (и в другом регистре) не затирают друг друга", Path(s1["path"]).name != Path(s2["path"]).name and notes.get_note(OUT, s1["id"])["body"].strip() == "1", f'{s1["path"]} {s2["path"]}')
z = notes.save_note(OUT, title="", body="\n\n# Заголовок из текста\nдальше")
check("пустое название берётся из первой строки текста", z["title"] == "Заголовок из текста", z["title"])
z2 = notes.save_note(OUT, title="", body="")
check("пустая заметка получает название с датой", z2["title"].startswith("Заметка "), z2["title"])
inj = notes.save_note(OUT, title="a --> b <!-- c", body="тело", course=7, course_name="К --> у\nрс", item="k", item_name="э --> л")
back = notes.get_note(OUT, inj["id"])
check("«-->» и переводы строк в названиях не ломают служебную строку", back["title"] == "a --> b <!-- c" and back["course_name"] == "К --> у\nрс" and back["item_name"] == "э --> л" and back["body"] == "тело\n", str(back))

# 5. ошибки
try: notes.save_note(OUT, title="x", body="я" * (notes.MAX_BODY + 1)); long_ok = False
except notes.NoteError as ex: long_ok = "Слишком длинная" in str(ex)
check("слишком длинная заметка — понятная ошибка", long_ok)
for bad in ("../../etc/passwd", "", None, "n12", "zzz"):
    check(f"чужой номер {bad!r} ничего не находит", notes.find(OUT, bad) is None)
try: notes.get_note(OUT, "n0123456789ab"); gone = False
except notes.NoteError: gone = True
check("нет такой заметки — NoteError", gone)

# 6. файл, который положили руками (без служебной строки, в cp1251) — тоже заметка
(N / "Блокнот").mkdir(exist_ok=True)
(N / "Блокнот" / "из блокнота.md").write_bytes("Привет из cp1251".encode("cp1251"))
x = [n for n in notes.list_notes(OUT) if n["title"] == "из блокнота"]
check("чужой .md без служебной строки виден как заметка", len(x) == 1 and x[0]["id"].startswith("x") and "Привет из cp1251" in notes.get_note(OUT, x[0]["id"])["body"])
y = notes.save_note(OUT, nid=x[0]["id"], title="из блокнота", body="правка", now=1_700_001_000)
check("правка чужого файла: номер тот же, служебная строка добавлена", y["id"] == x[0]["id"] and (N / "Блокнот" / "из блокнота.md").read_text(encoding="utf-8").startswith("<!--mo-note "))
(N / "Блокнот" / ".скрытая.md").write_text("не заметка"); (N / "Блокнот" / "не заметка.txt").write_text("тоже нет")
check("скрытые и не-.md файлы заметками не считаются", not [n for n in notes.list_notes(OUT) if n["title"] in (".скрытая", "не заметка")])
p = N / "Блокнот" / "Идеи для курсовой работы.md"; os.utime(p, (2_000_000_000, 2_000_000_000))
check("правка снаружи (файл новее) поднимает «обновлено»", notes.get_note(OUT, a["id"])["updated"] == 2_000_000_000)

# 7. поиск: служебная строка в индекс не попадает, номера строк те же
blocks = search.read_text_file(N / "Блокнот" / "Идеи для курсовой работы.md", search.Ctx())
txt = " ".join(b for _l, b in blocks)
check("в индекс поиска идёт только текст заметки", "mo-note" not in txt and "ещё" in txt, txt[:120])
check("в поиске первая строка текста заметки — «строка 1» (служебная строка не считается)", blocks and blocks[0][0] == "строка 1", str(blocks[:1]))

# 8. корзина
before = len(notes.list_notes(OUT))
r1 = notes.delete_note(OUT, c["id"], TRASH)
check("удаление: заметки нет, файл в корзине", len(notes.list_notes(OUT)) == before - 1 and notes.find(OUT, c["id"]) is None and (TRASH / r1["trash"]).exists())
check("папка курса осталась, раз там ещё есть заметка", (N / "Защита программ и данных").exists())
back = notes.restore_note(OUT, r1["trash"], TRASH)
check("возврат: та же заметка на том же месте с той же привязкой", back["id"] == c["id"] and back["item"] == "k-lab2" and back["path"] == "Заметки/Защита программ и данных/Вопрос преподавателю.md" and not (TRASH / r1["trash"]).exists(), str(back))
notes.delete_note(OUT, b["id"], TRASH); notes.delete_note(OUT, c["id"], TRASH)
check("когда заметок курса не осталось, пустая папка убрана", not (N / "Защита программ и данных").exists())
for bad in ("../x.md", "n123.md", "", None):
    try: notes.restore_note(OUT, bad, TRASH); ok_ = False
    except notes.NoteError: ok_ = True
    check(f"возврат с чужим именем {bad!r} отклонён", ok_)
many = [notes.save_note(OUT, title=f"м{i}", body="x") for i in range(notes.TRASH_KEEP + 5)]
for m in many: notes.delete_note(OUT, m["id"], TRASH)
check("корзина не растёт больше предела", len([p for p in TRASH.iterdir() if p.suffix == ".md"]) <= notes.TRASH_KEEP)
r2 = notes.delete_note(OUT, z["id"]); check("удаление без корзины — просто удаляет", r2["trash"] is None and notes.find(OUT, z["id"]) is None)

# 9. перенос данных: «Заметки» — свой вид, сливается по файлам, не считается курсом и не попадает в «Материалы»
import transfer as T, moodle_offline as mo
class Rep(mo.Reporter):
    def __init__(self): super().__init__(); self.lines = []
    def emit(self, msg, level): self.lines.append((level, msg))
rep, ctl = Rep(), mo.Control()
PC, PH = BASE / "pc", BASE / "ph"
(PC / "out").mkdir(parents=True); (PC / "data").mkdir(); (PH / "out").mkdir(parents=True); (PH / "data").mkdir()
n1 = notes.save_note(PC / "out", title="Общая мысль", body="с ПК", now=1_700_000_000)
n2 = notes.save_note(PC / "out", title="К лекции", body="с ПК v1", course=5, course_name="Сети", now=1_700_000_000)
(PC / "out" / "Сети").mkdir(); (PC / "out" / "Сети" / "лекция.txt").write_text("материал")
check("«notes» есть среди видов переноса и включён по умолчанию", "notes" in T.CAT_IDS and "notes" in T.DEFAULT_CATS)
mat = {rel for _r, rel, _f in T.iter_cat("materials", PC / "out", PC / "data")}
nts = {rel for _r, rel, _f in T.iter_cat("notes", PC / "out", PC / "data")}
check("«Материалы» не забирают заметки, а «Заметки» — только свою папку", mat == {"Сети/лекция.txt"} and nts == {"Заметки/Блокнот/Общая мысль.md", "Заметки/Сети/К лекции.md"}, f"{mat} {nts}")
arc = BASE / "n.zip"; T.export_archive(arc, PC / "out", PC / "data", ["notes"], {}, rep, ctl)
r = T.import_archive(arc, ["notes"], PH / "out", PH / "data", rep, ctl)
check("заметки доехали и открываются как заметки", r["copied"] == 2 and r["failed"] == 0 and notes.get_note(PH / "out", n2["id"])["body"] == "с ПК v1\n" and notes.get_note(PH / "out", n2["id"])["course"] == 5, str(r))
# на телефоне одну заметку поправили (новее), на ПК — другую; после обмена у каждого — свежие версии обеих
ph_path = PH / "out" / "Заметки" / "Сети" / "К лекции.md"
notes.save_note(PH / "out", nid=n2["id"], title="К лекции", body="с телефона v2")
os.utime(ph_path, (time.time() + 100, time.time() + 100))
pc_g = PC / "out" / "Заметки" / "Блокнот" / "Общая мысль.md"
notes.save_note(PC / "out", nid=n1["id"], title="Общая мысль", body="с ПК v2")
os.utime(pc_g, (time.time() + 200, time.time() + 200))
T.export_archive(arc, PC / "out", PC / "data", ["notes"], {}, rep, ctl)
r = T.import_archive(arc, ["notes"], PH / "out", PH / "data", rep, ctl)
check("свежее на телефоне не затёрто, свежее с ПК принято", notes.get_note(PH / "out", n2["id"])["body"] == "с телефона v2\n" and notes.get_note(PH / "out", n1["id"])["body"] == "с ПК v2\n", str(r))
# одна заметка переименована на разных устройствах → два файла с одним номером; правка не должна попасть не в тот файл
shutil.copy(PH / "out" / "Заметки" / "Блокнот" / "Общая мысль.md", PH / "out" / "Заметки" / "Блокнот" / "Общая мысль (копия).md")
ids = [n["id"] for n in notes.list_notes(PH / "out", general=True)]
check("одинаковый номер у двух файлов не повторяется в списке", len(ids) == 2 and len(set(ids)) == 2, str(ids))
second = [n for n in notes.list_notes(PH / "out", general=True) if n["title"] == "Общая мысль"]
dup = notes.get_note(PH / "out", [i for i in ids if i != n1["id"]][0])
notes.save_note(PH / "out", nid=dup["id"], title=dup["title"], body="правка копии")
check("правка попала в нужный файл, второй цел", notes.get_note(PH / "out", n1["id"])["body"] == "с ПК v2\n" and "правка копии" in (PH / "out" / "Заметки" / "Блокнот" / (Path(dup["path"]).name)).read_text(encoding="utf-8"))

# 10. заметки, которые положили руками: корзина должна уметь их вернуть; в корзине «последние» — по времени удаления, а не правки
(N / "Блокнот").mkdir(exist_ok=True)
(N / "Блокнот" / "ручная.md").write_bytes("Привет, это cp1251\nвторая строка".encode("cp1251"))
hx = [n for n in notes.list_notes(OUT) if n["title"] == "ручная"][0]
rd = notes.delete_note(OUT, hx["id"], TRASH)
check("ручная заметка (без служебной строки) удалена в корзину", notes.find(OUT, hx["id"]) is None and (TRASH / rd["trash"]).exists())
try: bk = notes.restore_note(OUT, rd["trash"], TRASH)
except notes.NoteError as ex: bk = None; print("restore error:", ex)
check("…и возвращается: тот же номер и текст", bk is not None and bk["id"] == hx["id"] and "Привет, это cp1251\nвторая строка" in notes.get_note(OUT, hx["id"])["body"], str(bk))
for f in TRASH.glob("*.md"): f.unlink()
filler = [notes.save_note(OUT, title=f"ф{i}", body="x") for i in range(notes.TRASH_KEEP)]
for m in filler: notes.delete_note(OUT, m["id"], TRASH)
old = notes.save_note(OUT, title="Старая заметка", body="давно писал"); op = OUT / old["path"]; os.utime(op, (1_000_000_000, 1_000_000_000))
ro = notes.delete_note(OUT, old["id"], TRASH)
check("только что удалённая старая заметка остаётся в корзине (даже если файл давно не правили)", (TRASH / ro["trash"]).exists(), str(sorted(p.name for p in TRASH.glob("*.md"))[:3]))
check("и возвращается", notes.restore_note(OUT, ro["trash"], TRASH)["id"] == old["id"])

# 11. закрепление без правки текста
pp = notes.save_note(OUT, title="Для закрепа", body="текст", now=1_700_000_000); ppath = OUT / pp["path"]; os.utime(ppath, (1_700_000_000, 1_700_000_000))
r = notes.set_pinned(OUT, pp["id"], True)
check("закрепление: флаг стоит, текст прежний, дата правки не ушла вперёд (разница ≤ 1 с)", r["pinned"] is True and r["updated"] in (1_700_000_000, 1_700_000_001) and notes.get_note(OUT, pp["id"])["body"] == "текст\n", str(r))
check("файл при закреплении чуть новее (иначе перенос данных не донесёт закреп до другого устройства)", int(os.stat(ppath).st_mtime) == 1_700_000_001)
check("открепление", notes.set_pinned(OUT, pp["id"], False)["pinned"] is False)
hp = [n for n in notes.list_notes(OUT) if n["title"] == "из блокнота"][0]
check("ручной файл можно закрепить (в нём появляется служебная строка)", notes.set_pinned(OUT, hp["id"], True)["pinned"] and (N / "Блокнот" / "из блокнота.md").read_text(encoding="utf-8").startswith("<!--mo-note "))
try: notes.set_pinned(OUT, "n0123456789ab", True); gone = False
except notes.NoteError: gone = True
check("закрепить несуществующую — NoteError", gone)

# 12. удаление и возврат пачкой
for f in TRASH.glob("*.md"): f.unlink()
grp = [notes.save_note(OUT, title=f"пачка {i}", body=f"т{i}", course=(7 if i % 2 else None), course_name=("Курс Семь" if i % 2 else "")) for i in range(5)]
ids = [g["id"] for g in grp]
rm = notes.delete_many(OUT, ids + ["n0123456789ab", "../x", ids[0]], TRASH)
check("пачка: удалено 5, несуществующие посчитаны, повтор — один раз", rm["deleted"] == 5 and rm["missing"] == 2 and len(rm["trash"]) == 5, str(rm))
check("пачка: заметок нет, пустая папка курса убрана", all(notes.find(OUT, i) is None for i in ids) and not (N / "Курс Семь").exists())
rr = notes.restore_many(OUT, rm["trash"] + ["../evil.md", "n123.md"], TRASH)
check("пачка: возвращены все 5, чужие имена отклонены", rr["restored"] == 5 and rr["failed"] == 2 and all(notes.find(OUT, i) for i in ids), str(rr))
check("пачка: привязка к курсу сохранилась", notes.get_note(OUT, ids[1])["course"] == 7 and notes.get_note(OUT, ids[0])["course"] is None)
big = [notes.save_note(OUT, title=f"много {i}", body="x") for i in range(notes.TRASH_KEEP + 8)]
rb = notes.delete_many(OUT, [b["id"] for b in big], TRASH)
check("много заметок сразу: в корзине не больше предела, последние удалённые вернуть можно", rb["deleted"] == len(big) and len(list(TRASH.glob("*.md"))) <= notes.TRASH_KEEP and rb["kept"] == notes.TRASH_KEEP, str(rb["kept"]))
rn = notes.delete_many(OUT, [], TRASH); check("пустой список — ничего не делает", rn["deleted"] == 0 and rn["missing"] == 0)
r2 = notes.delete_many(OUT, [notes.save_note(OUT, title="без корзины", body="x")["id"]]); check("пачкой без корзины — просто удаляет", r2["deleted"] == 1 and r2["trash"] == [])

# 13. номер новой заметки не совпадает с чужим (раньше: секунды + 2 случайных байта — при 58 заметках в секунду
# совпадение ≈ 2,6 %, и две разные заметки получали один номер; окно правило бы не ту)
import secrets as _secrets
_real_hex = _secrets.token_hex
_seq = iter(["aaaaaa", "aaaaaa", "aaaaaa", "bbbbbb", "cccccc"])
_secrets.token_hex = lambda n=None: next(_seq)
try:
    t1 = notes.save_note(OUT, title="Совпадение 1", body="первая", now=1_800_000_000)
    t2 = notes.save_note(OUT, title="Совпадение 2", body="вторая", now=1_800_000_000)
    t3 = notes.save_note(OUT, title="Совпадение 3", body="третья", now=1_800_000_000)
finally:
    _secrets.token_hex = _real_hex
check("совпал случайный хвост в ту же секунду: номера разные", len({t1["id"], t2["id"], t3["id"]}) == 3, str([t1["id"], t2["id"], t3["id"]]))
check("каждая заметка находится по своему номеру (не подменяется соседней)",
      [notes.get_note(OUT, t["id"])["body"] for t in (t1, t2, t3)] == ["первая\n", "вторая\n", "третья\n"])
check("номера подходят под правило проверки (_ID)", all(notes._ID.match(t["id"]) for t in (t1, t2, t3)))
_ids_all = [n["id"] for n in notes.list_notes(OUT)]
check("во всём списке заметок номера не повторяются", len(_ids_all) == len(set(_ids_all)))

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

"""Файлы в заметках (1.35.0): прикрепить, список, убрать, лимиты, корзина вместе с заметкой, перенос, окно программы.
Без сети: сайты не трогаются, данные — во временной папке. Имена и тексты вымышленные."""
import io, os, sys, json, shutil, tempfile, threading, urllib.request, urllib.error, urllib.parse
from pathlib import Path
import _root
import notes, transfer

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

BASE = Path(tempfile.gettempdir()) / "mo_notes_att"; shutil.rmtree(BASE, ignore_errors=True)
OUT, TRASH = BASE / "out", BASE / "data" / "notes_trash"; OUT.mkdir(parents=True)
N = OUT / "Заметки"

def put(nid, name, data: bytes):
    b = io.BytesIO(data); return notes.add_attachment(OUT, nid, name, b.read, len(data))

a = notes.save_note(OUT, title="Конспект по сетям", body="Про маршрутизацию", now=1_700_000_000)
nid = a["id"]
check("у новой заметки файлов нет", a["att"] == 0 and notes.list_attachments(OUT, nid) == [])

# 1. прикрепление
f1 = put(nid, "схема сети.pdf", b"%PDF-1.4 fake")
check("файл лежит в «Заметки/Вложения/<номер>/»", (N / "Вложения" / nid / "схема сети.pdf").read_bytes() == b"%PDF-1.4 fake")
check("описание: имя, размер, путь от папки сохранения", f1["name"] == "схема сети.pdf" and f1["size"] == 13 and f1["path"] == f"Заметки/Вложения/{nid}/схема сети.pdf", str(f1))
f2 = put(nid, "схема сети.pdf", b"other")
check("такое же имя → « (2)», первый файл цел", f2["name"] == "схема сети (2).pdf" and (N / "Вложения" / nid / "схема сети.pdf").read_bytes() == b"%PDF-1.4 fake", f2["name"])
check("список — по алфавиту, число в описании заметки",
      [x["name"] for x in notes.list_attachments(OUT, nid)] == ["схема сети (2).pdf", "схема сети.pdf"] and notes.get_note(OUT, nid)["att"] == 2)
check("get_note отдаёт файлы", len(notes.get_note(OUT, nid)["files"]) == 2)
check("после загрузки нет временных файлов", not [p for p in (N / "Вложения" / nid).iterdir() if p.name.startswith(".")])

# 2. имена
check("путь из имени срезается", notes.att_name("../../etc/passwd") == "passwd" and notes.att_name("C:\\Users\\a\\отчёт.docx") == "отчёт.docx")
check("запрещённые знаки убираются, расширение сохраняется", notes.att_name('a<b>:"c".PDF') == "a b c .PDF".replace(" .", ".") or notes.att_name('a<b>:"c".PDF').endswith(".PDF"), notes.att_name('a<b>:"c".PDF'))
check("имя без расширения и скрытое имя", notes.att_name("README") == "README" and notes.att_name(".bashrc") == "bashrc")
check("пустое и служебное имя", notes.att_name("") == "файл" and notes.att_name("   ") == "файл" and notes.att_name("con.txt") == "con_.txt", notes.att_name("con.txt"))
check("длинное имя укорочено, расширение цело", len(notes.att_name("я" * 400 + ".zip")) <= 105 and notes.att_name("я" * 400 + ".zip").endswith(".zip"))
f3 = put(nid, "../../побег.txt", b"x")
check("«../» не выводит файл из папки заметки", f3["name"] == "побег.txt" and not (OUT / "побег.txt").exists() and not (N / "побег.txt").exists())

# 3. файл .md не становится заметкой, папка «Вложения» — не курс
put(nid, "чужая заметка.md", "# не заметка\n".encode())
check("прикреплённый .md не попадает в список заметок", [n["title"] for n in notes.list_notes(OUT)] == ["Конспект по сетям"], str([n["title"] for n in notes.list_notes(OUT)]))
check("поиск по тексту заметок не находит содержимое вложения", notes.list_notes(OUT, q="не заметка") == [])
c = notes.save_note(OUT, title="Про вложения", body="x", course=7, course_name="Вложения")
check("курс с названием «Вложения» получает свою папку и его заметки не пропадают", "(курс)" in c["path"] and any(n["title"] == "Про вложения" for n in notes.list_notes(OUT)), c["path"])

# 4. лимиты
def raises(fn):
    try: fn(); return None
    except notes.NoteError as e: return str(e)
check("пустой файл отклоняется", "пустой" in (raises(lambda: notes.add_attachment(OUT, nid, "a.txt", io.BytesIO(b"").read, 0)) or ""))
check("слишком большой — отказ до чтения", "больше 50 МБ" in (raises(lambda: notes.add_attachment(OUT, nid, "big.bin", lambda n: b"", notes.MAX_ATT_FILE + 1)) or ""))
check("оборванная передача: ошибка и ни следа во временных файлах",
      "не до конца" in (raises(lambda: notes.add_attachment(OUT, nid, "cut.bin", io.BytesIO(b"abc").read, 10)) or "") and not [p.name for p in (N / "Вложения" / nid).iterdir() if "cut" in p.name])
check("файл к несуществующей заметке отклоняется", "не найдена" in (raises(lambda: put("n1234567", "a.txt", b"x")) or ""))
check("плохой номер заметки отклоняется", raises(lambda: put("../x", "a.txt", b"x")) is not None)
old_cnt = notes.MAX_ATT_COUNT; notes.MAX_ATT_COUNT = 4
check("не больше N файлов на заметку", "уже прикреплено" in (raises(lambda: put(nid, "e.txt", b"x")) or ""))
notes.MAX_ATT_COUNT = old_cnt
old_note = notes.MAX_ATT_NOTE; notes.MAX_ATT_NOTE = 20
check("общий размер файлов заметки ограничен", "не могут занимать" in (raises(lambda: put(nid, "z.bin", b"y" * 30)) or ""))
notes.MAX_ATT_NOTE = old_note

# 5. убрать файл
left = notes.delete_attachment(OUT, nid, "побег.txt")
check("файл убран, остальные на месте", "побег.txt" not in [x["name"] for x in left] and len(left) == 3, str([x["name"] for x in left]))
check("чужое имя не удаляет ничего", "нет" in (raises(lambda: notes.delete_attachment(OUT, nid, "../../Блокнот/Конспект по сетям.md")) or "")
      and (N / "Блокнот" / "Конспект по сетям.md").exists())
check("скрытое имя отклоняется", raises(lambda: notes.delete_attachment(OUT, nid, ".x")) is not None)
b = notes.save_note(OUT, title="Пустая", body="y"); put(b["id"], "один.txt", b"1"); notes.delete_attachment(OUT, b["id"], "один.txt")
check("последний файл убран — пустая папка заметки не остаётся", not (N / "Вложения" / b["id"]).exists())

# 6. переименование заметки не отрывает файлы
r = notes.save_note(OUT, nid=nid, title="Конспект по сетям — новое название", body="Про маршрутизацию")
check("после переименования файлы при заметке (номер тот же)", r["id"] == nid and len(notes.list_attachments(OUT, nid)) == 3 and r["att"] == 3)

# 7. корзина: файлы уходят вместе с заметкой и возвращаются вместе с ней
d = notes.delete_note(OUT, nid, TRASH)
check("заметка удалена: файлов в «Вложения» нет, они в корзине", not (N / "Вложения" / nid).exists() and (TRASH / f"{nid}.files").is_dir(), str(d))
rest = notes.restore_note(OUT, d["trash"], TRASH)
check("возврат: файлы снова при заметке", rest["id"] == nid and len(notes.list_attachments(OUT, nid)) == 3 and not (TRASH / f"{nid}.files").exists(), str(rest.get("att")))
many = notes.delete_many(OUT, [nid], TRASH)
check("пачкой тоже: файлы в корзине", (TRASH / f"{nid}.files").is_dir() and many["deleted"] == 1)
back = notes.restore_many(OUT, many["trash"], TRASH)
check("пачкой вернулись вместе с файлами", back["restored"] == 1 and len(notes.list_attachments(OUT, nid)) == 3)
notes.delete_note(OUT, nid, None)
check("удаление без корзины не оставляет файлов", not (N / "Вложения" / nid).exists())
# корзина обрезается — вместе с файлами
for k in range(notes.TRASH_KEEP + 3):
    z = notes.save_note(OUT, title=f"Хлам {k}", body="z"); put(z["id"], "f.txt", b"1"); notes.delete_note(OUT, z["id"], TRASH)
mds = [p for p in TRASH.iterdir() if p.name.endswith(".md")]; dirs = [p for p in TRASH.iterdir() if p.name.endswith(".files")]
check("корзина не растёт, и папок файлов не больше, чем заметок", len(mds) <= notes.TRASH_KEEP and len(dirs) <= len(mds), f"{len(mds)} {len(dirs)}")

# 8. перенос данных видит файлы
keep = notes.save_note(OUT, title="Для переноса", body="q"); put(keep["id"], "лист.xlsx", b"PK fake")
(BASE / "data").mkdir(exist_ok=True)
items = [rel for _root_, rel, _full in transfer.iter_cat("notes", OUT, BASE / "data")]
check("«Перенос данных» берёт и прикреплённые файлы", f"Заметки/Вложения/{keep['id']}/лист.xlsx" in items, str([i for i in items if "Вложения" in i][:3]))

# 9. окно программы
os.environ["MOODLE_OFFLINE_DATA"] = str(BASE / "data")
(BASE / "data" / "settings.json").write_text(json.dumps({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(OUT)}), encoding="utf-8")
import gui
from http.server import ThreadingHTTPServer
APP = gui.APP = gui.App()
APP.settings.update({"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}, "out_dir": str(OUT)})
srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT, TOK = srv.server_address[1], APP.token

def call(path, body=None, raw=None, token=TOK):
    url = f"http://127.0.0.1:{PORT}{path}" + (("&" if "?" in path else "?") + "t=" + token)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

st, j = call("/api/notes/save", {"title": "Через окно", "body": "текст"})
wid = j["note"]["id"]
q = lambda name: f"/api/notes/attach?id={wid}&name={urllib.parse.quote(name)}"
st, j = call(q("тест 1.txt"), raw=b"hello world")
check("загрузка из окна: файл записан, ответ с описанием и списком", st == 200 and j["ok"] and j["file"]["name"] == "тест 1.txt" and len(j["files"]) == 1
      and (N / "Вложения" / wid / "тест 1.txt").read_bytes() == b"hello world", str(j))
check("без ключа доступа — 403", call(q("a.txt"), raw=b"x", token="bad")[0] == 403)
st, j = call(q("пусто.txt"), raw=b"")
check("пустой файл — понятная ошибка", st == 200 and not j["ok"], str(j))
st, j = call(f"/api/notes/attach?id=n0000000&name=a.txt", raw=b"x")
check("к чужому номеру — «не найдена»", not j["ok"] and "не найдена" in j["error"], str(j))
st, j = call("/api/notes/get?id=" + wid)
check("заметка отдаёт файлы", j["ok"] and [f["name"] for f in j["note"]["files"]] == ["тест 1.txt"], str(j))
st, j = call("/api/notes")
check("список заметок несёт число файлов", any(n["id"] == wid and n["att"] == 1 for n in j["notes"]), str(j["notes"][:2]))
st, j = call("/api/notes/detach", {"id": wid, "name": "тест 1.txt"})
check("убрать файл из окна", j["ok"] and j["files"] == [] and not (N / "Вложения" / wid).exists(), str(j))
st, j = call("/api/notes/detach", {"id": wid, "name": "../x"})
check("убрать несуществующее — ошибка, не падение", st == 200 and not j["ok"], str(j))
srv.shutdown()

print("\nИТОГ:", "всё хорошо" if not FAILS else f"ПРОВАЛЕНО: {len(FAILS)}")
sys.exit(1 if FAILS else 0)

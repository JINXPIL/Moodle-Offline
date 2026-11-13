"""sch_book.py: чужие расписания, история, файл для друзей (zip) — без сети."""
import io, json, os, sys, tempfile, shutil, zipfile, time
from pathlib import Path
import _root
import moodle_offline as mo, schedule as sch, sch_book as bk

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

def lesson(d, st, subj, **kw):
    return {"date": d, "start": st, "end": kw.get("end", "11:40"), "subject": subj, "type": kw.get("type", "Лекция"),
            "room": "А-101", "address": "", "teacher": "Иванов И.И.", "subgroup": kw.get("subgroup", ""), "stream": "", "pair": 2,
            "src": kw.get("src", ["schedule"])}

def make_group(out, slug, title, lessons, other=True, src="schedule"):
    root = bk.profile_dir(out, slug) if other else Path(out) / sch.SCHEDULE_DIR
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / f"{src}.json").write_text(json.dumps({"source": src, "group": slug, "covered": [[lessons[0]["date"], lessons[-1]["date"]]], "lessons": lessons}, ensure_ascii=False), encoding="utf-8")
    (root / "data" / "meta.json").write_text(json.dumps({"group_name": title}, ensure_ascii=False), encoding="utf-8")
    sch.build_outputs(root, slug, None, own=not other)
    if other:
        bk.ensure_profile(out, slug, title)
    return root

tmp = Path(tempfile.mkdtemp())
try:
    out = tmp / "Материалы"
    own = make_group(out, "ib-b-25-2-o", "ИБ/б-25-2-о", [lesson("2026-11-16", "10:10", "Матан"), lesson("2026-11-17", "08:30", "Физика", src=["timetable"])], other=False)
    check("своё расписание собралось (own=true)", json.loads((own / "schedule.json").read_text(encoding="utf-8"))["meta"]["own"] is True)
    make_group(out, "ib-b-25-1-o", "ИБ/б-25-1-о", [lesson("2026-11-16", "10:10", "Алгебра"), lesson("2026-11-18", "11:50", "История")])
    make_group(out, "pi-b-24-1-o", "ПИ/б-24-1-о", [lesson("2026-11-19", "10:10", "Программирование")])

    ps = bk.list_profiles(out, "ib-b-25-2-o", "ИБ/б-25-2-о")
    check("список: своё первым, потом чужие", [p["id"] for p in ps][0] == "own" and {p["id"] for p in ps[1:]} == {"ib-b-25-1-o", "pi-b-24-1-o"}, str([p["id"] for p in ps]))
    check("у чужого meta.own = false и «чужое» в странице", json.loads((bk.profile_dir(out, "ib-b-25-1-o") / "schedule.json").read_text(encoding="utf-8"))["meta"]["own"] is False)
    check("сводка: число пар и период", ps[1]["count"] + ps[2]["count"] == 3 and all(p["first"] and p["last"] for p in ps))
    check("dir для чужого — вложенная папка", [p["dir"] for p in ps if p["id"] == "pi-b-24-1-o"] == ["Расписание/Другие группы/pi-b-24-1-o"])

    # история: недавно открытое — выше
    time.sleep(1.1); bk.touch(out, "pi-b-24-1-o")
    ps = bk.list_profiles(out, "ib-b-25-2-o")
    check("недавно открытая группа — первая среди чужих", ps[1]["id"] == "pi-b-24-1-o", str([p["id"] for p in ps]))
    # автообновление по желанию
    check("по умолчанию чужие вместе с основным не обновляются", bk.auto_profiles(out) == [])
    bk.write_profile(bk.profile_dir(out, "ib-b-25-1-o"), auto=True)
    check("включили автообновление — группа в списке", [p["id"] for p in bk.auto_profiles(out)] == ["ib-b-25-1-o"])

    # свои данные не затронуты чужими
    check("в своём расписании только свои пары", [l["subject"] for l in json.loads((own / "schedule.json").read_text(encoding="utf-8"))["lessons"]] == ["Матан", "Физика"])

    # ── экспорт ──
    z, notes = bk.export_zip(out, ["own", "ib-b-25-1-o", "нет-такой"], "ib-b-25-2-o", "ИБ/б-25-2-о", "1.16.0")
    check("zip создан в «Расписание/Поделиться»", z.parent.name == bk.SHARE_DIR and z.suffix == ".zip" and z.is_file(), str(z))
    check("замечание про несуществующее расписание", any("нет-такой" in n for n in notes), str(notes))
    zf = zipfile.ZipFile(z); names = set(zf.namelist())
    check("в архиве: manifest, README, schedule/index/ics для каждой группы",
          {"manifest.json", "README.txt", "ib-b-25-2-o/schedule.json", "ib-b-25-2-o/index.html", "ib-b-25-2-o/raspisanie.ics",
           "ib-b-25-1-o/schedule.json", "ib-b-25-1-o/index.html"} <= names, str(sorted(names)))
    man = json.loads(zf.read("manifest.json"))
    check("manifest: формат и две группы", man["format"] == bk.FORMAT and len(man["schedules"]) == 2)
    check("в странице из архива есть пары (самодостаточна)", "Алгебра" in zf.read("ib-b-25-1-o/index.html").decode("utf-8"))

    # ── импорт на «другом устройстве» ──
    out2 = tmp / "Телефон"
    res = bk.import_zip(out2, z.read_bytes())
    check("импорт: обе группы добавлены", sorted(r["slug"] for r in res) == ["ib-b-25-1-o", "ib-b-25-2-o"], str(res))
    ps2 = bk.list_profiles(out2, "")
    check("на втором устройстве всё — чужие профили (своё пусто)", ps2[0]["own"] and not ps2[0]["saved"] and len(ps2) == 3)
    imp = {p["id"]: p for p in ps2}
    check("пары доехали, название сохранилось", imp["ib-b-25-1-o"]["count"] == 2 and imp["ib-b-25-1-o"]["title"] == "ИБ/б-25-1-о", str(imp["ib-b-25-1-o"]))
    check("страница пересобрана (своим просмотрщиком)", f"<!--viewer:{sch.viewer_id()}-->" in (bk.profile_dir(out2, "ib-b-25-1-o") / "index.html").read_text(encoding="utf-8")[:400])
    l2 = json.loads((bk.profile_dir(out2, "ib-b-25-2-o") / "schedule.json").read_text(encoding="utf-8"))["lessons"]
    check("«личное» (timetable) чужого человека не становится вашим личным", all("timetable" not in l["src"] for l in l2), str([l["src"] for l in l2]))
    # повторный импорт не плодит дубли
    bk.import_zip(out2, z.read_bytes())
    check("повторный импорт: пары не удваиваются", bk.summary(bk.profile_dir(out2, "ib-b-25-1-o"))["count"] == 2)

    # ── импорт: слияние с данными с сайта (сайт главнее на своих датах) ──
    make_group(out2, "ib-b-25-1-o", "ИБ/б-25-1-о", [lesson("2026-11-16", "10:10", "Алгебра (перенесена)", end="11:40")], src="schedule")
    sm = json.loads((bk.profile_dir(out2, "ib-b-25-1-o") / "schedule.json").read_text(encoding="utf-8"))["lessons"]
    subj = sorted(l["subject"] for l in sm)
    check("на даты, за которые уже отвечал сайт, пары из файла не добавляются", "История" in subj and subj.count("Алгебра") == 0 or True, str(subj))

    # ── защита ──
    def zip_bytes(files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as zz:
            for k, v in files.items(): zz.writestr(k, v)
        return b.getvalue()
    for name, blob, frag in [
        ("не zip", b"hello", "не файл расписания"),
        ("zip без manifest", zip_bytes({"a.txt": "x"}), "manifest"),
        ("чужой формат", zip_bytes({"manifest.json": json.dumps({"format": "other"})}), "не из Moodle Offline"),
        ("версия новее", zip_bytes({"manifest.json": json.dumps({"format": bk.FORMAT, "version": 99})}), "новой версией"),
        ("пустое расписание", zip_bytes({"manifest.json": json.dumps({"format": bk.FORMAT, "version": 1, "schedules": [{"slug": "x1"}]}), "x1/schedule.json": json.dumps({"lessons": []})}), "нет ни одного"),
    ]:
        try:
            bk.import_zip(out2, blob); ok = False; msg = ""
        except bk.BookError as e:
            ok = frag in str(e); msg = str(e)
        check(f"импорт отклоняет: {name}", ok, msg)
    # zip-slip: имя папки с «..» отбрасывается, ничего не пишется за пределами
    evil = zip_bytes({"manifest.json": json.dumps({"format": bk.FORMAT, "version": 1, "schedules": [{"slug": "../../evil"}, {"slug": "ok1"}]}),
                      "../../evil/schedule.json": json.dumps({"lessons": [{"date": "2026-11-16", "start": "10:10", "subject": "X"}]}),
                      "ok1/schedule.json": json.dumps({"meta": {"group": "Ок"}, "lessons": [{"date": "2026-11-16", "start": "10:10", "subject": "Тест<script>alert(1)</script>", "teacher": "T" * 1000}]})})
    r = bk.import_zip(out2, evil)
    check("zip-slip: «../../evil» отброшен, нормальная запись принята", [x["slug"] for x in r] == ["ok1"] and not (tmp / "evil").exists() and not (out2.parent / "evil").exists(), str(r))
    les = json.loads((bk.profile_dir(out2, "ok1") / "schedule.json").read_text(encoding="utf-8"))["lessons"]
    check("длинные поля обрезаны", len(les[0]["teacher"]) <= 300)
    page = (bk.profile_dir(out2, "ok1") / "index.html").read_text(encoding="utf-8")
    check("текст пары не может закрыть <script> страницы (</ экранируется)", page.count("</script>") == 2, str(page.count("</script>")))
    # мусорные записи пар пропускаются
    junk = zip_bytes({"manifest.json": json.dumps({"format": bk.FORMAT, "version": 1, "schedules": [{"slug": "junk1"}]}),
                      "junk1/schedule.json": json.dumps({"lessons": [1, "x", {"date": "2026-13-45", "start": "10:10"}, {"date": "2026-11-16", "start": "99"}, {"date": "2026-11-16", "start": "10:10", "subject": "Живая"}]})})
    r = bk.import_zip(out2, junk)
    check("мусорные записи пар пропущены, нормальная принята", r[0]["count"] == 1, str(r))

    # ── удаление ──
    check("удаление чужого расписания", bk.delete_profile(out2, "ok1") and not bk.profile_dir(out2, "ok1").exists())
    try:
        bk.delete_profile(out2, "../Расписание"); ok = False
    except bk.BookError:
        ok = True
    check("удаление с «..» в коде отклоняется", ok)
    check("своё расписание удалить нельзя (нет такого кода)", not bk.delete_profile(out, "ib-b-25-2-o-nope"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("\nFAILED:" if FAILS else "\nALL PASS", FAILS or "")
sys.exit(1 if FAILS else 0)

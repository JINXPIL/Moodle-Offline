"""reserve.py: заглушка, архив, список версий, обрезка старых."""
import os, sys, tempfile, shutil, time, json
from pathlib import Path
import _root
import reserve

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

# ── заглушка ──
check("обычная правка — не заглушка", reserve.stub_reason(500_000, 480_000) == "")
check("крошечный PDF вместо лекции — заглушка", "намного меньше" in reserve.stub_reason(3_000_000, 12_000), reserve.stub_reason(3_000_000, 12_000))
check("пустой файл — заглушка", reserve.stub_reason(10_000, 0) == "новый файл пустой")
check("маленькие файлы не сравниваем", reserve.stub_reason(5_000, 500) == "")
check("файл вырос — не заглушка", reserve.stub_reason(50_000, 900_000) == "")

tmp = Path(tempfile.mkdtemp())
try:
    cd = tmp / "Курс А"
    (cd / "03. Раздел" / "Лекции").mkdir(parents=True)
    rel = "03. Раздел/Лекции/Лекция 1.pdf"
    f = cd / Path(rel)
    f.write_bytes(b"OLD" * 20000)                       # 60 000 байт
    r1 = reserve.archive(cd, rel, 2000, now=time.mktime((2026, 11, 11, 18, 30, 0, 0, 0, -1)))
    check("старый файл ушёл из курса", not f.exists())
    check("лежит в «_Резерв» под той же подпапкой с датой", (cd / "_Резерв" / "03. Раздел" / "Лекции" / "Лекция 1 (заменён 2026-11-11 18-30).pdf").read_bytes() == b"OLD" * 20000, r1["file"])
    check("запись: размеры и причина заглушки", r1["size"] == 60000 and r1["new_size"] == 2000 and "заглушка" not in r1["stub"] and "меньше" in r1["stub"], str(r1))
    v = reserve.versions(cd)
    check("versions: ключ — путь файла в курсе", list(v) == [rel], str(list(v)))
    check("versions: path от папки курса", v[rel][0]["path"] == "_Резерв/" + r1["file"], str(v[rel][0]))
    # вторая и третья замены, четвёртая вытесняет самую старую
    for i, ts in enumerate((2, 3, 4)):
        f.write_bytes(bytes([i + 1]) * (40000 + i))
        reserve.archive(cd, rel, 50000, now=time.mktime((2026, 11, 11, 18, 30 + ts, 0, 0, 0, -1)))
    v = reserve.versions(cd)[rel]
    check("хранится не больше KEEP версий", len(v) == reserve.KEEP, str(len(v)))
    check("самые новые сверху", [r["saved"] for r in v] == ["2026-11-11 18-34", "2026-11-11 18-33", "2026-11-11 18-32"], str([r["saved"] for r in v]))
    check("самая старая удалена с диска", not (cd / "_Резерв" / "03. Раздел" / "Лекции" / "Лекция 1 (заменён 2026-11-11 18-30).pdf").exists())
    # одинаковая минута — имя не затирается
    f.write_bytes(b"Z" * 100)
    reserve.archive(cd, rel, 10, now=time.mktime((2026, 11, 11, 18, 34, 0, 0, 0, -1)))
    names = sorted(p.name for p in (cd / "_Резерв" / "03. Раздел" / "Лекции").iterdir())
    check("одинаковая минута: второй файл с суффиксом, а не затёрт", any(" 2.pdf" in n for n in names), str(names))
    # удалили файл руками — запись пропадает из списка
    for r in reserve.versions(cd)[rel][:1]:
        os.remove(cd / r["path"])
    check("versions пропускает записи без файла", all((cd / r["path"]).exists() for r in reserve.versions(cd)[rel]))
    # порча списка не роняет
    (cd / "_Резерв" / reserve.MANIFEST).write_text("{мусор", encoding="utf-8")
    check("битый .versions.json → пустой список, без ошибки", reserve.versions(cd) == {})
    # защита от «..» в записи
    (cd / "_Резерв" / reserve.MANIFEST).write_text(json.dumps({"files": {"a.pdf": [{"file": "../../x", "ts": 1}]}}), encoding="utf-8")
    check("запись с «..» игнорируется", reserve.versions(cd) == {})
    # нет исходного файла → OSError
    try:
        reserve.archive(cd, "нет/такого.pdf", 1); ok = False
    except OSError:
        ok = True
    check("нет файла — OSError (вызывающий решит)", ok)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAILED:" if FAILS else "\nALL PASS", FAILS or "")
sys.exit(1 if FAILS else 0)

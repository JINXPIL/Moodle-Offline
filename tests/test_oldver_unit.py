"""Старые версии программы (oldver.py): что считается файлом программы, версия из имени и из настоящего apk, поиск с оценкой
«старая / такая же / новее / остаток», безопасное удаление. Файлы — во временной папке, настоящие загрузки не трогаем."""
import os, sys, struct, shutil, tempfile, zipfile
from pathlib import Path
import _root

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import oldver

# ── что наше, что нет ──
K = oldver.kind_of
for name, kind in [("MoodleOffline.exe", "exe"), ("MoodleOffline (1).exe", "exe"), ("moodleoffline-1.28.0.EXE", "exe"),
                   ("Moodle-Offline-main.zip", "zip"), ("MoodleOffline-macos-arm64.zip", "zip"), ("MoodleOffline.apk", "apk"),
                   ("MoodleOffline-arm64-v8a.apk", "apk"), ("MoodleOffline-linux-x86_64", "bin"), ("MoodleOffline.exe.old", "leftover"),
                   ("MoodleOffline.exe.new", "leftover"), ("MoodleOffline.exe.new.part", "leftover"), ("MoodleOffline.apk.crdownload", "leftover"),
                   ("MoodleOffline.exe.sha256", "sha"), ("Moodle Offline v1.2.exe", "exe"), ("MoodleOffline_1.0.apk", "apk")]:
    check(f"kind_of: {name} → {kind}", K(name) == kind, K(name))
for name in ["notes.exe", "setup.exe", "MoodleOfflineГайд.pdf", "MoodleOffline.pdf", "MoodleOffline lecture.docx", "Moodle_report.zip",
             "moodle.apk", "MoodleOffline-1.28.0", "readme.txt", "OfflineMoodle.exe", "mymoodleoffline.exe", ""]:
    check(f"kind_of: {name!r} — не наше", K(name) is None, K(name))

check("name_version: из имени", oldver.name_version("MoodleOffline-1.28.0.exe") == "1.28.0" and oldver.name_version("MoodleOffline_v2.3.apk") == "2.3")
check("name_version: нет версии → None", oldver.name_version("MoodleOffline (1).exe") is None and oldver.name_version("MoodleOffline-linux-x86_64") is None
      and oldver.name_version("MoodleOffline-arm64-v8a.apk") is None)
check("compare: старее / одинаково / новее", oldver.compare("1.28.0", "1.29.1") == -1 and oldver.compare("1.29.1", "1.29.1") == 0
      and oldver.compare("1.30", "1.29.9") == 1 and oldver.compare("1.29", "1.29.0") == 0)
check("compare: непонятное → 0 (не решаем за человека)", oldver.compare("abc", "1.0") == 0 and oldver.compare(None, "1.0") == 0)

# ── версия внутри apk ──
HERE = Path(__file__).parent
REAL = (HERE / "data" / "AndroidManifest-1.29.1.bin").read_bytes()          # настоящий манифест из релиза v1.29.1
mv = oldver.manifest_version(REAL)
check("манифест из настоящего apk (v1.29.1): имя и код версии", mv == {"name": "1.29.1", "code": 12901}, mv)
check("обрезанный/битый манифест → None, без исключений", all(oldver.manifest_version(REAL[:n]) is None for n in (0, 3, 8, 40, 200, 3000))
      and oldver.manifest_version(b"\x00" * 100) is None and oldver.manifest_version(os.urandom(500)) is None)

def axml(version="1.2.3", code=10203, utf8=False, root="manifest"):
    """Минимальный двоичный манифест по описанию формата — для кодировки строк, которой в настоящем файле нет."""
    strs = [root, "versionName", "versionCode", version, "package", "x.y"]
    def enc(s):
        if utf8:
            b = s.encode("utf-8"); return bytes([len(s), len(b)]) + b + b"\0"
        return struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
    blobs, offs, pos = [], [], 0
    for s in strs:
        e = enc(s); offs.append(pos); blobs.append(e); pos += len(e)
    data = b"".join(blobs); data += b"\0" * (-len(data) % 4)
    start = 28 + 4 * len(strs)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, start + len(data), len(strs), 0, 0x100 if utf8 else 0, start, 0) + struct.pack(f"<{len(strs)}I", *offs) + data
    attrs = struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 3, 8, 0, 0x03, 3)         # versionName="…" (строка)
    attrs += struct.pack("<IIIHBBI", 0xFFFFFFFF, 2, 0xFFFFFFFF, 8, 0, 0x10, code)   # versionCode (число)
    elem = struct.pack("<HHI", 0x0102, 16, 16 + 20 + len(attrs)) + struct.pack("<II", 1, 0xFFFFFFFF) + \
           struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 2, 0, 0, 0) + attrs
    body = pool + elem
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body
for utf8 in (False, True):
    check(f"манифест (строки {'UTF-8' if utf8 else 'UTF-16'}): версия и код", oldver.manifest_version(axml(utf8=utf8)) == {"name": "1.2.3", "code": 10203},
          oldver.manifest_version(axml(utf8=utf8)))
check("не <manifest> в начале → None", oldver.manifest_version(axml(root="application")) is None)

TMP = Path(tempfile.mkdtemp(prefix="mo_oldver_"))
def make_apk(path, manifest):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", manifest); z.writestr("classes.dex", b"dex")
make_apk(TMP / "a.apk", REAL)
check("apk_version: из настоящего файла", oldver.apk_version(TMP / "a.apk") == {"name": "1.29.1", "code": 12901})
(TMP / "bad.apk").write_bytes(b"PK\x03\x04 partial download")
(TMP / "empty.apk").write_bytes(b"")
with zipfile.ZipFile(TMP / "no.apk", "w") as z: z.writestr("other.txt", "x")
check("apk_version: недокачанный/пустой/не apk → None", all(oldver.apk_version(TMP / n) is None for n in ("bad.apk", "empty.apk", "no.apk", "нет.apk")))

# ── где искать ──
H = TMP / "home"
for n in ("Downloads", "Desktop", "Documents", "Загрузки"):
    (H / n).mkdir(parents=True)
sd = oldver.search_dirs(H, "linux")
check("search_dirs: существующие обычные места, без лишних", {p.name for p in sd} == {"Downloads", "Desktop", "Documents", "Загрузки"}, sd)
EXE_DIR = TMP / "app"; EXE_DIR.mkdir()
check("search_dirs: папка самой программы — первой", oldver.search_dirs(H, "linux", EXE_DIR)[0] == EXE_DIR)
check("search_dirs: повторы убраны", len(oldver.search_dirs(H, "linux", H / "Downloads")) == len(sd))
SH = TMP / "shared"; (SH / "Download").mkdir(parents=True); (SH / "Documents").mkdir()
ad = oldver.search_dirs(H, "android", None, SH)
check("search_dirs: на телефоне — «Download» и «Documents» общей памяти, домашняя папка не ищется", [p.name for p in ad] == ["Download", "Documents", "shared"] and H / "Downloads" not in ad, ad)
check("search_dirs: на телефоне без доступа к общей памяти — пусто", oldver.search_dirs(H, "android", None, None) == [])

# ── поиск ──
D = H / "Downloads"
RUN = EXE_DIR / "MoodleOffline.exe"
RUN.write_bytes(b"R" * 5000)
def put(path, data=b"x", age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = RUN.stat().st_mtime - age
    os.utime(path, (t, t))
    return path
os.utime(RUN, (1_800_000_000, 1_800_000_000))
put(D / "MoodleOffline.exe", b"R" * 5000)                                  # точная копия запущенной — «такая же»
put(D / "MoodleOffline-1.28.0.exe", b"o" * 100, age=86400 * 30)            # старая по имени
put(D / "MoodleOffline-1.30.0.exe", b"n" * 100, age=-86400)                # новее по имени
put(D / "MoodleOffline (2).exe", b"q" * 5000, age=86400 * 5)               # того же размера, другой содержимое, старше — старая по дате
put(D / "MoodleOffline (3).exe", b"z" * 700, age=-86400 * 2)               # версию не понять, скачана позже — не знаем
put(D / "MoodleOffline (4).exe", b"w" * 700, age=0)                        # версию не понять, дата та же — не знаем
put(D / "MoodleOffline.exe.old", b"old")
put(D / "MoodleOffline.apk.crdownload", b"part")
put(D / "MoodleOffline-1.28.0.exe.sha256", b"abc  MoodleOffline-1.28.0.exe\n")   # к старой — старая
put(D / "MoodleOffline-1.30.0.exe.sha256", b"abc  MoodleOffline-1.30.0.exe\n")   # к новой — новее
put(D / "MoodleOffline-9.9.9.apk.sha256", b"abc  gone.apk\n")                     # без самого файла — мусор
make_apk(D / "MoodleOffline-arm64-v8a.apk", REAL)                                # внутри 1.29.1; запущена 1.30.0 → старая
os.utime(D / "MoodleOffline-arm64-v8a.apk", (1_800_000_000 - 100, 1_800_000_000 - 100))
put(D / "notes.exe", b"n"); put(D / "MoodleOffline-guide.pdf", b"p"); put(D / "readme.txt", b"t")
(D / "MoodleOffline-dir.exe").mkdir()                                            # папка с похожим именем
put(D / "MoodleOffline-dir.exe" / "MoodleOffline.exe", b"inside")        # имя папки начинается с «MoodleOffline», поэтому внутри неё тоже смотрим
put(D / "MoodleOffline-1.27" / "MoodleOffline.exe", b"s" * 90, age=86400 * 60)   # подпапка «MoodleOffline-…» — смотрим
put(D / "Другое" / "MoodleOffline.exe", b"s" * 91)                                # чужая подпапка — не смотрим
(D / "MoodleOffline-link.exe").symlink_to(D / "notes.exe")                         # ссылка — не трогаем
put(EXE_DIR / "MoodleOffline.exe.new", b"partial")                               # рядом с запущенной: остаток обновления

r = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN)
by = {x["name"] if x["dir"] == str(D) or x["dir"] == str(EXE_DIR) else x["dir"].split("/")[-1] + "/" + x["name"]: x for x in r["items"]}
names = sorted(by)
check("запущенная программа в список не попала", all(x["path"] != str(RUN) for x in r["items"]))
check("посторонние файлы, папки и ссылки не попали", not any(n in by for n in ("notes.exe", "MoodleOffline-guide.pdf", "readme.txt", "MoodleOffline-link.exe", "MoodleOffline-dir.exe")), names)
check("чужая подпапка пропущена, подпапка «MoodleOffline-…» просмотрена", "Другое/MoodleOffline.exe" not in by and "MoodleOffline-1.27/MoodleOffline.exe" in by
      and "MoodleOffline-dir.exe/MoodleOffline.exe" in by, names)
check("точная копия запущенной → «такая же», не отмечена", by["MoodleOffline.exe"]["status"] == "same" and not by["MoodleOffline.exe"]["checked"], by["MoodleOffline.exe"])
check("старая по имени → «старая», отмечена", by["MoodleOffline-1.28.0.exe"]["status"] == "old" and by["MoodleOffline-1.28.0.exe"]["checked"]
      and "1.28.0" in by["MoodleOffline-1.28.0.exe"]["why"])
check("та же версия по имени → «такая же», не отмечена", by["MoodleOffline-1.30.0.exe"]["status"] == "same" and not by["MoodleOffline-1.30.0.exe"]["checked"], by["MoodleOffline-1.30.0.exe"])
r2 = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.29.1", running=RUN)
by2 = {x["name"]: x for x in r2["items"] if x["dir"] == str(D)}
check("новее запущенной (1.30.0 при запущенной 1.29.1) → «новее», не отмечена", by2["MoodleOffline-1.30.0.exe"]["status"] == "newer" and not by2["MoodleOffline-1.30.0.exe"]["checked"], by2["MoodleOffline-1.30.0.exe"])
check("версия 1.29.1 при запущенной 1.29.1 → «такая же»", by2["MoodleOffline-arm64-v8a.apk"]["status"] == "same" and by2["MoodleOffline-arm64-v8a.apk"]["version"] == "1.29.1")
check("apk с версией внутри (1.29.1 при запущенной 1.30.0) → «старая», версия из файла", by["MoodleOffline-arm64-v8a.apk"]["status"] == "old"
      and by["MoodleOffline-arm64-v8a.apk"]["version"] == "1.29.1" and by["MoodleOffline-arm64-v8a.apk"]["checked"])
check("той же длины, другое содержимое, скачана раньше → «старая» по дате", by["MoodleOffline (2).exe"]["status"] == "old" and "раньше" in by["MoodleOffline (2).exe"]["why"], by["MoodleOffline (2).exe"])
check("версию не понять, скачана позже → «не знаю», не отмечена", by["MoodleOffline (3).exe"]["status"] == "unknown" and not by["MoodleOffline (3).exe"]["checked"]
      and "позже" in by["MoodleOffline (3).exe"]["why"])
check("версию не понять, дата та же → «не знаю», не отмечена", by["MoodleOffline (4).exe"]["status"] == "unknown" and not by["MoodleOffline (4).exe"]["checked"])
check("остатки обновления → мусор, отмечены", all(by[n]["status"] == "junk" and by[n]["checked"] for n in ("MoodleOffline.exe.old", "MoodleOffline.apk.crdownload")))
check("остаток обновления рядом с запущенной → мусор", by["MoodleOffline.exe.new"]["status"] == "junk")
check(".sha256 берёт оценку у своего файла", by["MoodleOffline-1.28.0.exe.sha256"]["status"] == "old" and by["MoodleOffline-1.28.0.exe.sha256"]["checked"]
      and by["MoodleOffline-1.30.0.exe.sha256"]["status"] == by["MoodleOffline-1.30.0.exe"]["status"], by["MoodleOffline-1.28.0.exe.sha256"])
check(".sha256 без своего файла → мусор", by["MoodleOffline-9.9.9.apk.sha256"]["status"] == "junk" and by["MoodleOffline-9.9.9.apk.sha256"]["checked"])
st = [x["status"] for x in r["items"]]
check("список: сначала старые и мусор, потом неясные, потом новее и такие же", st == sorted(st, key=lambda s: oldver.STATUS_ORDER[s]), st)
check("итоги: сколько всего и сколько отмечено", r["total"] == sum(x["size"] for x in r["items"]) and r["to_delete"] == sum(x["size"] for x in r["items"] if x["checked"]) and 0 < r["to_delete"] < r["total"])
check("в ответе нет служебных полей", not any("_mt" in x for x in r["items"]))
check("id стабильные и разные", len({x["id"] for x in r["items"]}) == len(r["items"]) and {x["id"] for x in r["items"]} == {x["id"] for x in oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN)["items"]})

rb = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN, busy=[str(EXE_DIR / "MoodleOffline.exe.new")])
check("файлы, которые сейчас нужны обновлению, не показываются", all(x["name"] != "MoodleOffline.exe.new" for x in rb["items"]))
rs = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN, skip=[D / "MoodleOffline-1.27"])
check("папки из skip не просматриваются", all("MoodleOffline-1.27" not in x["dir"] for x in rs["items"]))
rn = oldver.scan(oldver.search_dirs(H, "linux", None), "1.30.0", running=None)
check("запущено не из exe (исходники/телефон): работает по версии, без даты", {x["name"]: x["status"] for x in rn["items"] if x["dir"] == str(D)}["MoodleOffline (3).exe"] == "unknown"
      and {x["name"]: x["status"] for x in rn["items"] if x["dir"] == str(D)}["MoodleOffline-1.28.0.exe"] == "old")
check("нет папок — пустой список, не ошибка", oldver.scan([], "1.0")["items"] == [] and oldver.scan([TMP / "нет такой"], "1.0")["items"] == [])
big = TMP / "big"; big.mkdir()
for i in range(oldver.MAX_ITEMS + 25):
    (big / f"MoodleOffline-0.{i}.0.exe").write_bytes(b"x")
rbig = oldver.scan([big], "9.0.0")
check("потолок на число найденного", len(rbig["items"]) == oldver.MAX_ITEMS and rbig["truncated"] is True)

# ── удаление ──
found = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN)
checked = [x for x in found["items"] if x["checked"]]
keep = [x for x in found["items"] if not x["checked"]]
ro = D / "MoodleOffline-1.28.0.exe"; os.chmod(ro, 0o444)
res = oldver.delete(found, [x["id"] for x in checked] + ["abc123", "ffffffffffffffff"])
check("удалены отмеченные, освобождённое место посчитано", len(res["deleted"]) == len(checked) and res["freed"] == sum(x["size"] for x in checked), res)
check("файлы с диска исчезли (в том числе «только чтение»)", all(not Path(x["path"]).exists() for x in checked))
check("неотмеченные и чужие целы", all(Path(x["path"]).exists() for x in keep) and RUN.exists() and (D / "notes.exe").exists() and (D / "readme.txt").exists()
      and (D / "Другое" / "MoodleOffline.exe").exists() and (D / "MoodleOffline-dir.exe" / "MoodleOffline.exe").exists())
check("чужие идентификаторы не удаляют ничего и честно названы", sum(1 for f in res["failed"] if "нет в списке" in f["why"]) == 2, res["failed"])

f2 = oldver.scan(oldver.search_dirs(H, "linux", EXE_DIR), "1.30.0", running=RUN)
victim = next(x for x in f2["items"] if x["name"] == "MoodleOffline (3).exe")
os.remove(victim["path"])
r3 = oldver.delete(f2, [victim["id"]])
check("файл пропал между поиском и удалением → понятная ошибка, без падения", r3["deleted"] == [] and len(r3["failed"]) == 1 and r3["freed"] == 0, r3)
v2 = next(x for x in f2["items"] if x["name"] == "MoodleOffline (4).exe")
os.remove(v2["path"]); (TMP / "secret.txt").write_text("не трогать"); os.symlink(TMP / "secret.txt", v2["path"])
r4 = oldver.delete(f2, [v2["id"]])
check("на месте файла появилась ссылка → не удаляем (и цель цела)", r4["deleted"] == [] and (TMP / "secret.txt").exists(), r4)
check("пустой/странный список → ничего", oldver.delete(f2, []) == {"deleted": [], "failed": [], "freed": 0} and oldver.delete(f2, None)["deleted"] == [])

shutil.rmtree(TMP, ignore_errors=True)
print("\nИТОГО:", "всё хорошо" if not FAILS else f"ПРОВАЛЕНО {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)

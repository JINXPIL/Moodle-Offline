"""Английский README (README.en.md) и переключатель языка.

Английская версия — самостоятельное руководство, а не построчный перевод русского README (он в 20 раз длиннее), поэтому проверяется
то, что расходится незаметно: ссылки между файлами, якоря оглавления, отсылки «(RU §…)» на разделы русского README, ключи командной
строки, упомянутые файлы и что текст действительно английский."""
import re, sys
from pathlib import Path
import _root
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
RU = (ROOT / "README.md").read_text(encoding="utf-8")
EN = (ROOT / "README.en.md").read_text(encoding="utf-8")
core = (ROOT / "moodle_offline.py").read_text(encoding="utf-8")
runsh = (ROOT / "run.sh").read_text(encoding="utf-8")

top = lambda t: "\n".join(t.split("\n")[:8])
check("1. вверху русского README есть переключатель на английский", "README.en.md" in top(RU))
check("2. вверху английского README есть переключатель на русский", "](README.md)" in top(EN))
check("3. в каждом файле своя сторона переключателя — не ссылка (жирным), чтобы было видно, где вы", "**Русский**" in top(RU) and "**English**" in top(EN))

# 4. оглавление английского README: якоря совпадают с настоящими заголовками (так их строит GitHub)
def slug(h):
    h = h.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h, flags=re.U)
    return h.replace(" ", "-")
heads = {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", EN, re.M)}
anchors = re.findall(r"\]\(#([^)]+)\)", EN)
check("4. в английском README есть оглавление, и каждая его ссылка ведёт на существующий заголовок",
      len(anchors) >= 10 and all(a in heads for a in anchors), str([a for a in anchors if a not in heads]))

# 5. «(RU §4б)» — такой раздел есть в русском README
ru_heads = re.findall(r"^## (\S+?)\.\s", RU, re.M)
refs = set()
for m in re.finditer(r"RU §([^)*]+)", EN):
    for part in re.split(r"[,;]| and ", m.group(1)):
        for x in re.findall(r"§?\s*([0-9]+[а-яё]?(?:-[0-9]+)?)", part):
            refs.add(x)
check("5. отсылок «RU §…» много, и каждая указывает на раздел, который есть в русском README",
      len(refs) >= 20 and all(r in ru_heads for r in refs), f"{len(refs)} отсылок; нет: {sorted(r for r in refs if r not in ru_heads)}")

# 6. ключи командной строки, названные в английском тексте, существуют
flags = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", EN))
missing = sorted(f for f in flags if f'"{f}"' not in core and f"'{f}'" not in core and f not in runsh and f != "--help")
check("6. каждый ключ `--…` из английского README есть в программе (moodle_offline.py или run.sh)", len(flags) >= 25 and not missing, f"{len(flags)} ключей; нет: {missing}")

# 7. относительные ссылки и картинки ведут на существующие файлы
bad = []
for m in re.finditer(r"!?\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)", EN):
    tgt = m.group(1)
    if re.match(r"[a-z]+://|mailto:", tgt): continue
    if not (ROOT / tgt).exists(): bad.append(tgt)
check("7. все локальные ссылки и картинки английского README указывают на существующие файлы", not bad, str(bad))

# 8. файлы релизов, которые перечислены как скачиваемые, названы так же, как в сборке и в русском README
for f in ("MoodleOffline.exe", "MoodleOffline.apk", "MoodleOffline-arm64-v8a.apk", "MoodleOffline-armeabi-v7a.apk", "MoodleOffline-x86_64.apk",
          "MoodleOffline-linux-x86_64", "MoodleOffline-macos-arm64.zip", "MoodleOffline-macos-x86_64.zip"):
    check(f"8. файл релиза {f} назван одинаково в обоих README", f in EN and f in RU, f)

# 9. все вкладки программы упомянуты (под своими русскими названиями — по ним их ищут в окне)
for tab in ("Курсы", "Что нового", "Поиск", "Календарь", "Заметки", "Расписание", "Элективы", "Нейросеть", "Перенос данных", "Вид"):
    check(f"9. вкладка/раздел «{tab}» есть в английском README", tab in EN, tab)

# 10. английский — английский: кириллицы немного (названия кнопок в кавычках), основная масса — латиница
cyr = sum(1 for c in EN if "а" <= c.lower() <= "я" or c.lower() == "ё")
lat = sum(1 for c in EN if c.isascii() and c.isalpha())
check("10. в английском README кириллицы не больше 6 % от букв (только названия интерфейса)", cyr < 0.06 * (cyr + lat), f"{cyr} / {cyr + lat}")

secret = re.compile(r"sesskey=|MoodleSession=\S{6,}|Bearer\s+[A-Za-z0-9_\-]{16,}|password\s*=\s*\S{4,}|api[_-]?key\s*=\s*\S{8,}", re.I)
check("11. в английском README нет ключей, токенов, cookies и паролей", not secret.search(EN))
check("12. в русском README есть строка про README.en.md в таблице файлов проекта", re.search(r"^\| `README\.en\.md` \|", RU, re.M) is not None)

print("\nИТОГО:", "всё хорошо" if not FAILS else f"провалено {len(FAILS)}")
sys.exit(1 if FAILS else 0)

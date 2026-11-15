"""История изменений (CHANGELOG.md — по-русски, CHANGELOG.en.md — перевод).

Проверяется то, что ломается незаметно: поднял `VERSION`, а запись в истории не добавил; дописал русский текст и забыл
английский; сбилась нумерация. Читаемость и точность формулировок тест не оценивает — это на совести того, кто пишет."""
import re, sys
from pathlib import Path
import _root
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
RU = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
EN = (ROOT / "CHANGELOG.en.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
core = (ROOT / "moodle_offline.py").read_text(encoding="utf-8")
VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', core, re.M).group(1)

HEAD = re.compile(r"^## (\d+\.\d+\.\d+) — (\d{4}-\d{2}-\d{2})$", re.M)
def entries(text):
    """[(версия, дата, тело записи)] в порядке файла."""
    ms = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((m.group(1), m.group(2), text[m.end():end]))
    return out
ru, en = entries(RU), entries(EN)
key = lambda v: tuple(int(x) for x in v.split("."))

check("1. в обоих файлах есть записи", len(ru) > 20 and len(en) > 20, f"{len(ru)} / {len(en)}")
check("2. верхняя запись — текущая версия (VERSION в moodle_offline.py): поднял версию — допиши историю",
      ru and ru[0][0] == VERSION and en and en[0][0] == VERSION, f"VERSION={VERSION}, ru={ru[0][0] if ru else None}, en={en[0][0] if en else None}")
check("3. русский и английский файлы — одни и те же версии с теми же датами, в том же порядке",
      [(v, d) for v, d, _ in ru] == [(v, d) for v, d, _ in en],
      str(sorted(set((v, d) for v, d, _ in ru) ^ set((v, d) for v, d, _ in en))))
vers = [v for v, _, _ in ru]
check("4. версии не повторяются и идут от новых к старым", len(vers) == len(set(vers)) and vers == sorted(vers, key=key, reverse=True), str(vers))
dates = [d for _, d, _ in ru]
check("5. даты не растут сверху вниз (новая версия не может быть старше прежней)", dates == sorted(dates, reverse=True), str(dates))
check("6. самая первая запись — 1.0.0", vers[-1] == "1.0.0", vers[-1])
check("7. ни одна запись не пустая", all(len(b.strip()) > 40 for _, _, b in ru + en))

# перевод не отстаёт от оригинала: у каждой версии столько же пунктов списка и столько же подзаголовков (**Новое** …)
def shape(body):
    return (len(re.findall(r"^- ", body, re.M)), len(re.findall(r"^\*\*[^*]+\*\*\s*$", body, re.M)))
bad = [v for (v, _, a), (_, _, b) in zip(ru, en) if shape(a) != shape(b)]
check("8. у каждой версии одинаковое число пунктов и подзаголовков в русском и английском тексте", not bad, str(bad))

# записи без отдельного релиза называют, куда вошли
nr_ru = [v for v, _, b in ru if "Отдельного релиза нет — вошла в v" in b]
nr_en = [v for v, _, b in en if "No separate release — shipped in v" in b]
check("9. «без отдельного релиза» есть и в русском, и в английском — для одних и тех же версий", nr_ru == nr_en and len(nr_ru) >= 5, f"{nr_ru} / {nr_en}")
refs = re.findall(r"вошла в v(\d+\.\d+\.\d+)", RU)
check("10. на какой релиз указано «вошла в vX» — такая версия есть в истории", all(r in vers for r in refs), str([r for r in refs if r not in vers]))

# ссылки друг на друга и из README
check("11. файлы ссылаются друг на друга", "CHANGELOG.en.md" in RU and "CHANGELOG.md" in EN)
check("12. README ведёт на оба файла", "CHANGELOG.md" in README and "CHANGELOG.en.md" in README)

# в историю не должно попасть личное
secret = re.compile(r"sesskey=|MoodleSession=|Bearer\s+[A-Za-z0-9_\-]{16,}|password\s*=\s*\S{4,}|api[_-]?key\s*=\s*\S{8,}", re.I)
check("13. в истории нет ключей, токенов, cookies и паролей", not secret.search(RU) and not secret.search(EN))
check("14. английский текст — действительно английский (в основном латиница вне цитат интерфейса)",
      sum(c.isascii() and c.isalpha() for c in EN) > 4 * sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in EN), "слишком много кириллицы")

print("\nИТОГО:", "всё хорошо" if not FAILS else f"провалено {len(FAILS)}")
sys.exit(1 if FAILS else 0)

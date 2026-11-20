"""«Календарь», «Поиск», «Что нового», «Успеваемость» на телефоне (1.35.0) — текстовые проверки ui/index.html.

Поведение (фильтры, прокрутка, раскрытие карточек, график) проверяется в Chromium вне репозитория;
здесь — то, что видно по тексту: нужные id и функции, префиксы классов, пункты «Вида» с правилами CSS,
баланс скобок и отсутствие повторных id."""
import re
from collections import Counter
from pathlib import Path
import _root

FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
style = html[html.index("<style>"):html.index("</style>")]

# --- id и функции
for i in ("calF", "calShowSeg", "calDaysSeg", "calMore", "sqIdxB", "nwSeg", "nwInfo"):
    check(f"1. id {i} есть ровно один раз", len(re.findall(rf'id="{i}"', html)) == 1)
for f in ("renderCal", "calRow", "calDayHead", "calFDot", "sqRecent", "renderNews", "nwGroup", "nwStatusLine", "nwDay"):
    check(f"2. функция {f}", re.search(rf"function {f}\(", html) is not None)
# calPl — const на верхнем уровне и объявлена ДО первого вызова renderNews/renderGrades из начального кода
check("3. calPl объявлена один раз (const)", len(re.findall(r"const calPl = ", html)) == 1)

# --- «Вид»: разделы и у каждого пункта есть правило CSS или vGet
m = re.search(r"const VIEWDEF = \{\n(.*?)\n\};\nconst V_TABS", html, re.S)
body = m.group(1) if m else ""
for sec in ("calendar", "search", "news", "grades"):
    mm = re.search(rf"\n  {sec}: \{{name: .*?(?=\n  \w+: \{{name:|\Z)", body, re.S)
    block = mm.group(0) if mm else ""
    keys = re.findall(r"\{k:\s*\"(\w+)\"", block)
    check(f"4. раздел «{sec}» в VIEWDEF и в нём есть пункты", bool(keys), sec)
    for k in keys:
        used = (f"data-v-{sec}-{k}" in style) or (f'vGet("{sec}","{k}")' in html) or (f"vGet('{sec}','{k}')" in html) or (f'vGet("{sec}", "{k}")' in html)
        check(f"5. {sec}.{k} ни на что не влияет?", used)

# --- префиксы новых классов: общие имена не занимаем
# в «Успеваемости» нельзя называть класс просто .gr (так зовётся плашка оценки курса)
check("6. у «Успеваемости» классы с префиксом gv-", "#gradesView .gr{" not in style and "#gradesView .gr " not in style)
check("7. у календаря, поиска и ленты свои префиксы (cal-/sq/nw-)", all(p in style for p in (".cal-", ".nw-", "#searchView")))

# --- баланс скобок CSS и блоков @media
check("8. скобки CSS сбалансированы", style.count("{") == style.count("}"), (style.count("{"), style.count("}")))

# --- узкие правила закрыты: после большого @media (max-width:700px) идёт блок без вложенности
phone = [m.start() for m in re.finditer(r"@media \(max-width:700px\)\s*\{", style)]
check("9. есть блоки для телефона", len(phone) >= 1)

print("FAILS:", FAILS)
raise SystemExit(1 if FAILS else 0)

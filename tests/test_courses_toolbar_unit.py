"""«Курсы» без правой колонки (1.25.0): «Скачать выбранные» и «Синхронизация» — в строке над карточками (ПК),
плашка над нижними вкладками, пока выбран курс (телефон). Поведение проверено в Chromium (стенд вне репозитория);
здесь — то, что можно проверить текстом (ui/index.html)."""
import re, sys
from pathlib import Path
import _root
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
a = html.index("<style>") + 7; b = html.index("</style>")
css = re.sub(r"/\*.*?\*/", "", html[a:b], flags=re.S)
script = html[html.index("<script>"):]

def stack_at(text, pos):
    """Какие @-правила охватывают позицию pos (для поиска «внутри @media (max-width:700px)»)."""
    st = []; last = 0
    for i, ch in enumerate(text[:pos]):
        if ch == "{": st.append(text[last:i].strip()); last = i + 1
        elif ch == "}": st.pop() if st else None; last = i + 1
        elif ch == ";": last = i + 1
    return st

cv = html[html.index('id="coursesView"'):html.index('id="scheduleView"')]
tb = cv[cv.index('<div class="toolbar">'):cv.index('<div class="grid" id="grid">')]

# --- разметка
check("1. в «Курсах» нет правой колонки (<aside class=side>)", "<aside" not in cv and 'class="side"' not in cv)
check("2. кнопки скачивания — в строке .toolbar над карточками, один раз каждая", 'id="dlBar"' in tb and tb.count('id="startBtn"') == 1 and tb.count('id="syncBtn"') == 1)
order = [tb.index(x) for x in ['id="selAll"', 'id="selNone"', 'id="dlBar"', 'id="startBtn"', 'id="syncBtn"', 'data-vw="courses"', 'id="refreshBtn"']]
check("3. порядок: «Выбрать все», «Снять», «Скачать выбранные», «Синхронизация», «Вид», «Обновить»", order == sorted(order), str(order))
check("4. #selInfo (подпись под кнопками) убран и из разметки, и из скрипта", "selInfo" not in html)
check("5. #startBtn / #syncBtn в странице однократно", html.count('id="startBtn"') == 1 and html.count('id="syncBtn"') == 1)
check("6. подсказка про закрытие окна переехала в title кнопки", 'закрыть окно во время загрузки' in script and '$("#startBtn").title' in script)
check("7. у «Синхронизации» есть title с полным названием (быстрая, только новое)", re.search(r'id="syncBtn" title="Быстрая синхронизация[^"]*только новое', tb) is not None)

# --- ПК
check("8. колонка «Курсы» одна и не шире окна: #coursesView .layout{grid-template-columns:minmax(0,1fr)}", re.search(r"#coursesView \.layout\{grid-template-columns:minmax\(0,1fr\)\}", css) is not None)
check("9. #app: колонка minmax(0,1fr) — длинная строка кнопок не растягивает страницу и не обрезает край", re.search(r"#app\{display:grid;grid-template-columns:minmax\(0,1fr\);", css) is not None)
check("10. на ПК .dlbar — display:contents (кнопки — обычные элементы строки)", re.search(r"(?m)^\.dlbar\{display:contents\}", css) is not None)
m = re.search(r"@media \(min-width:701px\)\{#coursesView \.search\{min-width:(\d+)px\}\}", css)
check("11. поиск на ПК сжимается до 150 px, чтобы строка помещалась в окно 1280", bool(m) and int(m.group(1)) <= 160)

# --- телефон
def phone_rule(sel):
    i = css.find(sel)
    return (i, stack_at(css, i)) if i >= 0 else (-1, [])
i, st = phone_rule(".dlbar{display:none}")
check("12. без выбора плашки нет (.dlbar{display:none} внутри @media (max-width:700px))", i >= 0 and any("max-width:700px" in x for x in st), str(st))
i, st = phone_rule("#coursesView.has-sel .dlbar{")
rule = css[i:css.index("}", i)] if i >= 0 else ""
check("13. с выбором — плашка position:fixed внутри @media (max-width:700px)", i >= 0 and any("max-width:700px" in x for x in st) and "position:fixed" in rule, str(st))
check("14. плашка стоит над нижними вкладками (bottom использует --bnav-h и safe-area)", "--bnav-h" in rule and "safe-area-inset-bottom" in rule)
z = re.search(r"z-index:(\d+)", rule)
check("15. z-index плашки ниже листа «Загрузки» (48) и окон (50)", bool(z) and int(z.group(1)) < 48, rule[:80])
check("16. последний ряд карточек не прячется под плашкой (padding-bottom от --dl-h)", re.search(r"#coursesView\.has-sel \.content\{padding-bottom:calc\(var\(--dl-h", css) is not None)
check("17. кнопка скачивания забирает свободное место, «Синхронизация» — по тексту", ".dlbar .btn.primary{flex:1 1 8em}" in css and ".dlbar #syncBtn{flex:0 1 auto" in css)

# --- скрипт
fn = script[script.index("function updateSelInfo"):script.index("async function loadCourses")]
check("18. updateSelInfo ставит класс has-sel на #coursesView (n > 0)", 'classList.toggle("has-sel", n > 0)' in fn and '$("#coursesView")' in fn)
check("19. updateSelInfo: «Скачать выбранные (N)» / без числа, неактивны без выбора и во время загрузки", "Скачать выбранные (${n})" in fn and '"Скачать выбранные"' in fn and "disabled = !n || busy" in fn)
sb = script[script.index("function setBarVars"):script.index("addEventListener(\"resize\", setBarVars)")]
check("20. setBarVars пишет --dl-h, а ResizeObserver следит за #dlBar", '"--dl-h"' in sb and 'ro.observe($("#dlBar"))' in sb)
st_fn = script[script.index("async function start("):script.index("async function start(") + 900]
check("21. подсказка «окно можно закрыть» — один раз за запуск и не на телефоне (там своя в showDock)", "bgHinted" in st_fn and "!NARROW.matches" in st_fn and "let bgHinted = false" in script)
check("22. кнопки по-прежнему запускают загрузку и синхронизацию", '$("#startBtn").onclick = () => start(false)' in script and '$("#syncBtn").onclick = () => start(true)' in script)
check("23. плашка — внутри .toolbar: в окне курса (строка прячется) она тоже скрыта", 'classList.add("hidden")' in script and '$(".content .toolbar").classList.add("hidden")' in script)

print("\nИТОГО:", "всё хорошо" if not FAILS else f"провалено {len(FAILS)}")
sys.exit(1 if FAILS else 0)

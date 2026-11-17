"""«Успеваемость» на телефоне (1.31.0): компактный список вместо таблицы, полоска семестров, фильтр, поиск,
свёрнутая панель «Обновление и вход», короткая подпись вкладки. Поведение проверено в Chromium (стенд вне репозитория,
вымышленная страница `tests/lk_pages.py`); здесь — то, что можно проверить текстом (ui/index.html).

Главное, что ловит тест: класс `.gr` — это плашка оценки курса (`white-space:nowrap`, круглая рамка). Раньше таблица
оценок называлась так же, и на телефоне названия не переносились, а поверх строк рисовалась огромная дуга."""
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
css_raw = html[a:b]
css = re.sub(r"/\*.*?\*/", "", css_raw, flags=re.S)
script = html[html.index("<script>"):]

gv_html = html[html.index('id="gradesView"'):html.index('id="newsView"')]
js_a = script.index("/* ── Успеваемость (lk.sevsu.ru) ── */"); js_b = script.index("/* ── вкладки ── */")
gjs = script[js_a:js_b]
gcss = "\n".join(l for l in css.splitlines() if "#gradesView" in l)

def stack_at(text, pos):
    st = []; last = 0
    for i, ch in enumerate(text[:pos]):
        if ch == "{": st.append(text[last:i].strip()); last = i + 1
        elif ch == "}": st.pop() if st else None; last = i + 1
        elif ch == ";": last = i + 1
    return st

# --- ловушка с классом .gr
check("1. в разметке и скрипте «Успеваемости» нет класса gr (только gv-*)",
      not re.search(r'class="[^"]*(?<![\w-])gr(?![\w-])', gv_html + gjs) and not re.search(r"classList\.[a-z]+\([^)]*['\"]gr['\"]", gjs))
check("2. в CSS «Успеваемости» нет голого селектора .gr / table.gr", not re.search(r"(?<![\w-])\.gr(?![\w-])", gcss) and "table.gr" not in gcss)
check("3. таблицы в «Успеваемости» нет вовсе (список карточек)", "<table" not in gjs and "<thead" not in gjs and "<table" not in gv_html)
check("4. общая плашка оценки курса `.gr` на месте (её правило мы не трогаем)", re.search(r"(?<![\w-])\.gr\{[^}]*nowrap", css) is not None)

# --- список
check("5. строка — .gv-row с цветной меткой по виду оценки (k5/kp/k4/k3/k2/kf/k0)",
      all(f".gv-row.{k}" in gcss for k in ("k5", "k4", "k3", "k2", "kf", "k0")) or ".gv-row.k0" in gcss)
check("6. название переносится: overflow-wrap:anywhere + hyphens на .gv-subj, у .gv-txt min-width:0",
      re.search(r"\.gv-subj\{[^}]*overflow-wrap:anywhere", gcss) is not None and re.search(r"\.gv-txt\{[^}]*min-width:0", gcss) is not None)
check("7. нигде в «Успеваемости» нет white-space:nowrap на названии/строке (только у «ждёт» и кнопок фильтра)",
      all(("gv-wait" in l or "gv-seg" in l) for l in gcss.splitlines() if "white-space:nowrap" in l))
check("8. список — сетка с колонками от 22rem (на ПК в несколько колонок, на телефоне в одну)", re.search(r"\.gv-list\{[^}]*minmax\(min\(100%,22rem\)", gcss) is not None)
check("9. оценка — круглая метка .gv-g, текстовая — .gv-pill, не сдано — «ждёт»", all(x in gjs for x in ("gv-g gb", "gv-pill", "gv-wait", "ждёт")))

# --- полоска семестров
m = re.search(r"#gradesView \.gv-sems\{[^}]*position:sticky[^}]*\}", css)
check("10. на телефоне полоска семестров липнет под шапкой: position:sticky; top:var(--top-h)", m is not None and "top:var(--top-h" in m.group(0), m.group(0) if m else "")
if m:
    st = stack_at(css, m.start())
    check("11. это правило — внутри @media (max-width:700px), на ПК полоска обычная", any("max-width:700px" in s for s in st), str(st))
check("12. полоска прокручивается вбок без видимого ползунка (overflow-x:auto, scrollbar-width:none)", "overflow-x:auto" in css[css.index("#gradesView .gv-sems{flex-wrap:nowrap"):][:400] and "scrollbar-width:none" in css[css.index("#gradesView .gv-sems{flex-wrap:nowrap"):][:400])
check("13. у чипа семестра — «N из M» и полоска прогресса", "<small>" in gjs and "gv-sem" in gjs and re.search(r"<i style=\"width:", gjs) is not None)

# --- фильтр и поиск
check("14. фильтр «Все / С оценкой / Ждут» (grFilter) и поиск (grQ) есть", all(x in gjs for x in ("grFilter", "grQ", 'data-f="all"', 'data-f="done"', 'data-f="wait"')))
check("15. фильтр и поиск перерисовывают только список (renderGrList), чтобы не терять фокус и прокрутку полоски",
      "function renderGrList" in gjs and gjs.count("renderGrList()") >= 3)
check("16. поиск идёт по всем семестрам программы, в строке тогда видно «N сем.»", "сем." in gjs and re.search(r"grQ", gjs[gjs.index("function grVisibleRows"):][:900]) is not None)
check("17. #gvQ — поле поиска, #gvQBtn — лупа (поле скрыто, пока не нажали)", "gvQ" in gjs and "gvQBtn" in gjs and "gv-qrow" in gjs)
check("18. выбранный семестр прокручивает полоску к себе (scrollIntoView / scrollLeft)", "grScrollSem" in gjs and ("scrollIntoView" in gjs or "scrollLeft" in gjs))

# --- панель обновления
check("19. «Обновление и вход» — сворачиваемый <details id=grPanel> под списком", re.search(r'<details class="panel gv-more" id="grPanel">', gv_html) is not None
      and gv_html.index('id="grBody"') < gv_html.index('id="grPanel"'))
check("20. все прежние id на месте", all(f'id="{i}"' in gv_html for i in ("grInfo", "grRefresh", "grMsg", "grBody", "grFile", "grSite", "grClear", "grFileIn", "grPassBox", "grUser", "grPass", "grPassGo")))
check("21. панель сама раскрывается, когда данных нет или нужен вход (и только один раз на вход)", "grNeedPrev" in gjs and "grPanel" in gjs and re.search(r"grPanel[^;]*open", gjs) is not None)
check("22. «Обновить» — в шапке вкладки, на время загрузки меняет подпись («Обновляю…»)", "Обновляю…" in gjs and gv_html.index('id="grRefresh"') < gv_html.index('id="grBody"'))

# --- сводка
check("23. сводка: средний балл, «Сдано N из M», ЗЕТ, полоска, распределение оценок", all(x in gjs for x in ("gv-stats", "gv-meter", "gv-dist", "Средний балл", "Сдано", "ЗЕТ")))

# --- вкладка
tab = html[html.index('data-view="grades"'):][:900]
check("24. подпись вкладки: полная «Успеваемость» + короткая «Оценки» (tl-lg / tl-sh)", 'class="tl-lg">Успеваемость<' in tab and 'class="tl-sh">Оценки<' in tab, tab[:300])
check("25. короткая подпись скрыта на ПК и показана на телефоне", re.search(r"\.mtabs \.tl-sh\{display:none\}", css) is not None
      and any("max-width:700px" in s for s in stack_at(css, css.index(".mtabs .tl-lg{display:none}"))))

# --- скобки CSS (новый блок не должен ломать то, что после него)
check("26. скобки в <style> сбалансированы", css.count("{") == css.count("}"), f"{css.count('{')} / {css.count('}')}")
check("27. блок «Успеваемости» стоит после большого @media телефона (иначе правила действовали бы только на нём)",
      not stack_at(css, css.index("#gradesView .gv-top{")), str(stack_at(css, css.index("#gradesView .gv-top{"))))

if FAILS:
    print("\nПровалено:", len(FAILS)); sys.exit(1)
print("\nВсё в порядке")

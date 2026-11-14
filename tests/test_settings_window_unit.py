"""Окно «Настройки» по шестерёнке, короткий статус Moodle, фильтры и карточки курсов на телефоне (ui/index.html).
Поведение проверено в Chromium (телефон 390/360/320 px, крупный шрифт, ПК) — стенд вне репозитория; здесь — то, что можно проверить текстом."""
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

# --- таблица стилей: скобки сходятся (раньше не хватало «}» у @media (max-width:700px) — всё после действовало только на телефоне)
check("1. в таблице стилей скобки { } сходятся", css.count("{") == css.count("}"), f"{css.count('{')} / {css.count('}')}")
depth = 0; bad = []
for ln, line in enumerate(css.split("\n"), 1):
    if line and not line[0].isspace() and line[0] != "}" and depth != 0: bad.append(ln)
    depth += line.count("{") - line.count("}")
check("2. каждое правило верхнего уровня начинается на нулевой глубине", not bad, f"строки стиля: {bad[:5]}")

# --- окно «Настройки»
m = re.search(r'<div class="modal hidden" id="setModal">(.*?)\n</div></div>\n<div class="modal hidden" id="modal">', html, re.S)
check("3. окно #setModal есть и стоит раньше окна файла (остальные окна лягут поверх)", bool(m))
body = m.group(1) if m else ""
check("4. внутри окна кнопка закрытия с id, оканчивающимся на Close", 'id="setClose"' in body)
for key in ['id="modeBox"', 'data-s="zip"', 'id="outDir"', 'id="rulesBtn"', 'data-s="wait_for_server"', 'id="watchOpen"', 'id="trOpen"', 'id="mcpPanel"', 'id="mcpOpen"', 'id="updPanel"', 'id="updCheck"', 'id="updAuto"']:
    check(f"5. в «Настройках» есть {key}", key in body)
check("6. в окне ровно четыре панели", len(re.findall(r'<div class="panel"', body)) == 4, str(len(re.findall(r'<div class="panel"', body))))
# боковая колонка «Курсы» — только скачивание
side = re.search(r'<aside class="side">(.*?)</aside>', html.split('id="coursesView"', 1)[1], re.S).group(1)
check("7. в колонке «Курсы» остались только кнопки скачивания", side.count('<div class="panel"') == 1 and 'id="startBtn"' in side and 'id="syncBtn"' in side and 'id="updPanel"' not in side and 'data-s="zip"' not in side)
for i in ['id="startBtn"', 'id="syncBtn"', 'id="selInfo"']:
    check(f"8. {i} на месте (однократно)", html.count(i) == 1)
markup = html[html.index("<body>"):html.index("<script>")]
ids = re.findall(r'\bid="([^"]+)"', markup)
dup = sorted({i for i in ids if ids.count(i) > 1})
check("9. id в разметке страницы не повторяются (кроме шаблонов в скрипте)", not dup, str(dup[:8]))

# --- шестерёнка
check("10. в шапке есть #setBtn после «Оформления» и до панели #topProg", html.index('id="themeBtn"') < html.index('id="setBtn"') < html.index('id="topProg"'))
check("11. шестерёнка скрыта на экране входа (тот же toggle, что у вкладок)", '$("#setBtn").classList.toggle("hidden", !ok)' in html)
check("12. шестерёнка открывает окно, «Закрыть» и щелчок по затемнению закрывают", all(x in html for x in ('$("#setBtn").onclick = setOpen', '$("#setClose").onclick = setHide', 'e.target === e.currentTarget')))
check("13. плашка обновления открывает настройки (goUpdPanel → setOpen)", "function goUpdPanel(){ setOpen();" in html)
check("14. на телефоне шестерёнка последняя в шапке (order:6)", "#setBtn{order:6}" in html)
check("15. окно настроек на ПК 640 px только в широких экранах (на телефоне — весь экран)", "@media (min-width:701px){.setbox{width:min(640px,94vw)}}" in html)

# --- статус Moodle
check("16. у статуса два текста: .lg (полный) и .sh (короткий)", 'class="lg"' in html and 'class="sh"' in html)
check("17. текст статуса пишет одна функция netText — прямых записей в #net .txt нет", "function netText(full, short)" in html and not re.search(r'\$\("#net \.txt"\)\.textContent\s*=', html))
for w in ["Онлайн", "Медленно", "Недоступен", "Пауза", "Проверка…"]:
    check(f"18. короткая подпись «{w}» есть", f'"{w}"' in html)
check("19. на телефоне виден короткий текст, на ПК — полный", "#net .sh{display:none}" in html and "#net .lg{display:none}#net .sh{display:inline}" in html)
check("20. нажатие на статус показывает полный текст", '$("#net").addEventListener("click"' in html)
check("21. шапка на телефоне в одну строку (nowrap), на узких экранах прячется значок", "flex-wrap:nowrap;padding:8px 10px" in html and "@media (max-width:340px){.brand{display:none}}" in html)

# --- фильтры и карточки
check("22. фильтры не прокручиваются вбок: контейнер и два ряда по две при крупном шрифте", "#filters{container-type:inline-size}" in html and "@container (max-width:26em){#filters .chip{flex:1 1 calc(50% - 3px)}}" in html)
check("23. в карточках на телефоне число колонок считается в em (крупный шрифт — одна колонка)", "minmax(min(100%,11.5em),1fr)" in html)
check("24. название до 4 строк, категория до 2, слова переносятся", ".ctitle{-webkit-line-clamp:4;overflow-wrap:anywhere" in html and ".cat{white-space:normal;display:-webkit-box;-webkit-line-clamp:2" in html)
check("25. подвал карточки переносится (кнопка «Папка» не вылезает)", ".cfoot{flex-wrap:wrap" in html and ".cfoot .mini{margin-left:auto}" in html)
print("\nВСЕ ПРОВЕРКИ ПРОШЛИ" if not FAILS else f"\nПРОВАЛЕНО: {len(FAILS)}")
sys.exit(1 if FAILS else 0)

"""Названия курсов в карточках читаются полностью (1.28.0).

Было: название обрезалось «…» на 4-й строке, категория на 2-й, а при скрытой картинке весь текст сдвигался на 46 px вправо
(место под галочку) — на телефоне для названия оставалось ~110 px, и два курса «Английский язык (Уровень Pre-Intermedi…»
нельзя было отличить друг от друга. Поведение проверено в Chromium (телефон 390/360/320 px, ПК, картинка показана и скрыта,
категория скрыта, одна колонка, обе плотности, список; стенд вне репозитория); здесь — то, что можно проверить текстом
(ui/index.html)."""
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

rules = [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]
def touching(cls):
    return [(sel, body) for sel, body in rules if re.search(r"(?<![\w-])\." + cls + r"(?![\w-])", sel)]

# 1. никаких ограничений числа строк и «…» у названия и категории — ни в одном правиле, ни на какой ширине
for cls in ("ctitle", "cat"):
    bad = [sel for sel, body in touching(cls) if re.search(r"-webkit-line-clamp\s*:\s*\d|text-overflow\s*:\s*ellipsis|max-height", body)]
    check(f"1. у .{cls} нет ограничения строк, «…» и max-height", not bad, str(bad))
    clip = [sel for sel, body in touching(cls) if re.search(r"overflow\s*:\s*hidden|display\s*:\s*-webkit-box|white-space\s*:\s*nowrap", body)]
    check(f"2. .{cls} не обрезается (нет overflow:hidden, -webkit-box, nowrap)", not clip, str(clip))
check("3. слово не вылезает за край карточки: overflow-wrap:anywhere у названия и категории", ".ctitle{overflow-wrap:anywhere;hyphens:auto}" in css and ".cat{overflow-wrap:anywhere}" in css)
check("4. в карточках курсов нет скрытой обрезки через line-clamp вообще", "line-clamp" not in " ".join(body for sel, body in rules if "#grid" in sel or ".card" in sel or ".ctitle" in sel or ".cat" in sel))

# 5. без картинки: название на всю ширину, галочка — рядом с категорией, а не сдвиг всего текста
OFF = r"body\[data-v-courses-cover=off\]\[data-v-courses-style=cards\]"
off_rules = [(sel, body) for sel, body in rules if re.search(OFF, sel)]
check("5. скрытая картинка больше не сдвигает весь текст вправо (нет padding-left:46px)", not any(re.search(r"padding-left\s*:\s*46px", body) for _, body in rules))
cat_off = [body for sel, body in off_rules if sel.endswith("#grid .cat")]
check("6. без картинки категория стоит справа от галочки (отступ 32px, высота строки 24px)", cat_off and "margin-left:32px" in cat_off[0] and "min-height:24px" in cat_off[0], str(cat_off))
chk_off = [body for sel, body in off_rules if sel.endswith("#grid .check")]
check("7. без картинки галочка выровнена по строке категории при любой плотности (top считается от --vd-courses)", chk_off and "--vd-courses" in chk_off[0], str(chk_off))
nocat = [body for sel, body in off_rules if 'data-v-courses-cat="0"' in sel and sel.endswith("#grid .cbody")]
check("8. категория скрыта и картинки нет — название начинается под галочкой (padding-top +30px), а не под ней", nocat and "padding-top" in nocat[0] and "30px" in nocat[0], str(nocat))
tg_body = [body for sel, body in off_rules if ".card.tagged .cbody" in sel]
tg_tag = [body for sel, body in off_rules if sel.endswith(".card.tagged .tag")]
tg_cat = [body for sel, body in off_rules if sel.endswith(".card.tagged .cat")]
check("9. карточка с плашкой («прошлый», «скрыт»): верхний ряд — галочка и плашка, категория и название под ним на всю ширину",
      tg_body and "30px" in tg_body[0] and tg_tag and tg_cat and "margin-left:0" in tg_cat[0], str((tg_body, tg_tag, tg_cat)))
check("10. карточка с плашкой получает класс tagged", '${tag ? " tagged" : ""}' in script)
check("11. полное название по-прежнему в подсказке карточки (title)", 'class="ctitle" title="${esc(c.fullname)}"' in script)
hide = [body for sel, body in rules if sel.strip() == 'body[data-v-courses-cat="0"] #grid .cat']
check("13. «Не показывать категорию» работает: правило скрытия есть", hide and "display:none" in hide[0], str(hide))
other_display = [sel for sel, body in touching("cat") if re.search(r"(?<![\w-])display\s*:\s*(?!none)", body)]
check("14. ни одно правило .cat не задаёт display (иначе оно перебьёт скрытие по специфичности — так было при первой правке)", not other_display, str(other_display))
check("12. у списка нет своего ограничения строк у названия", not any("style=list" in sel and ".ctitle" in sel for sel, _ in rules))

print("\nИТОГО:", "всё хорошо" if not FAILS else f"провалено {len(FAILS)}")
sys.exit(1 if FAILS else 0)

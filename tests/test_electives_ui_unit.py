"""«Элективы» на телефоне и ПК (1.32.0): варианты — компактные строки с кнопкой «Выбрать» вместо
сплошного выпадающего списка на 200 px (теперь плитки в две колонки), «Обновить» в шапке вкладки, закладки разделов, поиск и фильтр у больших
блоков, занятия по дням с фильтром, прошлые блоки — одной строкой, панель сохранения не уезжает под нижние вкладки.
Поведение проверено в Chromium (стенд вне репозитория, вымышленный `iot.json`); здесь — то, что можно проверить текстом
(ui/index.html).

Что ловит тест: возврат старой вёрстки (`.eopt` с `<select>` на каждый вариант, `.eev`), классы без префикса `el-`
(ловушка из «Успеваемости»: общий класс совпал с чужим и сломал вид), панель сохранения без отступа от `--bnav-h`
(пряталась под нижними вкладками), пункты «Вида», которые ни на что не влияют, и несбалансированные скобки CSS."""
import re, sys
from pathlib import Path
import _root
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
css = re.sub(r"/\*.*?\*/", "", html[html.index("<style>") + 7:html.index("</style>")], flags=re.S)
script = html[html.index("<script>"):]
ev_html = html[html.index('id="electivesView"'):html.index('id="gradesView"')]
js_a = script.index("const elPl = "); js_b = script.index("function elCall(")
ejs = script[js_a:js_b]
rend = script[script.index("function renderElectives(){"):js_b]
ecss = "\n".join(l for l in css.splitlines() if "#electivesView" in l or "el-" in l or "#elb" in l or "elSec" in l)

# --- старая вёрстка не вернулась
check("1. старых строк `.eopt` / `.eev` нет ни в разметке скрипта, ни в CSS", not re.search(r"(?<![\w-])(eopt|eev)(?![\w-])", ejs + rend + css))
check("2. у каждого варианта не один и тот же широкий select: при одной роли — нажимаемая плитка (`data-elpick`), список ролей только при нескольких",
      "data-elpick" in ejs and "roles.length === 1" in ejs and ejs.count("<select") == 1)
check("3. варианты — плитки в сетке: две колонки на телефоне (`minmax(min(100%,8.5em),1fr)` — em, чтобы при крупном шрифте была одна), шире на ПК",
      re.search(r"\.el-list\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(min\(100%,8\.5em\),1fr\)\)", css) is not None
      and re.search(r"@media \(min-width:701px\)\{#electivesView \.el-list\{grid-template-columns:repeat\(auto-fill,minmax\(min\(100%,12\.5em\)", css) is not None)
check("4. плитка — колонка (`flex-direction:column`), вся нажимается: кнопка `.el-pick` на всю ширину, галочка `.el-ck` в углу; название не уходит под галочку",
      re.search(r"\.el-row\{[^}]*flex-direction:column", css) is not None and re.search(r"\.el-pick\{[^}]*width:100%", css) is not None
      and ".el-ck" in css and re.search(r"\.el-pick \.el-n\{padding-right:\d+px\}", css) is not None and 'class="el-ck"' in ejs)
check("4а. шкала мест — столбиком под подписью и с переносом (в узкой плитке «свободно 79 из 132» не должно вылезать за край)",
      re.search(r"\.el-pl\{[^}]*flex-direction:column", css) is not None and "white-space:nowrap" not in re.search(r"\.el-pl\{[^}]*\}", css).group(0))

# --- шапка и панель
check("5. «Обновить» (#elRefresh) — одна, в шапке вкладки `.el-top`, а не внизу боковой колонки",
      html.count('id="elRefresh"') == 1 and re.search(r'class="el-tb"[^>]*>\s*<button[^>]*id="elRefresh"', ev_html) is not None)
check("6. вход и слежение — сворачиваемая панель `<details id=\"elPanel\">`, на телефоне свёрнута, при «нужен пароль» раскрывается",
      re.search(r'<details[^>]*id="elPanel"', ev_html) is not None and "pn.open = !NARROW.matches" in rend and "need_password" in rend)
check("7. закладки разделов `.el-nav` (data-elgo) прилипают под шапкой и есть только при двух и более разделах",
      "data-elgo" in rend and "nav.length > 1" in rend and re.search(r"\.el-nav\{[^}]*position:sticky;top:var\(--top-h", css) is not None)
check("8. у разделов есть отступ прокрутки под шапку и закладки (иначе заголовок прячется под ними)",
      re.search(r'\[id\^="elSec"\]\{scroll-margin-top:calc\(var\(--top-h', css) is not None and 'id="elSecCan"' in rend and 'id="elSecEv"' in ejs and 'id="elSecMine"' in rend)

# --- панель сохранения
check("9. панель сохранения `.el-bar` прилипает снизу и на телефоне стоит над нижними вкладками (--bnav-h + safe-area)",
      re.search(r"\.el-bar\{[^}]*position:sticky;bottom:\d", css) is not None
      and re.search(r"@media \(max-width:700px\)\{[^@]*\.el-bar\{bottom:calc\(var\(--bnav-h,58px\) \+ env\(safe-area-inset-bottom", css) is not None)
check("10. кнопка «Проверить и сохранить…» отключена, пока не выбраны все роли; «Сохранено» — когда совпадает с сайтом",
      'data-ereview="${b.id}" disabled' in ejs and "elSame(b, st, roles)" in ejs and "Сохранено" in ejs)
check("11. выбор и запись по-прежнему только по нажатию и в два шага: сводка `data-esave`, «Точно записаться?/отписаться?»",
      "data-esave" in ejs and "Точно записаться?" in ejs and "Точно отписаться?" in ejs)

# --- поиск, фильтры, занятия
check("12. поиск и фильтр у блока — только от шести вариантов; поиск перерисовывает один список, а не всю вкладку (фокус не теряется)",
      "opts.length < 6" in ejs and re.search(r'addEventListener\("input", e => \{.*?data-elq.*?elListHtml', script, flags=re.S) is not None)
check("13. занятия сгруппированы по дням (`.el-day`), «Сегодня»/«Завтра», первые 12, остальное — «Показать все»",
      "el-day" in ejs and "Сегодня" in ejs and "Завтра" in ejs and "LIM = 12" in ejs and "data-evall" in ejs)
check("14. фильтр занятий «Можно записаться / Вы записаны» и общий счётчик считают одно и то же (у отменённого записи нет)",
      "const elReg = e => !!(e.reg_id && e.reg_status === \"registered\" && !e.cancelled)" in ejs and "all.filter(elReg)" in ejs)
check("15. прошлые и закрытые блоки — `details.el-mini` с итогом в строке, раскрытое запоминается (`ELV.open`) и не схлопывается при перерисовке",
      "function elMini(" in ejs and "ELV.open" in ejs and 'addEventListener("toggle"' in script)

# --- что сохранено из прежней логики
check("16. логика выбора прежняя: роли `elRoles`, `elNeed`, перенос роли (`elSetRole`), запросы /api/electives/choose|register|retract",
      all(x in script for x in ("const elRoles = ", "function elNeed(", "function elSetRole(", "/api/electives/choose", "/api/electives/register", "/api/electives/retract")))
check("17. выбор вариантов не лезет в чужие данные: роль снимается у прежнего обладателя и ставится новому",
      re.search(r"function elSetRole\(st, oid, role\)\{[^}]*delete st\.pick\[k\][^}]*st\.pick\[role\] = \+oid", script, flags=re.S) is not None)

# --- «Вид»
m = re.search(r'electives: \{name: "Элективы", opts: \[(.*?)\]\},\n\};', script, flags=re.S)
vd = m.group(1) if m else ""
check("18. в «Виде» элективов есть «Варианты, где мест нет» (full) и «Описание под вариантом» (desc), оба переключатели",
      '{k: "full"' in vd and '{k: "desc"' in vd and vd.count("b: 1") >= 2, vd[:200])
check("19. у обоих пунктов есть правило CSS (иначе пункт ни на что не влияет)",
      'body[data-v-electives-desc="0"] #electivesView .el-d{display:none}' in css and 'body[data-v-electives-full="0"] #electivesView .el-row.full:not(.on)' in css)

# --- ловушка с общими именами и баланс скобок
cls = set(re.findall(r'class="([^"${]+)"', script[js_a:script.index("function renderElectives(){")]))
names = {c for x in cls for c in x.split()} - {"btn", "primary", "ghost", "danger", "txt", "adv", "mmeta", "hint", "esec", "esum", "eact", "est", "eblock", "can", "eh", "ean", "mychoice", "on", "full", "mine", "cancel", "na", "today", "ok", "warn", "bad", "low", "none", "t", "b", "a", "m"}
check("20. новые классы вкладки — все с префиксом `el-` (общее имя совпало бы с чужим правилом, как `.gr` в «Успеваемости»)",
      all(c.startswith("el-") for c in names), sorted(c for c in names if not c.startswith("el-")))
check("21. скобки CSS сбалансированы (новый блок стоит после закрытия большого @media телефона)", css.count("{") == css.count("}"), (css.count("{"), css.count("}")))
check("22. у блоков списка нет обрезки названий (`line-clamp`, `ellipsis`) — названия вариантов переносятся целиком",
      not re.search(r"line-clamp|text-overflow:ellipsis", ecss))

if FAILS:
    print("\nПровалено:", len(FAILS)); sys.exit(1)
print("\nВсё в порядке")

"""Расписание на телефоне (1.34.0): «Колонки» — как недельная сетка на сайте schedule.sevsu.ru, чтобы вся неделя была видна на экране.

Просьба пользователя (скриншот сайта: карточки дней в две колонки): «чтобы можно было такой же вид настраивать — колонок».
Здесь — проверки текста; вид (2/3/4 колонки на 390/360/320 px и ПК, неделя и список, плотность) проверен в Chromium: стенд вне репозитория.
Если в системе есть node, ещё и разбор скрипта и допустимых значений."""
import json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
import _root
import schedule as sc

FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
V = sc.VIEWER_HTML
APP = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
css = V.split("<style>", 1)[1].split("</style>", 1)[0]
script = V.split('<script>\n"use strict";', 1)[1].rsplit("</script>", 1)[0]

check("1. rs_cols — общий ключ просмотрщика (не по группам)", re.search(r"SHARED = \[[^\]]*\"rs_cols\"", script) is not None)
check("2. значения: авто и 1–4, остальное сводится к «авто»", 'const COLS = ["auto", "1", "2", "3", "4"]' in script and 'COLS.includes(String(v)) ? String(v) : "auto"' in script)
check("3. применяется на <html> как data-cols (в applyLook, значит и по сообщению из окна программы)", re.search(r"function applyLook\(\)\{\s*root\.dataset\.cols = goodCols\(LS\.get\(\"rs_cols\", \"auto\"\)\)", script) is not None)
for n in (1, 2, 3, 4):
    check(f"4. для {n} колонок есть правило сетки", f'html[data-cols="{n}"] .days{{grid-template-columns:' in css)
check("5. «авто» не трогает прежнюю сетку (правил для data-cols=auto нет)", 'data-cols="auto"' not in css)
check("6. компактный вид только у 2–4 колонок и только внутри .days (день и месяц не меняются)",
      ':where(html[data-cols="2"],html[data-cols="3"],html[data-cols="4"]) .days .les{' in css and re.search(r'^[^:\n]*data-cols="[234]"\]?\) \.les\{', css, re.M) is None)
check("7. компактные правила через :where (плотность «Свободно/Плотно» их перебивает — специфичность 0)",
      all(l.startswith(":where(") or l.startswith("html[data-cols") or l.startswith("html:not(.wide):is(") for l in css.splitlines() if "data-cols" in l))
check("7а. вне :where — только переменные (html[data-cols=N]{--…}), сетка .days и поля страницы (margin-inline)",
      all(re.match(r'html\[data-cols="\d"\]( \.days)?\{(--|grid-template-columns:)', l) or l.startswith("html:not(.wide):is(") or l.startswith(":where(")
          for l in css.splitlines() if "data-cols" in l))
check("8. полное (.wl) и короткое (.ws) написание: заголовок дня («Пн, 16 ноя»), тип занятия и преподаватель; показывается одно",
      script.count('class="wl"') >= 3 and script.count('class="ws"') >= 3 and "\n.ws{display:none}" in css
      and ':where(html[data-cols="2"],html[data-cols="3"],html[data-cols="4"]) .days .wl{display:none}' in css
      and ':where(html[data-cols="2"],html[data-cols="3"],html[data-cols="4"]) .days .ws{display:inline}' in css)
check("9. адрес корпуса — отдельный .ad, в узких колонках скрыт (аудитория остаётся)", 'class="ad"' in script and ".days .les .ad{display:none}" in css)
check("10. время в компактной паре — «08:30 – 10:00» в одну строку", ".days .les .t{display:flex" in css and 'content:"– "' in css)
check("11. в 3–4 колонках дата и «N пар» — друг под другом (иначе заголовок ломается посередине)",
      ':where(html[data-cols="3"],html[data-cols="4"]) .days .day h3{flex-direction:column' in css)
check("11а. поток (.st) в 3–4 колонках скрыт, подгруппа и тип остаются; плашки не переносятся по слогам",
      ':where(html[data-cols="3"],html[data-cols="4"]) .days .st{display:none}' in css and ".days :is(.tag,.sg){white-space:nowrap}" in css.replace(":where(html[data-cols=\"2\"],html[data-cols=\"3\"],html[data-cols=\"4\"]) ", ""))
check("11б. узкий экран: поля у страницы и зазор меньше в 3–4 колонках (--cm, --cg), отступ пары — --lp",
      all(f'html[data-cols="{n}"]{{--cf:' in css and "--cm:" in css and "--lp:" in css for n in (2, 3, 4)) and "margin-inline:calc(var(--cm) - 12px)" in css and "html:not(.wide):is(" in css)
check("12. кегль зависит от числа колонок (--cf), а не фиксирован", all(f'html[data-cols="{n}"]{{--cf:' in css for n in (2, 3, 4)))
check("13. кнопки «Авто / 1 / 2 / 3 / 4» — в окошке «Аа» и в листе фильтров (одни и те же, переключаются вместе)",
      'id="csheet"' in V and "colseg" in script and 'class="seg colseg"' in script and "colsUi()" in script)
check("14. выбор пишется в localStorage сразу и подсвечивается во всех группах кнопок", 'LS.set("rs_cols", root.dataset.cols)' in script and 'b.classList.toggle("on", b.dataset.c === root.dataset.cols)' in script)
check("15. подсветка обновляется и при изменении из окна программы (lookUi → zoomUi → colsUi)", re.search(r"const zoomUi = \(\) => \{\s*colsUi\(\);", script) is not None)
check("16. окно программы: пункт «Колонок в неделе и списке» с тем же ключом rs_cols", 'k: "cols", t: "Колонок в неделе и списке", ls: "rs_cols"' in APP)
check("17. скобки в CSS просмотрщика сбалансированы", css.count("{") == css.count("}"), (css.count("{"), css.count("}")))
check("18. узкая шапка: подсказка у кнопки размера называет и колонки", "Размер текста и колонки" in V)

node = shutil.which("node")
if node:
    tmp = Path(tempfile.mkdtemp(prefix="scols_"))
    page = sc.render_viewer([{"date": "2026-11-19", "start": "08:30", "end": "10:00", "subject": "Тест", "kind": "lecture", "room": "А-101", "address": "ул. Тестовая, 1", "src": ["official"]}],
                            {"group": "Т-1", "generated": "", "sources": {}, "short": {}, "sites": {}, "streams": [], "own": True})
    js = page.split('<script>\n"use strict";', 1)[1].rsplit("</script>", 1)[0]
    (tmp / "viewer.js").write_text('"use strict";' + js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(tmp / "viewer.js")], capture_output=True, text=True)
    check("19. скрипт просмотрщика разбирается (node --check)", r.returncode == 0, r.stderr[:300])
    fn = re.search(r'const COLS = .*?goodCols = [^\n]*\n', script, re.S).group(0)
    probe = fn + "console.log(JSON.stringify([goodCols('auto'), goodCols('1'), goodCols(2), goodCols('4'), goodCols('5'), goodCols('0'), goodCols(null), goodCols(undefined), goodCols('abc')]));"
    (tmp / "probe.js").write_text(probe, encoding="utf-8")
    r = subprocess.run([node, str(tmp / "probe.js")], capture_output=True, text=True)
    got = json.loads(r.stdout.strip() or "[]")
    check("20. goodCols: авто и 1–4 как есть (и числом), всё прочее — «авто»", got == ["auto", "1", "2", "4", "auto", "auto", "auto", "auto", "auto"], got)
    fn2 = re.search(r"const TYPE_SHORT = .*?const shortName = .*?\}\)\.join\(\", \"\);", script, re.S).group(0)
    probe2 = fn2 + """
console.log(JSON.stringify([
 shortType("Практика (ПЗ)"), shortType("Лабораторная (ЛЗ)"), shortType("Лекция"), shortType("Консультация"), shortType("Зачёт/экзамен"), shortType("Контрольная работа письменная"), shortType("(ПЗ)"),
 shortName("Маслова Мария Александровна"), shortName("Иванов И. И."), shortName("Иванов И.И."), shortName("Орлов"), shortName("Белов Иван Петрович, Громова Анна Сергеевна"),
 shortName("Ким Чен Ын"), shortName("Иванов Иван"), shortName("Иванов (дистанционно)"), shortName("")]));"""
    (tmp / "probe2.js").write_text(probe2, encoding="utf-8")
    r = subprocess.run([node, str(tmp / "probe2.js")], capture_output=True, text=True)
    got = json.loads(r.stdout.strip() or "[]")
    want = ["Практика", "Лаб.", "Лекция", "Консульт.", "Зач./экз.", "Контроль.", "(ПЗ)",
            "Маслова М.А.", "Иванов И. И.", "Иванов И.И.", "Орлов", "Белов И.П., Громова А.С.",
            "Ким Ч.Ы.", "Иванов И.", "Иванов (дистанционно)", ""]
    check("21. shortType/shortName: «Практика (ПЗ)» → «Практика», «Лабораторная (ЛЗ)» → «Лаб.», ФИО → «Фамилия И.О.», готовые инициалы и одно слово не трогаются", got == want, got)
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print("SKIP 19–21 (нет node)")

print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}"); sys.exit(1 if FAILS else 0)

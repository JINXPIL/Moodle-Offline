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
      all(l.startswith(":where(") or l.startswith("html[data-cols") for l in css.splitlines() if "data-cols" in l))
check("8. в заголовке дня два варианта: полный (.wl) и короткий (.ws: «Пн, 16 ноя»), показывается один",
      script.count('class="wl"') >= 2 and script.count('class="ws"') >= 2 and ".day h3 .ws{display:none}" in css and ".days .day h3 .wl{display:none}" in css)
check("9. адрес корпуса — отдельный .ad, в узких колонках скрыт (аудитория остаётся)", 'class="ad"' in script and ".days .les .ad{display:none}" in css)
check("10. время в компактной паре — «08:30 – 10:00» в одну строку", ".days .les .t{display:flex" in css and 'content:"– "' in css)
check("11. в 3–4 колонках дата и «N пар» — друг под другом (иначе заголовок ломается посередине)",
      ':where(html[data-cols="3"],html[data-cols="4"]) .days .day h3{flex-direction:column' in css)
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
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print("SKIP 19–20 (нет node)")

print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}"); sys.exit(1 if FAILS else 0)

"""Расписание на телефоне (1.33.0): верхняя панель — одна неподвижная строка, масштаб — плавный (щипок и «Аа»), больше места под пары.

Что стояло не так (скриншоты пользователя, телефон):
- верхняя строка просмотрщика («вид» и «фильтры») при прокрутке уезжала (`top:-52px`) и возвращалась в самом верху — панель «прыгала»;
- масштаб прятался в листе фильтров, был в четыре шага и увеличивал вместе с текстом и саму панель;
- над расписанием лежали три слоя (шапка программы, плашка, строка выбора группы), окно с парами получало ~55 % экрана.

Здесь — проверки текста (поведение — щипок, меню, прокрутка — проверено в Chromium с касаниями: стенд вне репозитория).
Если в системе есть node, ещё и разбор скрипта просмотрщика и функции допустимого масштаба."""
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

check("1. верхняя панель не уезжает: нет top:-52px / top:-46px", "top:-52px" not in V and "top:-46px" not in V)
check("2. панель не прячется и по высоте окна (нет правил, возвращающих top:0 только в низком окне)", "html:not(.wide) .bar{top:0}" not in V)
check("3. на узком экране вторая строка (.r1: вид и заголовок) скрыта, вид выбирается нажатием на дату",
      "html:not(.wide) .r1{display:none}" in css and 'id="vmenu"' in V and re.search(r'<button class="ttl" id="ttl"', V) is not None)
check("4. в одной строке: назад, дата, вперёд, сегодня, размер, фильтры",
      all(re.search(r'<div class="r r2">.*?id="%s"' % i, V, re.S) for i in ("prev", "ttl", "next", "tw", "zbtn", "fbtn")))
check("5. меню вида — четыре пункта с теми же data-v, что у переключателя на широком экране",
      V.count('data-v="day"') == 2 and V.count('data-v="list"') == 2 and 'id="vmenu"' in V)
check("6. «Сегодня» — значок-календарик с числом (слово остаётся на широком экране)", 'class="tw-ic"' in V and "html:not(.wide) .tw-tx{display:none}" in css and "html.wide .tw-ic" in css)
check("7. заголовок на широком экране — не кнопка меню (disabled), на узком — кнопка со стрелкой",
      '$("ttl").disabled = WIDE.matches' in script and ".ttl:not(:disabled) b::after" in css)

# масштаб
check("8. масштаб — zoom у содержимого, а не у всей страницы (панель не растёт); запасной путь — размер шрифта",
      "main,footer{zoom:var(--sc)}" in css and "@supports not (zoom:1)" in css and "html{font-size:14px" in css and "font-size:calc(14px * var(--sc))" in css.split("@supports not (zoom:1)", 1)[1].split("}", 1)[0])
check("9. допустимый масштаб — любое число 0,8–1,8; четырёх готовых значений в коде больше нет",
      "const SC_MIN = 0.8, SC_MAX = 1.8" in script and "[0.9, 1, 1.15, 1.3]" not in script and script.count("goodScale(LS.get(") == 2)
m = re.search(r"const setScale = (.*?)\n", script)
check("10. смена масштаба не перестраивает список (меняется только --sc)", bool(m) and "render(" not in m.group(1) and '--sc' in m.group(1))
check("11. запись в хранилище — при отпускании ползунка и в конце щипка, не на каждый шаг (иначе приложение получает поток событий)",
      "pzEnd" in script and 'addEventListener("change"' in script and "LS.set(\"rs_scale\", String(scale))" in script)
check("12. масштаб больше не считается «фильтром» (точка на кнопке фильтров)", "scale !== 1" not in script.split("const filtersActive", 1)[1].split("\n", 1)[0])
check("13. щипок: браузерный масштаб страницы отключён (touch-action), обработчики пассивные — обычная прокрутка не тормозит; якорь не борется с родной прокруткой",
      "touch-action:pan-x pan-y" in css and 'touchstart", e => {' in script and "{passive: false}" not in V and "preventDefault" not in script.split("let pz = null;", 1)[1].split("$(\"appset\")", 1)[0]
      and "overflow-anchor:none" in css and "user-scalable" not in V)
check("14. при щипке то, что под пальцами, остаётся на месте (якорь — элемент списка, scrollBy)", "elementFromPoint" in script and "scrollBy(0," in script)
check("15. после щипка оставшийся палец не листает день (свайп сбрасывается при втором касании)", "tOk = e.touches.length === 1" in script)
check("16. размер текста в листе — ползунок и «А−/А+», а не четыре кнопки",
      'id="zsheet"' in V and "data-z=" not in V and 'id="size"' not in V and 'type="range" class="zr"' in script)
check("17. ползунок и всплывающее окошко закрываются нажатием мимо и Esc; открытие одного закрывает другое",
      'pointerdown' in script and 'key === "Escape") popClose()' in script and "popClose(); fToggle(false)" in script)

# окно «Вид» и просмотрщик читают одно и то же значение
check("18. приложение: пункт «Размер текста» принимает любое число 0,8–1,8 (fr), а не только из списка",
      'fr: [0.8, 1.8]' in APP and "if (o.fr && r !== null)" in APP)
check("19. приложение: «Вид» показывает своё число процентами, а не «Обычный»", 'Math.round(parseFloat(cur) * 100) + " %"' in APP)

# оболочка
check("20. приложение: при одном расписании строка выбора скрыта (.nohead), при нескольких — на месте",
      "#scheduleView.nohead .schhead{display:none}" in APP and '.toggle("nohead", ps.length <= 1)' in APP)
check("21. приложение: окно с парами растягивается (flex), а не «экран минус 52px»",
      "#scheduleView .schedframe{width:100%;border-radius:0;flex:1 1 0;height:auto" in APP and "- 52px" not in APP.split("#scheduleView .schedframe{width:100%;border-radius:0;flex:1 1 0", 1)[0][-900:]
      and "body.sched-lock #scheduleView .content{box-sizing:border-box;" in APP)
check("22. приложение: рамка не даёт браузеру увеличить страницу щипком", re.search(r"\.schedframe\{[^}]*touch-action:pan-x pan-y", APP) is not None)
check("23. приложение: настройки открываются из просмотрщика сообщением — только от своей рамки и своего адреса",
      'e.data.moSched === "settings"' in APP and "e.source === f.contentWindow" in APP and "e.origin === location.origin" in APP.split('e.data.moSched === "settings"', 1)[0][-260:])
check("24. просмотрщик шлёт сообщение только родителю на свой адрес (не «*»)", 'parent.postMessage({moSched: "settings"}, location.origin)' in script and 'postMessage({moSched: "settings"}, "*")' not in V)
check("25. плашки в расписании на телефоне тоньше", "body.sched-lock .banner{margin:4px 8px 0" in APP)

# целостность страницы
check("26. скобки в CSS просмотрщика сбалансированы", css.count("{") == css.count("}"), (css.count("{"), css.count("}")))
ids = re.findall(r'\bid="([^"]+)"', V.split("<script", 1)[0])
check("27. id в разметке просмотрщика не повторяются", len(ids) == len(set(ids)), [i for i in set(ids) if ids.count(i) > 1])
check("28. в просмотрщике нет сторонних адресов", not re.search(r'(?:src|href)="https?://', V.split('<script id="data"', 1)[0]))
check("29. отпечаток просмотрщика зависит от этого текста (сохранённая страница пересоберётся сама)", sc.viewer_id() and len(sc.viewer_id()) >= 8)

node = shutil.which("node")
if node:
    tmp = Path(tempfile.mkdtemp(prefix="sbar_"))
    page = sc.render_viewer([{"date": "2026-11-19", "start": "08:30", "end": "10:00", "subject": "Тест", "kind": "lecture", "src": ["official"]}],
                            {"group": "Т-1", "generated": "", "sources": {}, "short": {}, "sites": {}, "streams": [], "own": True})
    js = page.split('<script>\n"use strict";', 1)[1].rsplit("</script>", 1)[0]
    (tmp / "viewer.js").write_text('"use strict";' + js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(tmp / "viewer.js")], capture_output=True, text=True)
    check("30. скрипт просмотрщика разбирается (node --check)", r.returncode == 0, r.stderr[:300])
    fn = re.search(r"const SC_MIN = .*?\nconst goodScale = .*?\n", script, re.S).group(0)
    probe = fn + "console.log(JSON.stringify([goodScale('0.9'), goodScale('1'), goodScale('1.15'), goodScale('1.3'), goodScale('1.234'), goodScale('abc'), goodScale(null), goodScale('9'), goodScale('0.1'), goodScale('-3')]));"
    (tmp / "probe.js").write_text(probe, encoding="utf-8")
    r = subprocess.run([node, str(tmp / "probe.js")], capture_output=True, text=True)
    got = json.loads(r.stdout.strip() or "[]")
    check("31. goodScale: прежние значения те же, любое число в пределах, мусор → 1, лишнее — к краю", got == [0.9, 1, 1.15, 1.3, 1.23, 1, 1, 1.8, 0.8, 0.8], got)
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print("SKIP 30–31 (нет node)")

print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}"); sys.exit(1 if FAILS else 0)

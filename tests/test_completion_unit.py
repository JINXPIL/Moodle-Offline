"""Отметки «выполнено» и «ограничение доступа» со страницы курса — на кусках настоящей разметки do.sevsu.ru (обезличено)
и, если указана, на целой настоящей странице курса. Саму страницу в репозиторий не кладём (имя, sesskey): путь к ней —
в переменной MO_REAL_COURSE_PAGE, имя пользователя на ней (чтобы проверить, что оно не попало в данные) — в MO_REAL_USER_NAME."""
import sys, os, re, json
import _root
import moodle_offline as mo
from bs4 import BeautifulSoup
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)
P = "https://do.sevsu.ru/course/view.php?id=1"
def act(html, mod="assign"):
    soup = BeautifulSoup(f'<ul><li class="activity {mod} modtype_{mod}" id="module-5"><div><div class="activityinstance">'
                         f'<a class="aalink" href="https://do.sevsu.ru/mod/{mod}/view.php?id=5"><span class="instancename">Работа №1<span class="accesshide "> Задание</span></span></a></div>{html}</div></li></ul>', mo.PARSER)
    return soup.select_one("li.activity")
I = "https://do.sevsu.ru/theme/image.php/sevsu/core/1793172904/i/"
cases = {
  "auto-y": ('<div class="actions"><span class="autocompletion"><img alt="Выполнено: Работа №1" class="icon" src="%scompletion-auto-y"/></span></div>' % I, "done"),
  "auto-pass": ('<div class="actions"><span class="autocompletion"><img alt="Выполнено: Работа №1 (оценка выше проходного балла)" class="icon" src="%scompletion-auto-pass"/></span></div>' % I, "done"),
  "auto-n": ('<div class="actions"><span class="autocompletion"><img alt="Не выполнено: Работа №1" class="icon" src="%scompletion-auto-n"/></span></div>' % I, "todo"),
  "auto-fail": ('<div class="actions"><span class="autocompletion"><img alt="Не выполнено: Работа №1 (оценка ниже проходного)" class="icon" src="%scompletion-auto-fail"/></span></div>' % I, "fail"),
  "manual-n": ('<div class="actions"><form class="togglecompletion" method="post"><div><input name="sesskey" type="hidden" value="x"/><button class="btn btn-link"><img alt="Не выполнено: Работа №1. Выберите, чтобы отметить элемент как выполненный." class="icon" src="%scompletion-manual-n"/></button></div></form></div>' % I, "todo"),
  "manual-y": ('<div class="actions"><form class="togglecompletion" method="post"><div><button class="btn btn-link"><img alt="Выполнено: Работа №1. Выберите, чтобы отметить элемент как невыполненный." class="icon" src="%scompletion-manual-y"/></button></div></form></div>' % I, "done"),
  "по подписи": ('<div class="actions"><img alt="Выполнено: Работа №1" src="/x/y.png"/></div>', "done"),
  "нет отметки": ('', ""),
  "чужие картинки": ('<div class="actions"><img alt="Файл" src="%sicon"/></div>' % I, ""),
  "enabled (у преподавателя)": ('<div class="actions"><img alt="Условие выполнения" src="%scompletion-auto-enabled"/></div>' % I, ""),
}
for name, (h, want) in cases.items():
    got = mo.completion_state(act(h))
    check(f"completion_state: {name} → {want or '—'}", got == want, got)
# ограничение
av = '<div class="availabilityinfo isrestricted"><span class="badge badge-info">Ограничено</span> Недоступно, пока не выполнены условия: Вы принадлежите к группе лок. ИБ/б-25-1-о</div>'
r = mo.restriction_text(act(av))
check("ограничение: без плашки «Ограничено»", r == "Недоступно, пока не выполнены условия: Вы принадлежите к группе лок. ИБ/б-25-1-о", r)
check("ограничения нет — пусто", mo.restriction_text(act("")) == "")
# в разборе элемента и раздела
sec = f'<li id="section-3" class="section main"><div class="content"><h3 class="sectionname">Раздел</h3><div class="section_availability">{av}</div><div class="summary"></div><ul class="section">'
sec += '<li class="activity assign modtype_assign" id="module-5"><div class="activityinstance"><a class="aalink" href="https://do.sevsu.ru/mod/assign/view.php?id=5"><span class="instancename">Работа №1</span></a></div>' + cases["auto-y"][0] + '</li>'
sec += '<li class="activity resource modtype_resource" id="module-6"><div class="activityinstance"><span class="instancename">Закрытое</span></div>' + av + '</li></ul></div></li>'
soup = BeautifulSoup("<html><body><div role='main'><ul>" + sec + "</ul></div></body></html>", mo.PARSER)
ss = mo.parse_sections(soup, P, 1)
check("раздел: ограничение прочитано", ss and "Вы принадлежите к группе" in ss[0].restriction, str(ss[0].restriction if ss else ""))
it = {i.cmid: i for i in ss[0].items}
check("элемент: выполнено", it["5"].done == "done" and it["5"].restriction == "")
check("элемент без ссылки: ограничение и «только онлайн»", it["6"].restriction.startswith("Недоступно") and it["6"].online_only and it["6"].url is None, str(it["6"]))
check("pruffme назван по-русски", mo.MOD_TITLES.get("pruffme") == "вебинар (Pruffme)")

# ── целая настоящая страница (если лежит) ──
F = os.environ.get("MO_REAL_COURSE_PAGE", "")
if F and os.path.exists(F):
    soup = BeautifulSoup(open(F, encoding="utf-8", errors="replace").read(), mo.PARSER)
    ss = mo.parse_sections(soup, "https://do.sevsu.ru/course/view.php?id=13566", 13566)
    items = [i for s in ss for i in s.items]
    from collections import Counter
    c = Counter(i.done for i in items)
    check("настоящая страница: 14 разделов, 43 элемента", len(ss) == 14 and len(items) == 43, f"{len(ss)} / {len(items)}")
    check("настоящая страница: отметок 20 (6 выполнено, 14 нет), у 23 отметки нет", c == Counter({"": 23, "todo": 14, "done": 6}), str(c))
    check("настоящая страница: число отметок = числу значков на странице", len(soup.select(".actions img[src*='completion-']")) == 20)
    check("настоящая страница: ограничение у раздела 2 и одного элемента",
          [s.number for s in ss if s.restriction] == [2] and sum(1 for i in items if i.restriction) == 1)
    check("настоящая страница: все тесты и «Посещаемость» отмечены выполненными",
          all(i.done == "done" for i in items if i.modtype in ("quiz", "attendance")))
    mo.classify_course(c2 := mo.Course(id=13566, fullname="x", sections=ss))
    blob = json.dumps([[i.__dict__ for i in s.items] for s in ss], ensure_ascii=False)
    check("в данных элементов нет sesskey и логина", "sesskey" not in blob.lower() and not (os.environ.get("MO_REAL_USER_NAME") and os.environ["MO_REAL_USER_NAME"] in blob))
else:
    print("(целой настоящей страницы нет — MO_REAL_COURSE_PAGE не задана, эти проверки пропущены)")
print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

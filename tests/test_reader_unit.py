import sys, tempfile, shutil, os
import _root
import reader
OK = BAD = 0
def chk(name, cond, extra=""):
    global OK, BAD
    if cond: OK += 1; print("  ok  ", name)
    else: BAD += 1; print("  FAIL", name, extra)
HOST = "do.sevsu.ru"; PAGE = "https://do.sevsu.ru/mod/assign/view.php?id=77"
ASSIGN = """<html><head><title>Лаб. 3: Задание | Moodle</title><script>alert(1)</script></head><body onload="x()">
<header id="page-header"><h1>Лабораторная 3</h1></header><nav class="breadcrumb">Главная / Курс</nav>
<div id="region-main"><div role="main"><span id="maincontent"></span>
<h1>Лабораторная 3</h1>
<div class="activity-information"><div data-region="activity-dates"><div><strong>Открыто:</strong> понедельник, 3 ноября 2026, 08:00</div><div><strong>Срок сдачи:</strong> пятница, 14 ноября 2026, 23:59</div></div></div>
<div class="completion-info"><button class="btn">Отметить как выполненное</button></div>
<div class="box py-3 generalbox boxaligncenter"><div id="intro"><div class="no-overflow"><p style="color:red" onclick="evil()">Реализуйте <b>очередь</b> на списке. <a href="https://do.sevsu.ru/mod/page/view.php?id=5&sesskey=ABC123">Теория</a></p>
<p><img src="https://do.sevsu.ru/pluginfile.php/10/mod_assign/intro/scheme.png" alt="схема" onerror="evil()"><img src="https://evil.example/track.gif" alt="трекер"></p>
<ul><li><a href="/pluginfile.php/10/mod_assign/introattachment/0/task.pdf?forcedownload=1">task.pdf</a></li><li><a href="https://github.com/x/y">репозиторий</a></li><li><a href="javascript:evil()">js</a></li><li><a href="/login/logout.php?sesskey=ZZZ">Выйти</a></li><li><a href="#mid">якорь</a></li><li><a href="mailto:t@sevsu.ru">написать</a></li></ul></div></div></div>
<table class="generaltable"><tr><th>Состояние ответа</th><td>Ответы не представлены</td></tr><tr><td colspan="2">Оценка не выставлена</td></tr></table>
<form action="/mod/assign/view.php" method="post"><input type="hidden" name="sesskey" value="SECRET"><button type="submit">Добавить ответ на задание</button><input type="text" name="x"></form>
<iframe src="https://www.youtube.com/embed/abc" title="Видео"></iframe>
<video controls><source src="/pluginfile.php/10/mod_assign/intro/v.mp4" type="video/mp4"></video>
<object data="x.swf"></object><svg onload="evil()"><circle/></svg>
</div></div><footer id="page-footer">Подвал</footer></body></html>"""
r = reader.render(ASSIGN, PAGE, HOST)
h = r["html"]
chk("заголовок взят из шапки", r["title"] == "Лабораторная 3", r["title"])
chk("скриптов, обработчиков и style нет", not any(x in h.lower() for x in ("<script", "onclick", "onerror", "onload", "style=", "alert(", "evil()", "<svg", "<object", "<iframe")), h[:500])
chk("кнопок и форм нет, значения скрытых полей не просочились", "<button" not in h and "<form" not in h and "<input" not in h and "SECRET" not in h and "Добавить ответ" not in h)
chk("форма посчитана", r["forms"] == 1)
chk("дубль h1 убран", "<h1" not in h)
chk("сроки сдачи на месте", "Срок сдачи" in r["text"] and "14 ноября 2026" in r["text"])
chk("таблица статуса на месте", "Ответы не представлены" in h and 'colspan="2"' in h)
chk("«Отметить как выполненное» убрано", "Отметить как выполненное" not in h)
chk("хлебные крошки и подвал убраны", "Главная / Курс" not in h and "Подвал" not in h)
chk("ссылка с sesskey (действие) — просто текст, ключа нет", "Теория" in r["text"] and "ABC123" not in h and "page/view.php?id=5" not in h, h[:900])
chk("обычная ссылка на страницу Moodle → data-go", 'data-rgo="https://do.sevsu.ru/mod/forum/view.php?id=4"' in reader.render('<div role="main"><a href="/mod/forum/view.php?id=4">Форум</a></div>', PAGE, HOST)["html"])
chk("pluginfile → data-file", 'data-rfile="https://do.sevsu.ru/pluginfile.php/10/mod_assign/introattachment/0/task.pdf?forcedownload=1"' in h)
chk("чужой сайт → data-ext", 'data-rext="https://github.com/x/y"' in h)
chk("javascript:, выход, якорь — не ссылки", "javascript" not in h and "logout" not in h and "ZZZ" not in h and "#mid" not in h and ">якорь<" in h)
chk("mailto → data-ext", 'data-rext="mailto:t@sevsu.ru"' in h)
chk("все href заменены на #", all(a.get("href") == "#" for a in reader.BeautifulSoup(h, "html.parser").find_all("a")))
chk("своя картинка → data-src", 'data-src="https://do.sevsu.ru/pluginfile.php/10/mod_assign/intro/scheme.png"' in h)
chk("чужая картинка не грузится", "evil.example" not in h and "[картинка с другого сайта: трекер]" in h)
chk("видео → data-src", 'data-src="https://do.sevsu.ru/pluginfile.php/10/mod_assign/intro/v.mp4"' in h)
chk("iframe → ссылка", "Встроенное содержимое: Видео" in h and 'data-rext="https://www.youtube.com/embed/abc"' in h)
chk("жирный текст сохранён", "<b>очередь</b>" in h)

FORUM = """<html><body><div id="region-main"><div role="main"><h2>Новости</h2><table class="forumheaderlist"><tr><td><a href="https://do.sevsu.ru/mod/forum/discuss.php?d=9">Перенос лекции</a></td><td>Иванова А.</td><td>3</td></tr></table></div></div></body></html>"""
r = reader.render(FORUM, "https://do.sevsu.ru/mod/forum/view.php?id=3", HOST)
chk("форум: обсуждение — data-go", 'data-rgo="https://do.sevsu.ru/mod/forum/discuss.php?d=9"' in r["html"] and "Перенос лекции" in r["text"], r["html"])
chk("без main — берётся body", reader.render("<html><body><p>Привет</p></body></html>", PAGE, HOST)["html"].count("Привет") == 1)
chk("пустое не падает", reader.render("", PAGE, HOST)["html"] == "")
chk("classify", reader.classify("https://do.sevsu.ru/mod/resource/view.php?id=3", HOST) == "file" and reader.classify("https://do.sevsu.ru/course/view.php?id=3", HOST) == "go"
    and reader.classify("https://x.ru/a", HOST) == "ext" and reader.classify("ftp://x", HOST) == "")
chk("ссылки-действия не ссылки", all(reader.classify("https://do.sevsu.ru" + x, HOST) == "" for x in ("/mod/forum/post.php?reply=5", "/mod/assign/view.php?id=7&action=editsubmission", "/mod/quiz/startattempt.php", "/mod/quiz/attempt.php?attempt=3")))
chk("просмотр обсуждения и попыток остаётся ссылкой", reader.classify("https://do.sevsu.ru/mod/quiz/review.php?attempt=3", HOST) == "go" and reader.classify("https://do.sevsu.ru/mod/forum/discuss.php?d=1", HOST) == "go")
chk("norm_url", reader.norm_url("https://do.sevsu.ru/a?id=1&sesskey=Q#top") == "https://do.sevsu.ru/a?id=1")
chk("same_site", reader.same_site("https://do.sevsu.ru/a", HOST) and not reader.same_site("https://do.sevsu.ru.evil.com/a", HOST) and not reader.same_site("ftp://do.sevsu.ru/a", HOST))
d = tempfile.mkdtemp()
reader.save(d, PAGE, {"title": "T", "html": "<p>x</p>"})
rec = reader.load(d, PAGE + "&sesskey=zz")
chk("кэш: запись и чтение (sesskey не влияет на ключ)", rec and rec["title"] == "T" and rec["ts"] > 0)
chk("кэш: нет записи → None", reader.load(d, "https://do.sevsu.ru/other") is None)
for i in range(reader.MAX_CACHE_PAGES + 5): reader.save(d, f"https://do.sevsu.ru/p?id={i}", {"title": str(i), "html": ""})
chk("кэш обрезается до лимита", len(list((reader.cache_dir(d) / "pages").glob("*.json"))) <= reader.MAX_CACHE_PAGES)
shutil.rmtree(d)
print(f"\nИТОГО: {OK} ок, {BAD} ошибок"); sys.exit(1 if BAD else 0)

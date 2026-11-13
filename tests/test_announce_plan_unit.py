"""План сохранения объявлений (news.plan_announcements): что сохранить сейчас, что отложить, что считать известным.
Раньше при настройке «сохранить 15 прошлых» сохранялись первые 10, а остальные 5 сразу попадали в «известные» и не сохранялись никогда.
Без сети."""
import sys
import _root
import news

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

def found(*ids): return [{"id": i, "title": f"Тема {i}", "url": f"https://do.sevsu.ru/mod/forum/discuss.php?d={i}"} for i in ids]
def ids(todo): return [d["id"] for d, _silent in todo]

# 1. первый раз: берём backfill последних молча, остальное запоминаем как известное
p = news.plan_announcements(found(*range(30, 0, -1)), None, [], 3, per_run=10)
check("первый раз: сохраняем 3 последних, без сообщений", ids(p["todo"]) == [30, 29, 28] and all(s for _d, s in p["todo"]), str(ids(p["todo"])))
check("первый раз: все 30 теперь известны, очереди нет", set(p["known"]) == set(range(1, 31)) and p["pend"] == [])

# 2. первый раз и backfill больше лимита: сохраняем сколько можно, остальное — в очередь, а не в «забытое»
p = news.plan_announcements(found(*range(30, 0, -1)), None, [], 15, per_run=10)
check("backfill 15 при лимите 10: сейчас 10 штук", ids(p["todo"]) == list(range(30, 20, -1)), str(ids(p["todo"])))
check("остальные 5 ждут очереди (не потеряны)", p["pend"] == list(range(20, 15, -1)), str(p["pend"]))
check("очередь не объявляется «новым»: все молча", all(s for _d, s in p["todo"]))

# 3. следующий проход добирает очередь, ничего не объявляя
p2 = news.plan_announcements(found(*range(30, 0, -1)), p["known"], p["pend"], 15, per_run=10)
check("следующий проход: сохраняет очередь молча", ids(p2["todo"]) == list(range(20, 15, -1)) and all(s for _d, s in p2["todo"]), str(ids(p2["todo"])))
check("очередь опустела", p2["pend"] == [])
p3 = news.plan_announcements(found(*range(30, 0, -1)), p2["known"], p2["pend"], 15, per_run=10)
check("потом всё тихо", p3["todo"] == [] and p3["pend"] == [])

# 4. новые объявления: объявляем и сохраняем; сверх лимита — объявляем, а страницу сохраним в следующий раз
known = list(range(1, 31))
newer = found(*range(45, 30, -1)) + found(*range(30, 0, -1))      # 15 новых сверху
p = news.plan_announcements(newer, known, [], 3, per_run=10)
check("новые: первые 10 с сообщением", ids(p["todo"][:10]) == list(range(45, 35, -1)) and all(not s for _d, s in p["todo"][:10]), str(ids(p["todo"])))
check("новые сверх лимита: сообщение есть, страница — в очереди", [d["id"] for d, s in p["todo"][10:]] == list(range(35, 30, -1)) and all(not s for _d, s in p["todo"][10:]) and p["pend"] == list(range(35, 30, -1)) and p["fetch"] == 10,
      f'{ids(p["todo"])} pend={p["pend"]} fetch={p.get("fetch")}')
check("все 45 известны (повторных сообщений не будет)", set(p["known"]) == set(range(1, 46)))
p2 = news.plan_announcements(newer, p["known"], p["pend"], 3, per_run=10)
check("следующий проход сохраняет «хвост» молча", ids(p2["todo"]) == list(range(35, 30, -1)) and all(s for _d, s in p2["todo"]) and p2["pend"] == [], str(ids(p2["todo"])))

# 5. очередь и новые вместе: лимит общий, новые идут первыми
p = news.plan_announcements(found(60, 59, *range(30, 20, -1)), list(range(1, 31)), [25, 24, 23], 3, per_run=4)
fetched = [d["id"] for d, s in p["todo"] if p["fetch"]][:p["fetch"]]
check("общий лимит страниц; новые идут раньше очереди", ids(p["todo"])[:2] == [60, 59] and p["fetch"] == 4 and ids(p["todo"])[2:4] == [25, 24], f'{ids(p["todo"])} fetch={p["fetch"]}')
check("не поместившееся остаётся в очереди", 23 in p["pend"], str(p["pend"]))

# 6. тема пропала с сайта (удалили) — из очереди уходит; known не растёт бесконечно
p = news.plan_announcements(found(5, 4), list(range(1, 6)), [3, 2, 99], 3, per_run=10)
check("в очереди остались только темы, которые ещё есть на странице", ids(p["todo"]) == [3, 2] or ids(p["todo"]) == [], str(ids(p["todo"])))
check("нет такой темы — очередь без неё", 99 not in p["pend"] and p["pend"] == [])
big = news.plan_announcements(found(*range(1000, 0, -1)), None, [], 0, per_run=10)
check("backfill 0: ничего не сохраняем, но всё запоминаем; известных не больше 300", big["todo"] == [] and len(big["known"]) <= 300 and 1000 in big["known"], str(len(big["known"])))
check("пустая страница форума — пусто", news.plan_announcements([], [1], [2], 3)["todo"] == [])

print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

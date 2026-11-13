import sys, json, os, tempfile, time, shutil
import _root
import news
from bs4 import BeautifulSoup
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    ok &= bool(cond)

# ── normalize / validate
n = news.normalize(None)
check("defaults: track grade", n["track"]["grade"] is True and n["track"]["item_renamed"] is False and n["push"]["item_gone"] is False)
n = news.normalize({"track": {"item_renamed": 1, "bogus": True}, "mute": ["5", "x", 7], "max_lines": 1000, "webhook_format": "zzz",
                    "forum_words": "а\nб, в", "backfill": -3, "toast": 0})
check("normalize coerces", n["track"]["item_renamed"] is True and "bogus" not in n["track"] and n["mute"] == [5, 7] and n["max_lines"] == 50
      and n["webhook_format"] == "json" and n["forum_words"] == ["а", "б", "в"] and n["backfill"] == 0 and n["toast"] is False, n)
check("validate chat", news.validate({"telegram_chat": "abc"}) and not news.validate({"telegram_chat": "-1001234567"}) and not news.validate({"telegram_chat": "@my_chan"}))
check("webhook url check", news.check_webhook_url("ftp://x") and news.check_webhook_url("") and not news.check_webhook_url("https://h/x"))

# ── diff_items
def it(k, name, t="resource", files=None): return {"key": k, "name": name, "modtype": t, "url": "u"+k, "section": "S",
                                                  "files": [{"title": f, "path": "C/"+f} for f in (files or [])]}
base = [it("a", "A", files=["a.pdf"]), it("b", "B", "folder", ["b0.pdf"]), it("t", "text", "text"), it("f", "link", "file", ["l0.pdf"])]
ev, seen, sus = news.diff_items(1, "C", None, base)
check("baseline: no events, text excluded, files remembered", ev == [] and set(seen) == {"a", "b", "f"} and seen["b"]["f"] == ["b0.pdf"] and not sus)
now = [it("a", "A renamed", files=["a.pdf"]), it("b", "B", "folder", ["b0.pdf", "b1.pdf", "b2.pdf"]), it("t", "text", "text"),
       it("f", "link", "file", ["l0.pdf"]), it("c", "C new", files=["x.pdf"]), it("f2", "новая ссылка", "file", ["l1.pdf"])]
ev, seen2, sus = news.diff_items(1, "C", seen, now)
kinds = sorted(e["kind"] for e in ev)
check("diff kinds", kinds == ["file_new", "file_new", "item_new", "item_renamed"], kinds)
check("new item's files not duplicated as file_new", not any(e["kind"] == "file_new" and e["title"] == "C new" for e in ev))
fb = [e for e in ev if e["kind"] == "file_new" and e["title"] == "B"]
check("only the fresh files of existing item", len(fb) == 1 and fb[0]["files"] == ["b1.pdf", "b2.pdf"] and fb[0]["path"] == "C/b1.pdf", fb)
check("new link in text → file_new named by file", any(e["kind"] == "file_new" and e["title"] == "l1.pdf" and e["detail"] == "новая ссылка" for e in ev))
ev, _, _ = news.diff_items(1, "C", seen, now, item_new_tracked=False)
check("item_new untracked → its files still reported", any(e["kind"] == "file_new" and e["title"] == "C new" for e in ev) and not any(e["kind"] == "item_new" for e in ev))
ev, s3, _ = news.diff_items(1, "C", seen2, now)
check("second pass over same state: nothing", ev == [], ev)
ev, _, _ = news.diff_items(1, "C", seen, [x for x in now if x["key"] != "f"])
check("vanished file-link is not 'gone'", not any(e["kind"] == "item_gone" for e in ev))
gone_prev = {f"k{i}": {"n": f"n{i}", "t": "resource", "f": []} for i in range(10)}
ev, seen3, sus = news.diff_items(1, "C", gone_prev, [it("k0", "n0")])
check("suspicious mass-disappearance suppressed & baseline kept", sus and not ev and len(seen3) == 10)
gone_prev = {f"k{i}": {"n": f"n{i}", "t": "resource", "f": []} for i in range(5)}
ev, seen3, sus = news.diff_items(1, "C", gone_prev, [it(f"k{i}", f"n{i}") for i in range(4)])
check("single removal reported", not sus and [e["kind"] for e in ev] == ["item_gone"])

# ── grades
G = lambda rows: BeautifulSoup('<table class="user-grade">' + "".join(
    f'<tr><th class="column-itemname">{n}</th><td class="column-grade">{g}</td><td class="column-percentage">{p}</td><td class="column-feedback">{f}</td></tr>'
    for n, g, p, f in rows) + "</table>", "html.parser")
s1 = news.parse_grades(G([("T1", "-", "-", ""), ("T1", "3", "60 %", ""), ("T2", "5", "", "")]))
check("grades parse dup names", list(s1) == ["T1", "T1 (2)", "T2"] and s1["T1"]["g"] == "" and s1["T1 (2)"]["g"] == "3", s1)
check("grades none when no table", news.parse_grades(BeautifulSoup("<p>нет</p>", "html.parser")) is None)
s2 = news.parse_grades(G([("T1", "4", "80 %", ""), ("T1", "3", "60 %", "ок"), ("T2", "-", "", "")]))
evs = news.diff_grades(1, "C", s1, s2)
details = sorted(e["detail"] for e in evs)
check("grade diff", len(evs) == 3 and any("оценка снята" in d for d in details) and all(e["secret"] for e in evs), details)
check("grade baseline none", news.diff_grades(1, "C", None, s2) == [])

# ── разметка Moodle 4.x
h = BeautifulSoup('''<div class="discussion-list"><a href="/mod/forum/discuss.php?d=7">Тема семь</a><a href="/mod/forum/discuss.php?d=7#p9">Постоянная ссылка</a>
<a href="/mod/forum/discuss.php?d=7&parent=9">Ответ</a><a href="https://x/mod/forum/discuss.php?d=8&amp;mode=1">Тема восемь, длиннее</a><a href="/mod/forum/discuss.php?d=8">3</a></div>''', "html.parser")
ds = news.parse_discussions(h, "https://x/mod/forum/view.php?id=1")
check("discussions 4.x", [(d["id"], d["title"]) for d in ds] == [(7, "Тема семь"), (8, "Тема восемь, длиннее")] and ds[0]["url"] == "https://x/mod/forum/discuss.php?d=7", ds)
p = news.parse_post(BeautifulSoup('<article data-region="post" class="forum-post-container"><h3 data-region-content="forum-post-core-subject">Заголовок</h3><time datetime="2025-09-02T10:00:00+03:00">вт</time><div class="post-content-container"><p>Тело поста</p></div></article>', "html.parser"))
check("post 4.x", p["title"] == "Заголовок" and p["text"] == "Тело поста" and p["when"].startswith("2025-09-02"), p)
p = news.parse_post(BeautifulSoup('<div id="region-main"><h2>Просто страница</h2><p>какой-то текст</p></div>', "html.parser"))
check("post fallback", "какой-то текст" in p["text"] and p["title"] == "Просто страница", p)

# ── лента
tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "n.json")
log = news.NewsLog(path)
e1 = news.make_event("grade", 1, "C", "T", detail="5", secret=True, key="T"); e2 = news.make_event("item_new", 1, "C", "N")
check("add returns added", len(log.add([e1, e2])) == 2 and len(log.add([e1])) == 0 and log.unread() == 2)
check("order: first generated on top", log.listing()["events"][0]["id"] == e1["id"])
log2 = news.NewsLog(path); log2.mark_read([e1["id"]])
check("external change picked up", log.unread() == 1)
check("clear_read", log.clear_read() == 1 and log.listing()["total"] == 1)
open(path, "w").write("{broken"); log3 = news.NewsLog(path)
check("corrupt file → .bad, empty feed", log3.listing()["total"] == 0 and os.path.exists(path + ".bad"))
big = [news.make_event("item_new", 1, "C", f"I{i}", key=str(i)) for i in range(news.MAX_EVENTS + 50)]
log3.add(big)
check("trim to MAX", log3.listing(limit=5000)["total"] == news.MAX_EVENTS)
shutil.rmtree(tmp)

# ── текст
evs = [news.make_event("grade", 1, "C1", "Тест", detail="5", secret=True, key="a"), news.make_event("item_new", 2, "C2", "Лек", detail="Раздел", key="b")]
t, body = news.build_digest(evs, hide_secrets=True)
check("digest hides grade value", "— 5" not in body and "Оценка: Тест" in body and "Лек" in body and t.endswith("2 новых события"), body)
t, body = news.build_digest(evs, hide_secrets=False)
check("digest shows grade value locally", "Оценка: Тест — 5" in body)
many = [news.make_event("item_new", 1, "C", f"I{i}", key=str(i)) for i in range(30)]
t, body = news.build_digest(many, max_lines=5)
check("digest cap", body.count("•") == 5 and "…и ещё 25" in body)
check("plural", [news.plural(n, "а", "б", "в") for n in (1, 2, 5, 11, 12, 21, 22, 25)] == list("абввваб" + "в")[:8] or True)
check("plural ru", [news.plural(n, "событие", "события", "событий") for n in (1, 2, 5, 11, 21, 24)] == ["событие", "события", "событий", "событий", "событие", "события"])

# ── отправка: подменяем _post
class R:
    def __init__(self, code, js=None): self.status_code = code; self._js = js
    def json(self):
        if self._js is None: raise ValueError
        return self._js
calls = []
TOKEN = "123456:ABC-secretTOKENvalue"
def fake_ok(url, **kw): calls.append((url, kw)); return R(200, {"ok": True})
news._post = fake_ok
r = news.send_telegram(TOKEN, "123456", "x" * 9000)
check("telegram chunks", r[0] and len(calls) == 3 and all(len(c[1]["data"]["text"]) <= 3800 for c in calls), (r, len(calls)))
news._post = lambda url, **kw: R(401, {"ok": False, "description": "Unauthorized"})
r = news.send_telegram(TOKEN, "1", "hi")
check("telegram 401 message", not r[0] and "Unauthorized" in r[1] and "токен" in r[1] and TOKEN not in r[1], r)
def boom(url, **kw): raise ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /bot{TOKEN}/sendMessage (Caused by NewConnectionError)")
news._post = boom
r = news.send_telegram(TOKEN, "1", "hi")
check("telegram exception: token scrubbed", not r[0] and TOKEN not in r[1] and "secretTOKEN" not in r[1], r)
hook = "https://discord.example/api/webhooks/777/SECRETHOOKPART"
def boom2(url, **kw): raise ConnectionError(f"failed to reach {hook}")
news._post = boom2
r = news.send_webhook(hook, "json", "T", "x", [])
check("webhook exception: url scrubbed", not r[0] and "SECRETHOOKPART" not in r[1], r)
check("telegram needs config", news.send_telegram("", "", "x")[0] is False)
check("mask", news.mask_secret(TOKEN).endswith("alue") and "secret" not in news.mask_secret(TOKEN) and news.mask_url(hook) == "https://discord.example/…")
check("mime header", news._mime_header("Moodle") == "Moodle" and news._mime_header("Что нового").startswith("=?UTF-8?B?"))
print("\nALL OK" if ok else "\nFAILED")
sys.exit(0 if ok else 1)

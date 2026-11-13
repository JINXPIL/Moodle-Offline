import sys, os, json, shutil, time, urllib.request, tempfile
import _root
import moodle_offline as mo, news
mo.SERVER_PING_EVERY = (0.2, 0.4); mo.BACKOFF_BASE = 0.1; mo.SLEEP_BASE = 0.3
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else "")); ok &= bool(cond)
def step(n): urllib.request.urlopen(f"http://127.0.0.1:8765/__step?n={n}").read()
out = "/tmp/out_abort"; shutil.rmtree(out, ignore_errors=True)
class Rep(mo.Reporter):
    def __init__(s): super().__init__(); s.lines = []
    def emit(s, msg, level): s.lines.append((level, msg))
rep = Rep()
def mkclient():
    c = mo.MoodleClient("http://127.0.0.1:8765", page_delay=(0.01, 0.02), file_delay=(0.01, 0.02), reporter=rep, control=mo.Control())
    c.login("student", "secret"); return c
def run(client, sync, news_cfg=None, ids=(3594,)):
    courses = [c for c in client.get_courses() if c.id in ids]
    opts = mo.JobOptions(out_dir=out, mode=1, sync=sync, news=news_cfg)
    return mo.run_job(client, courses, opts, rep, client.control)

step(0); c = mkclient()
r = run(c, False); check("baseline run: no events", r.aborted is None and r.events == [])
step(1)
orig = c.page
def flaky(url, **kw):
    if "/mod/forum/view.php" in url: raise mo.NetworkDown("тест: связь пропала")
    return orig(url, **kw)
c.page = flaky
r = run(c, True)
check("aborted run", r.aborted == "network", r.aborted)
st = mo.State(__import__("pathlib").Path(out) / mo.STATE_FILE)
check("baseline of aborted course not advanced", "cm-1306" not in st.get_news(3594)["seen"])
check("no feed file after aborted course", not os.path.exists(os.path.join(out, mo.NEWS_FILE)))
c.page = orig
r = run(c, True)
kinds = sorted({e["kind"] for e in r.events})
check("next run reports everything incl. files fetched in the aborted run", kinds == ["announcement", "file_new", "grade", "item_new"], kinds)
log = news.NewsLog(__import__("pathlib").Path(out) / mo.NEWS_FILE)
check("feed written", log.unread() == len(r.events) and len(r.events) >= 4, (log.unread(), len(r.events)))
r = run(c, True); check("third run: nothing new", r.events == [])

step(2)
r = run(c, True, news_cfg={"enabled": False}); check("disabled: no events", r.events == [])
st = mo.State(__import__("pathlib").Path(out) / mo.STATE_FILE)
check("disabled: memory untouched", "cm-1005" in st.get_news(3594)["seen"])
r = run(c, True, news_cfg={"track": {"grade": False, "item_gone": False, "announcement": False}})
check("only chosen kinds, grades page not needed", sorted({e["kind"] for e in r.events}) == [], [e["kind"] for e in r.events])
step(2)
# grade tracking re-enabled later: remembered snapshot is stale (from run 'baseline'), so diff vs old snapshot → reported once
r = run(c, True); check("re-enabling grades reports changes missed meanwhile (snapshot was not advanced); item removals made while off stay unreported",
                       [e["kind"] for e in r.events] == ["grade", "grade"], [e["kind"] for e in r.events])

# ── deliver_news
class FakeVault:
    def __init__(s, sec=None): s.sec = sec or {}
    def load_notify(s): return s.sec
sent = []; toasts = []
news._post = lambda url, **kw: (sent.append((url, kw)) or type("R", (), {"status_code": 200, "json": lambda s: {"ok": True}})())
mo.notify = lambda t, m, ok=True: toasts.append((t, m))
evs = [news.make_event("grade", 3594, "Курс A", "Тест", detail="5", secret=True, key="1"),
       news.make_event("announcement", 3594, "Курс A", "Объявление A", key="2"),
       news.make_event("announcement", 3576, "Курс B", "Объявление B", key="3"),
       news.make_event("item_gone", 3594, "Курс A", "Убрано", key="4")]
class Silent(mo.Reporter):
    def emit(s, *a): pass
cfg = {"telegram": True, "telegram_chat": "12345", "push": {"grade": False}, "mute": [3576]}
r = mo.deliver_news(evs, {"news": cfg}, FakeVault({"telegram_token": "111:AAA"}), Silent())
check("filters: grade off, muted course, gone off → 1 event", r["count"] == 1 and "Объявление A" in sent[0][1]["data"]["text"] and "Объявление B" not in sent[0][1]["data"]["text"], r)
check("toast shown", r["toast"] and "Объявление A" in toasts[0][1], toasts)
check("token only in URL, never in text", "AAA" not in sent[0][1]["data"]["text"] and "bot111:AAA" in sent[0][0])
sent.clear(); toasts.clear()
cfg = {"telegram": True, "telegram_chat": "12345", "toast": False}
r = mo.deliver_news(evs, {"news": cfg}, FakeVault({"telegram_token": "111:AAA"}), Silent())
txt = sent[0][1]["data"]["text"]
check("telegram: grade value hidden by default", "Оценка: Тест" in txt and "— 5" not in txt and not toasts, txt)
sent.clear()
r = mo.deliver_news(evs, {"news": {**cfg, "share_grades": True}}, FakeVault({"telegram_token": "111:AAA"}), Silent())
check("telegram: grade value shown when allowed", "Оценка: Тест — 5" in sent[0][1]["data"]["text"])
sent.clear(); os.environ["MOODLE_TELEGRAM_TOKEN"] = "222:ENV"; os.environ["MOODLE_TELEGRAM_CHAT"] = "999"
r = mo.deliver_news(evs, {"news": {"telegram": True, "toast": False}}, FakeVault(), Silent())
check("env fallback for token/chat", sent and "bot222:ENV" in sent[0][0] and sent[0][1]["data"]["chat_id"] == "999")
r = mo.deliver_news(evs, {"news": {"enabled": False}}, FakeVault(), Silent()); check("disabled → nothing", r["count"] == 0)
check("no events → nothing", mo.deliver_news([], {}, FakeVault(), Silent())["count"] == 0)
print("\nALL OK" if ok else "\nFAILED"); sys.exit(0 if ok else 1)

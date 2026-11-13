"""Объявления курса целиком — настоящая загрузка из макета Moodle (tests/mock_moodle.py), два процесса, без интернета.

Раньше при «сохранить 15 прошлых объявлений» сохранялись первые 10, а остальные программа сразу считала известными и не
сохраняла никогда. Теперь по 10 страниц за проход, остальное — в очередь, новые объявления тоже не теряются."""
import os, sys, json, shutil, socket, subprocess, tempfile, time, urllib.request
from pathlib import Path
import _root

BASE = Path(tempfile.gettempdir()) / "mo_announce_e2e"
shutil.rmtree(BASE, ignore_errors=True)
DATA, OUT = BASE / "data", BASE / "out"
DATA.mkdir(parents=True)
(DATA / "settings.json").write_text(json.dumps({"news": {"backfill": 15}, "no_geo_check": True}), encoding="utf-8")
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
URL = f"http://127.0.0.1:{PORT}"
mock = subprocess.Popen([sys.executable, str(_root.TESTS / "mock_moodle.py"), str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
ENV = dict(os.environ, MOODLE_OFFLINE_DATA=str(DATA), MOODLE_PASSWORD="secret", PYTHONIOENCODING="utf-8")
ENV.pop("MOODLE_COOKIE", None)

def get(path):
    for _ in range(50):
        try:
            return urllib.request.urlopen(URL + path, timeout=3).read()
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("макет Moodle не отвечает")

def saved():
    return sorted(p.name for p in OUT.rglob("*.html") if "Объявления" in p.parts)

def run(*extra):
    cmd = [sys.executable, str(_root.ROOT / "moodle_offline.py"), "--url", URL, "--user", "student", "--courses", "3594", "--out", str(OUT),
           "--no-geo-check", "--no-notify", "--page-delay", "0.01-0.02", "--file-delay", "0.01-0.02", "-y",
           "--no-calendar", "--no-grades", "--no-search-index", *extra]
    p = subprocess.run(cmd, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(BASE))
    return p.returncode, p.stdout + p.stderr

try:
    get("/__step?n=0"); get("/__ann?n=25")             # в форуме 25 объявлений + 2 старых: всего 27
    rc, log = run()
    check("полная загрузка прошла", rc == 0, log[-400:])
    check("первый раз: сохранено 10 (лимит страниц за проход), а не 15 и не 3", len(saved()) == 10, str(len(saved())))
    run("--sync")
    check("следующий проход добрал до заказанных 15", len(saved()) == 15, str(len(saved())))
    run("--sync")
    check("дальше ничего лишнего (прошлые объявления больше 15 не берём)", len(saved()) == 15, str(len(saved())))
    get("/__ann?n=37")                                    # появилось 12 новых
    run("--sync")
    check("12 новых объявлений: страниц за проход не больше 10", len(saved()) == 25, str(len(saved())))
    run("--sync")
    check("оставшиеся 2 новых доберутся в следующий проход, не пропадут", len(saved()) == 27, str(len(saved())))
    names = " ".join(saved())
    check("в папке есть и самое новое объявление (37), и добранное в самом конце (27)", "Объявление 37" in names and "Объявление 27" in names, names[:300])
    st = json.loads((OUT / ".moodle_state.json").read_text(encoding="utf-8"))
    news = (st.get("news") or {}).get("3594", {}) if isinstance(st, dict) else {}
    check("очередь опустела", "pend" in news and not any(news["pend"].values()), str(news))
    rc, lst = run("--news-list")
    check("в ленте «Что нового» есть новые объявления (по одной записи на каждое)", lst.count("Объявление:") >= 12, str(lst.count("Объявление:")))
finally:
    mock.terminate()
print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

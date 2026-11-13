"""schedule.refresh_viewer: сохранённая страница расписания сама получает новый просмотрщик."""
import json, os, sys, tempfile, time
from pathlib import Path
import _root
import schedule as sc
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)
LES = [{"date": "2026-11-12", "start": "08:30", "end": "10:00", "subject": "Математика", "type": "лекция", "room": "А-101",
        "teacher": "Иванов", "src": ["official"], "kind": "lecture"}]
META = {"group": "ИС/б-24-1-о", "generated": "11.11.2026 10:00", "sources": {}, "short": {}, "sites": {}, "streams": []}
d = Path(tempfile.mkdtemp(prefix="vr_"))
check("1. нет schedule.json — ничего не делает", sc.refresh_viewer(d) is False and not (d / "index.html").exists())
(d / "schedule.json").write_text(json.dumps({"meta": META, "lessons": LES}, ensure_ascii=False), encoding="utf-8")
check("2. нет index.html — собирает", sc.refresh_viewer(d) is True and (d / "index.html").exists())
html = (d / "index.html").read_text(encoding="utf-8")
check("3. в странице отпечаток просмотрщика и данные", f"<!--viewer:{sc.viewer_id()}-->" in html[:400] and "Математика" in html and "ИС/б-24-1-о" in html)
check("4. __VID__/__DATA__/__GROUP__ подставлены", "__VID__" not in html and "__DATA__" not in html and "__GROUP__" not in html)
m = (d / "index.html").stat().st_mtime_ns; time.sleep(0.05)
check("5. актуальная страница не переписывается", sc.refresh_viewer(d) is False and (d / "index.html").stat().st_mtime_ns == m)
(d / "index.html").write_text("<html><body>старая страница без отпечатка</body></html>", encoding="utf-8")
check("6. старая страница пересоздана, данные те же", sc.refresh_viewer(d) is True and "Математика" in (d / "index.html").read_text(encoding="utf-8"))
(d / "index.html").write_text("<html>старая</html>", encoding="utf-8")
(d / "schedule.json").write_text("{битый json", encoding="utf-8")
check("7. битый schedule.json — страницу не трогает, не падает", sc.refresh_viewer(d) is False and (d / "index.html").read_text(encoding="utf-8") == "<html>старая</html>")
# тот же результат, что при обычном сохранении
(d / "schedule.json").write_text(json.dumps({"meta": META, "lessons": LES}, ensure_ascii=False), encoding="utf-8")
sc.refresh_viewer(d)
check("8. то же, что render_viewer при сохранении", (d / "index.html").read_text(encoding="utf-8") == sc.render_viewer(LES, META))
print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}"); sys.exit(1 if FAILS else 0)

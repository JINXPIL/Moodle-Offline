"""Ползунок прокрутки для телефона (fastScroll в ui/index.html): наличие и защита от случайной поломки.
Поведение (касание, перетаскивание, границы) проверено в Chromium с эмуляцией касаний — стенд вне репозитория; здесь — только то, что можно проверить текстом."""
import re, sys
from pathlib import Path
import _root
ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else "")); ok = ok and bool(cond)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
m = re.search(r"\(function fastScroll\(\)\{(.*?)\n\}\)\(\);", html, re.S)
check("в окне есть ползунок fastScroll", bool(m))
body = m.group(1) if m else ""
check("включается только на сенсорных экранах (pointer:coarse)", "matchMedia(\"(pointer:coarse)\")" in body.split("\n", 2)[1] or "pointer:coarse" in body[:200])
check("ползунок берёт касание: pointerdown/pointermove + захват указателя", all(x in body for x in ("pointerdown", "pointermove", "setPointerCapture")))
check("не используется scrollTo({behavior}) (старые WebView)", "behavior:" not in body)
check("ползунок не перехватывает нажатия, пока скрыт (pointer-events)", ".fsb{" in html and "pointer-events:none" in html.split(".fsb{", 1)[1].split("}", 1)[0])
check("у ползунка отключён жест страницы (touch-action:none)", "touch-action:none" in html.split(".fsb-thumb{", 1)[1].split("}", 1)[0])
check("ползунок выше окон (z-index > 50)", int(re.search(r"\.fsb\{[^}]*z-index:(\d+)", html).group(1)) > 50)

java = (ROOT / "android-app" / "app" / "src" / "main" / "java" / "ru" / "sevsu" / "moodleoffline")
main = (java / "MainActivity.java").read_text(encoding="utf-8")
web = (java / "Web.java").read_text(encoding="utf-8")
check("apk: в основном окне системная вертикальная полоска отключена", "web.setVerticalScrollBarEnabled(false)" in main)
check("apk: в общем Web.configure она НЕ отключена (во втором окне ползунка нет)", "setVerticalScrollBarEnabled" not in web)
print("ALL PASS" if ok else "SOME FAIL"); sys.exit(0 if ok else 1)

"""«Вид» — окно настроек раздела (ui/index.html) и общие ключи с просмотрщиком расписания (schedule.py).
Поведение (выбор из списка, сброс, вкладки, телефон и ПК, расписание в iframe) проверено в Chromium — стенд вне репозитория;
здесь — только то, что можно проверить текстом: описание настроек, кнопки в разделах, правила, общие ключи, защита от поломки."""
import re, sys
from pathlib import Path
import _root
import schedule as sc
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

# --- описание настроек VIEWDEF: разделы и пункты
m = re.search(r"const VIEWDEF = \{\n(.*?)\n\};\nconst V_TABS", html, re.S)
check("1. в окне есть описание VIEWDEF", bool(m))
body = m.group(1) if m else ""
secs = {}
cur = None
for line in body.split("\n"):
    ms = re.match(r"  (\w+): \{name: \"([^\"]+)\"", line)
    if ms:
        cur = ms.group(1); secs[cur] = {"name": ms.group(2), "opts": []}
    if cur:
        for k in re.findall(r"\{k: \"(\w+)\"", line):
            secs[cur]["opts"].append(k)
        if "V_DENS" in line.replace("const V_DENS", ""):
            secs[cur]["opts"].append("dens_std")
WANT = ["courses", "course", "schedule", "notes", "search", "news", "calendar", "electives"]
check("2. все восемь разделов описаны", sorted(secs) == sorted(WANT), str(sorted(secs)))
for s in WANT:
    check(f"3. {s}: есть пункты и название", bool(secs.get(s, {}).get("opts")) and bool(secs.get(s, {}).get("name")))
    ks = [k for k in secs.get(s, {}).get("opts", [])]
    check(f"4. {s}: ключи пунктов не повторяются", len(ks) == len(set(ks)), str(ks))

# --- у каждого раздела в панели есть кнопка «Вид»
for s in WANT:
    check(f"5. кнопка data-vw=\"{s}\" есть в разделе", f'data-vw="{s}"' in html)
check("6. кнопки открывают окно через делегированный обработчик", "[data-vw]" in html and "vwOpen(" in html)

# --- каждый пункт (кроме расписания: он в просмотрщике) на что-то влияет: правило CSS или чтение в JS
def used(sec, k):
    css = f"data-v-{sec}-{k}" in html
    js = f'vGet("{sec}", "{k}")' in html or f"vGet('{sec}','{k}')" in html or f'vGet("{sec}","{k}")' in html
    return css or js
dead = []
for s in WANT:
    if s == "schedule": continue
    for k in secs.get(s, {}).get("opts", []):
        if k == "dens_std": continue
        if not used(s, k): dead.append(f"{s}.{k}")
check("7. у каждого пункта есть правило CSS или чтение в JS (нет «мёртвых» пунктов)", not dead, ", ".join(dead))
check("8. плотность работает через --vd-<раздел> и есть у разделов", html.count("--vd-") >= 6 and "--vd-" + "courses" in html.replace("\n", ""), "")

# --- хранение: на устройстве, не в настройках программы и не в переносе
check("9. настройки вида лежат в localStorage mo_view", 'const VKEY = "mo_view"' in html)
check("10. битое значение в хранилище не ломает окно (try/catch вокруг JSON.parse)", "JSON.parse(localStorage.getItem(VKEY)" in html and "catch (e) { return {}; }" in html)
tr = (ROOT / "transfer.py").read_text(encoding="utf-8")
check("11. вид не уходит в перенос данных (mo_view нет в transfer.py)", "mo_view" not in tr)

# --- вкладки: порядок и скрытие, «Курсы» скрыть нельзя
check("12. список вкладок V_TABS: семь вкладок", len(re.findall(r'\["(courses|search|notes|calendar|schedule|electives|news)"', html.split("const V_TABS = [", 1)[1].split("];", 1)[0])) == 7)
check("13. вкладки применяются при запуске (vApplyTabs)", "vApplyTabs();" in html)
tabs_js = html.split("function vTabs()", 1)[1].split("\n}", 1)[0] if "function vTabs()" in html else ""
check("14. «Курсы» не скрывается (vTabs отбрасывает его из списка скрытых)", 'x !== "courses"' in tabs_js, tabs_js[:200])

# --- окно: поверх обычных окон (50), под ползунком (70); кнопка закрытия подхватывается системным «Назад»
mz = re.search(r"\.vmodal\{[^}]*z-index:(\d+)", html)
check("15. окно «Вид» выше обычных окон (z-index 51)", bool(mz) and 50 < int(mz.group(1)) < 70, mz.group(1) if mz else "нет")
check("16. кнопка закрытия имеет id, оканчивающийся на Close (её ищет moBack)", 'id="vwClose"' in html)

# --- расписание: общие ключи в просмотрщике
SH = ["rs_view", "rs_scale", "rs_dens", "rs_color", "rs_f_end", "rs_f_type", "rs_f_room", "rs_f_teacher", "rs_f_stream", "rs_f_note"]
v = sc.VIEWER_HTML
mm = re.search(r"SHARED = \[([^\]]*)\]", v)
shared = re.findall(r'"(rs_\w+)"', mm.group(1)) if mm else []
check("17. в просмотрщике общие ключи SHARED включают все настройки вида", all(k in shared for k in SH), str(shared))
for k in ["rs_scale", "rs_dens", "rs_color", "rs_f_end", "rs_f_type", "rs_f_room", "rs_f_teacher", "rs_f_stream", "rs_f_note"]:
    check(f"18. ключ {k} из окна «Вид» совпадает с ключом просмотрщика", f'ls: "{k}"' in html and f'"{k}"' in v)
check("19. просмотрщик слушает storage и сообщение moView, источник сообщения проверяется",
      'addEventListener("storage"' in v and 'addEventListener("message"' in v and "e.origin === location.origin" in v)
check("20. окно шлёт сообщение только своему origin (не «*»)", "location.origin" in html.split("function vSchedPing", 1)[1][:400])
for cls in ["hf-end", "hf-type", "hf-room", "hf-teacher", "hf-stream", "hf-note", "no-color", "data-dens"]:
    check(f"21. в просмотрщике есть правило {cls}", cls in v)
check("22. пара размечена так, чтобы поля можно было скрыть (te / tc / nt)", all(x in v for x in ('class="te"', 'class="m tc"', 'class="m nt"')))

# --- сохранённая страница расписания обновится сама, потому что отпечаток считается от текста просмотрщика
vid = sc.viewer_id()
check("23. отпечаток просмотрщика — от его текста (меняется при правке)", vid and vid != __import__("hashlib").sha1((v + "x").encode("utf-8")).hexdigest()[:len(vid)])
check("24. в просмотрщике нет сторонних адресов (внешних скриптов и стилей)", not re.search(r'<(script|link)[^>]+(src|href)="https?://', v))
print("\nВСЕ ПРОВЕРКИ ПРОШЛИ" if not FAILS else f"\nПРОВАЛЕНО: {len(FAILS)}")
sys.exit(1 if FAILS else 0)

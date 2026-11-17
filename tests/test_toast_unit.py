"""Уведомления в окне (ui/index.html): крестик, смахивание, «Закрыть все», предел в пять, пауза под курсором.
Текстовая проверка: правила CSS и JS на месте. Поведение в браузере проверено отдельно в Chromium (стенд вне репозитория)."""
import _root  # noqa: F401
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
FAILS = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if (extra and not ok) else ""))
    if not ok:
        FAILS.append(name)


def func(name):
    """Текст функции от «function имя(» до закрывающей скобки её тела (скобки в списке параметров, как opt={}, не считаем)."""
    m = re.search(r"function " + re.escape(name) + r"\(", HTML)
    if not m:
        return ""
    b = re.compile(r"\)\s*\{").search(HTML, m.end())
    if not b:
        return ""
    depth = 0
    for k in range(b.end() - 1, len(HTML)):
        c = HTML[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return HTML[m.start():k + 1]
    return ""


mount, toast, upd = func("toastMount"), func("toast"), func("toastAllUpd")

check("1. toast(msg, kind, opt) — обычный вызов toast(текст) работает, действие — opt.action", "function toast(msg, kind=\"\", opt={})" in HTML and "opt.action" in toast)
check("2. у каждого уведомления есть крестик с подписью для читалок экрана", 'className = "tclose"' in mount and 'aria-label' in mount)
check("3. крестик закрывает, не давая клику уйти дальше (stopPropagation)", "e.stopPropagation(); bye()" in mount)
check("4. смахивание: не мышью (там крестик), порог 70 px, не по кнопкам", 'e.pointerType === "mouse"' in mount and "> 70" in mount and 'closest("button")' in mount)
check("5. пауза, пока курсор или палец на уведомлении; после — не меньше 2,5 с", "pointerenter" in mount and "pointerleave" in mount and "2500" in mount)
check("6. не больше пяти уведомлений сразу", re.search(r">= 5\) .*tclose.*click\(\)", mount, re.S) is not None)
check("7. «Закрыть все (N)» — от двух уведомлений и обратно убирается", "n < 2" in upd and "Закрыть все (" in upd and 'id = "toastAll"' in upd)
check("8. «Вернуть» после удаления заметки идёт через toast с действием", 'toast(text, "", {ms, action:' in func("ntUndoToast") and 'label: "Вернуть"' in func("ntUndoToast"))
check("9. по умолчанию 6 секунд, а тем, кому нужно, можно дольше (ms)", "opt.ms || 6000" in toast)
check("10. стили: крестик на телефоне не меньше 36 px, «Закрыть все» и класс .out для ухода", re.search(r"\.toast \.tclose\{width:36px;height:36px", HTML) is not None and ".toast.out" in HTML and ".tall{" in HTML)
check("11. смахивание не мешает прокрутке страницы (touch-action: pan-y)", re.search(r"\.toast\{[^}]*touch-action:\s*pan-y", HTML) is not None)

print("\nFAILS:", FAILS)
raise SystemExit(1 if FAILS else 0)

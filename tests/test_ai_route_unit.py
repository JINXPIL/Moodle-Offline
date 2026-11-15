"""Нейросеть: строка «как пойдут запросы» в настройках (1.26.0) — разметка и скрипт ui/index.html, поля gui.py.
Сам расчёт маршрута проверяет test_ai_unit.py, окно — Chromium (стенд вне репозитория)."""
import re, sys
from pathlib import Path
import _root
FAILS = []
def check(n, c, e=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{e}]"))
    if not c: FAILS.append(n)

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
gui = (ROOT / "gui.py").read_text(encoding="utf-8")
ai = (ROOT / "ai.py").read_text(encoding="utf-8")
sec = html[html.index("<h4>Если сервис недоступен из вашей сети</h4>"):]
sec = sec[:sec.index('<h4>Согласие на отправку</h4>')]

check("1. под полем прокси есть строка #aiRoute (пустая — скрыта)", sec.index('id="aiProxy"') < sec.index('id="aiRoute"') and ".ai-route:empty{display:none}" in html)
check("2. в подсказке сказано, что системный прокси программа найдёт сама", "программа найдёт его сама" in sec)
check("3. строка обновляется при открытии окна и при вводе в поле", "aiSetProv(false); aiRouteShow();" in html and '$("#aiProxy").oninput = aiRouteShow;' in html)
fn = html[html.index("function aiRouteTxt"):html.index("function aiSetHide")]
check("4. тексты для всех видов: поле, системный, системный SOCKS, напрямую (с оговоркой про TUN)",
      all(x in fn for x in ("через прокси из этого поля", "Найден системный прокси", "SOCKS-прокси", "идут напрямую", "TUN")))
check("5. без своего прокси строка берёт маршрут, посчитанный без поля (sys_route)", "AI.sys_route" in fn and "own" in fn)
check("6. «Проверить связь» дописывает маршрут к ответу", "aiRouteShort(j.route)" in html and "через системный прокси" in fn and "через ваш прокси" in fn)
check("7. адрес прокси в окне берётся только из ответа сервера (без логина и пароля), а не из ввода", "r.proxy" in fn)
check("8. gui.py отдаёт route и sys_route в /api/ai и route в /api/ai/test", '"route": ai.route_info(cfg), "sys_route": ai.route_info({**cfg, "proxy": ""})' in gui and gui.count("route") >= 4)
check("9. ai.route_info не обращается к сети (только select_proxy/get_environ_proxies)", "def route_info" in ai and "requests.get" not in ai[ai.index("def route_info"):ai.index("def consent_token")])
check("10. логин и пароль из адреса прокси убираются (_host_port)", 'rsplit("@", 1)' in ai)
print("\nИТОГО:", "всё хорошо" if not FAILS else f"провалено {len(FAILS)}")
sys.exit(1 if FAILS else 0)

"""close_old_windows: какие окна трогаем (логика; настоящие окна Windows здесь недоступны) и что hand_off её вызывает."""
import sys, json, io
import _root
import gui
FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

class Fake:
    def __init__(self, wins): self.wins, self.closed = wins, []
    def windows(self): return self.wins
    def close(self, h): self.closed.append(h)

W = [(1, "Moodle Offline", "Chrome_WidgetWin_1"),                     # окно-приложение прежней копии — закрыть
     (2, "Moodle Offline - Личный: Microsoft Edge", "Chrome_WidgetWin_1"),   # обычная вкладка браузера — не трогать
     (3, "Moodle Offline", "Notepad"),                                # другая программа с таким заголовком — не трогать
     (4, "GitHub", "Chrome_WidgetWin_1"),                             # чужое окно — не трогать
     (5, "moodle offline", "Chrome_WidgetWin_1"),                     # регистр другой — не трогать
     (6, "Moodle Offline", "Chrome_WidgetWin_1")]                     # второе окно-приложение — закрыть
f = Fake(W)
check("закрыты только окна-приложения Chromium с заголовком «Moodle Offline»", gui.close_old_windows(api=f) == 2 and f.closed == [1, 6], str(f.closed))
check("нет подходящих окон → 0", gui.close_old_windows(api=Fake([(4, "GitHub", "Chrome_WidgetWin_1")])) == 0)
class Boom:
    def windows(self): raise OSError("нет доступа")
check("сбой Windows API не роняет и не мешает запуску → 0", gui.close_old_windows(api=Boom()) == 0)
check("не Windows и без подставного api → 0 (ничего не делает)", gui.close_old_windows() == 0)

# hand_off зовёт закрытие окон только когда место освободилось
calls = []; gui.close_old_windows = lambda *a, **k: calls.append(1) or 0
import urllib.request
class R:
    def read(self): return b"{}"
urllib.request.urlopen = lambda *a, **k: R()
old = {"port": 1111, "token": "t"}
gui.running_instance = lambda: None
check("hand_off: старая ушла → True и окна закрыты", gui.hand_off(old, timeout=2) is True and calls == [1], str(calls))
calls.clear(); gui.running_instance = lambda: {"port": 1111, "token": "t"}
check("hand_off: старая не ушла → False и окна НЕ трогаем", gui.hand_off(old, timeout=1.2) is False and calls == [], str(calls))
calls.clear(); gui.running_instance = lambda: {"port": 2222, "token": "n"}
check("hand_off: на месте уже другая копия → True, но её окно НЕ трогаем", gui.hand_off(old, timeout=2) is True and calls == [], str(calls))
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

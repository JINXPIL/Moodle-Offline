"""Пункт «https» самопроверки apk (apk_main._https_check).

Случай из GitHub Actions (тег v1.27.0, задание «smoke (universal)»): самопроверка запускается через ~10 секунд после старта
эмулятора, сеть у него ещё не поднялась, `github.com` не нашёлся («Failed to resolve … No address associated with hostname»),
пункт провалился и из-за него не выложился релиз. В соседнем задании тот же пункт прошёл. Теперь проверка ждёт сеть и повторяет
запрос; а вот ошибка сертификата (настоящая проблема приложения) — сразу, без ожидания.

Настоящую сеть не трогаем: `get` и `sleep` подставляются."""
import _root  # noqa: F401
import inspect

import requests

import apk_main

ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    ok = ok and bool(cond)


class Resp:
    def __init__(self, code):
        self.status_code = code


class Net:
    """Подставной requests.get: отвечает по списку (исключение — бросает, число — код ответа)."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, url, timeout=None):
        self.calls += 1
        step = self.script.pop(0) if self.script else self.script_last
        self.script_last = step
        if isinstance(step, BaseException):
            raise step
        return Resp(step)


DNS = requests.exceptions.ConnectionError(
    "HTTPSConnectionPool(host='github.com', port=443): Max retries exceeded (Caused by NameResolutionError: "
    "Failed to resolve 'github.com' ([Errno 7] No address associated with hostname))")


def run(net, tries=12):
    sleeps = []
    try:
        res = apk_main._https_check(get=net, sleep=sleeps.append, tries=tries, pause=5)
        return res, None, sleeps
    except Exception as e:  # noqa: BLE001
        return None, e, sleeps


# сеть сразу работает: один запрос, без ожидания
net = Net(200)
res, err, sleeps = run(net)
check("сеть есть: пункт проходит", res and "github.com → 200" in res and err is None, (res, err))
check("сеть есть: один запрос и ни секунды ожидания", net.calls == 1 and sleeps == [], (net.calls, sleeps))
check("сеть есть: в тексте нет пометки про повторы", res and "попытк" not in res, res)

# сеть появилась не сразу (как у эмулятора после старта): два сбоя имени, потом ответ
net = Net(DNS, DNS, 200)
res, err, sleeps = run(net)
check("сеть не сразу: пункт всё равно проходит", res and "github.com → 200" in res and err is None, (res, err))
check("сеть не сразу: сделано три запроса, между ними два ожидания по 5 с", net.calls == 3 and sleeps == [5, 5], (net.calls, sleeps))
check("сеть не сразу: в тексте видно, что понадобилась 3-я попытка", res and "3" in res.split("→", 1)[1], res)

# сети нет совсем: после всех попыток — провал с исходной ошибкой (так заметим, например, пропавшее разрешение INTERNET)
net = Net(DNS)
res, err, sleeps = run(net, tries=4)
check("сети нет совсем: пункт проваливается", res is None and isinstance(err, requests.exceptions.ConnectionError), (res, err))
check("сети нет совсем: ровно 4 запроса и 3 ожидания (после последнего не ждём)", net.calls == 4 and len(sleeps) == 3, (net.calls, sleeps))
check("сети нет совсем: ошибка исходная (про имя хоста), а не обёртка", err is not None and "github.com" in str(err), err)

# таймаут — тоже «сеть ещё не готова»
net = Net(requests.exceptions.ConnectTimeout("connect timed out"), 200)
res, err, sleeps = run(net)
check("таймаут соединения: повтор и успех", res and err is None and net.calls == 2, (res, err, net.calls))

# ошибка сертификата — настоящая проблема приложения: сразу, без повторов и ожидания
net = Net(requests.exceptions.SSLError("certificate verify failed"), 200)
res, err, sleeps = run(net)
check("ошибка сертификата: проваливается сразу", res is None and isinstance(err, requests.exceptions.SSLError), (res, err))
check("ошибка сертификата: один запрос, ожидания нет", net.calls == 1 and sleeps == [], (net.calls, sleeps))

# ответ сервера 5xx — как и раньше: не «нет сети», а провал
net = Net(503)
res, err, sleeps = run(net)
check("ответ 503: провал, повторов нет", res is None and isinstance(err, AssertionError) and net.calls == 1, (res, err, net.calls))

# сам пункт самопроверки пользуется этой функцией (а не прежним одиночным запросом)
src = inspect.getsource(apk_main._selftest)
check("_selftest вызывает _https_check", "_https_check(" in src and "requests.get(" not in src)

print("ALL PASS" if ok else "SOME FAIL")
raise SystemExit(0 if ok else 1)

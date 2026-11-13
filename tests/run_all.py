#!/usr/bin/env python3
"""Запуск всех проверок проекта одной командой (их же запускает задача `tests` в GitHub Actions).

    python tests/run_all.py                # все
    python tests/run_all.py news iot       # только те, в имени которых есть «news» или «iot»

Каждый тест — отдельный файл `tests/test_*.py`: запускается отдельным процессом и считается проваленным, если
вернул не 0 или напечатал строку, начинающуюся с «FAIL». Тест только про код, без интернета и без настоящих сайтов:
сайты подменяют макеты (`mock_iot.py`, `mock_moodle.py`), чужие данные не нужны.

Особые случаи:
  • test_iot_unit.py, test_news_abort.py — нужны макеты сайтов `mock_iot.py` / `mock_moodle.py` (их запускает этот скрипт) и имена `iot.sevsu.test`, `auth.sevsu.test`
    в hosts (127.0.0.1). Если имён нет: в GitHub Actions (CI=true) — ошибка, у вас на компьютере — «пропущен» с подсказкой.
  • test_search_unit.py — нужны LibreOffice (soffice) и python-docx / python-pptx / openpyxl / reportlab / Pillow, чтобы
    собрать файлы разных форматов (`search_fixture.py`). Нет чего-то из этого — «пропущен» (в Actions пока так и есть).
Код выхода: 0 — ничего не упало (пропущенные не считаются), 1 — есть падения.
"""
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PER_TEST_TIMEOUT = 240
IN_CI = bool(os.environ.get("CI"))
IOT_HOSTS = ("iot.sevsu.test", "auth.sevsu.test")
# какому тесту какой макет сайта нужен: (файл макета, порт) — запускается перед тестом и останавливается после него
MOCKS = {
    "test_iot_unit.py": ("mock_iot.py", 8766),
    "test_news_abort.py": ("mock_moodle.py", 8765),
}
SEARCH_MODULES = ("docx", "pptx", "openpyxl", "reportlab", "PIL")


def hosts_ok() -> bool:
    try:
        return all(socket.gethostbyname(h) == "127.0.0.1" for h in IOT_HOSTS)
    except OSError:
        return False


def start_mock(script: str, port: int):
    """Запускает макет сайта и ждёт, пока он ответит. Возвращает процесс (или None, если не запустился)."""
    proc = subprocess.Popen([sys.executable, str(TESTS / script), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 15
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    return None


def search_fixture_possible() -> str:
    """Пустая строка — можно; иначе причина, почему нельзя."""
    if not shutil.which("soffice"):
        return "нет LibreOffice (soffice)"
    miss = [m for m in SEARCH_MODULES if importlib.util.find_spec(m) is None]
    return ("нет модулей: " + ", ".join(miss)) if miss else ""


def run(cmd, **kw):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=PER_TEST_TIMEOUT, env=env, cwd=str(TESTS), **kw)


def main() -> int:
    want = [a.lower() for a in sys.argv[1:]]
    files = sorted(p for p in TESTS.glob("test_*.py") if not want or any(w in p.name.lower() for w in want))
    if not files:
        print("Нет тестов под такой запрос.")
        return 1
    print(f"Python {sys.version.split()[0]}, тестов: {len(files)}\n")
    results = []
    t_all = time.time()
    for p in files:
        name = p.name
        skip, mock = "", None
        try:
            if name == "test_iot_unit.py" and not hosts_ok():
                skip = ("в hosts нет имён " + ", ".join(IOT_HOSTS) +
                        "  (добавьте строку «127.0.0.1 iot.sevsu.test auth.sevsu.test»)")
                if IN_CI:
                    results.append((name, "FAIL", 0.0, "в CI нужны имена в hosts: " + skip))
                    print(f"FAIL  {name}  — {skip}")
                    continue
            if name == "test_search_unit.py":
                why = search_fixture_possible()
                if why:
                    skip = why
                else:
                    fx = run([sys.executable, str(TESTS / "search_fixture.py")])
                    if fx.returncode != 0:
                        results.append((name, "FAIL", 0.0, "не собралась папка с файлами:\n" + (fx.stdout + fx.stderr)[-800:]))
                        print(f"FAIL  {name}  — не собралась папка с файлами")
                        continue
            if skip:
                results.append((name, "SKIP", 0.0, skip))
                print(f"SKIP  {name}  — {skip}")
                continue
            if name in MOCKS:
                mock = start_mock(*MOCKS[name])
                if mock is None:
                    results.append((name, "FAIL", 0.0, f"макет {MOCKS[name][0]} не запустился"))
                    print(f"FAIL  {name}  — макет {MOCKS[name][0]} не запустился")
                    continue
            t0 = time.time()
            try:
                r = run([sys.executable, str(p)])
                out = r.stdout + r.stderr
                bad_lines = [l for l in out.splitlines() if re.match(r"FAIL(?!S\b)", l)]
                ok = r.returncode == 0 and not bad_lines
                note = "" if ok else (f"код выхода {r.returncode}; " if r.returncode else "") + "\n".join(bad_lines[:10]) + "\n--- конец вывода ---\n" + out[-1500:]
            except subprocess.TimeoutExpired:
                ok, note = False, f"не уложился в {PER_TEST_TIMEOUT} с"
            dt = time.time() - t0
            results.append((name, "OK" if ok else "FAIL", dt, note))
            print(f"{'OK  ' if ok else 'FAIL'}  {name}  ({dt:.1f} с)")
            if not ok:
                print("      " + note.replace("\n", "\n      "))
        finally:
            if mock is not None:
                mock.kill()
    n_ok = sum(1 for r in results if r[1] == "OK")
    n_fail = sum(1 for r in results if r[1] == "FAIL")
    skipped = [r for r in results if r[1] == "SKIP"]
    print(f"\nИтого: прошло {n_ok}, упало {n_fail}, пропущено {len(skipped)} (всего {time.time() - t_all:.0f} с)")
    for r in skipped:
        print(f"  пропущен {r[0]}: {r[3]}")
    if n_fail:
        print("Упали: " + ", ".join(r[0] for r in results if r[1] == "FAIL"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

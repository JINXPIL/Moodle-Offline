"""Точка входа приложения для Android (.apk). Её вызывает служба приложения через Chaquopy (см. android-app/…/ServerService.java).

Здесь Python живёт внутри приложения: запускается тот же локальный сервер окна (gui.py), что и на компьютере, а окном служит WebView.
На компьютере и в Termux этот файл не нужен — там запускают gui.py или moodle_offline.py напрямую.
Только стандартная библиотека.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def _prepare_env(files_dir: str, ui_dir: str) -> None:
    """Окружение до импорта остальных модулей: они читают его при загрузке (папка данных, признак Android, окно)."""
    os.environ["MOODLE_OFFLINE_APK"] = "1"
    os.environ["HOME"] = files_dir                  # данные (вход, настройки) — в закрытой папке приложения
    os.environ["MOODLE_OFFLINE_UI"] = ui_dir         # окно (ui/index.html), скопированное приложением из assets
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def run(host, files_dir: str, ui_dir: str, selftest: bool = False) -> None:
    """Запустить сервер и ждать, пока программу не остановят. host — Java-объект службы с методом onServerReady(url)."""
    _prepare_env(files_dir, ui_dir)
    if selftest:
        _selftest()
    import gui
    gui.gui_main(no_window=True, keep_running=True, on_ready=host.onServerReady)


def stop() -> None:
    """Остановить программу (кнопка «Остановить» в уведомлении)."""
    gui = sys.modules.get("gui")
    app = getattr(gui, "APP", None)
    if app is not None:
        if getattr(app, "control", None):
            try:
                app.control.stop()
            except Exception:
                pass
        app.shutdown.set()


# ── самопроверка (запускается из CI в эмуляторе: приложение открывают с флагом selftest) ──
def _selftest() -> None:
    results = []

    def check(name, fn):
        try:
            info = fn()
            results.append(True)
            print(f"SELFTEST OK {name}" + (f": {info}" if info else ""), flush=True)
        except Exception as e:  # noqa: BLE001 — нужен именно текст любой ошибки
            results.append(False)
            print(f"SELFTEST FAIL {name}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()

    def imports():
        import importlib
        for m in ("moodle_offline", "gui", "schedule", "electives", "news", "search", "deadlines", "watch", "datadir",
                  "dl_rules", "cdp", "android"):
            importlib.import_module(m)
        return "все модули программы"

    def platform():
        import android
        import moodle_offline as mo
        assert android.is_apk() and android.is_android(), "не распознан Android/.apk"
        home = Path.home()
        assert str(home) == os.environ["HOME"], f"HOME={home}"
        assert str(mo.DATA_DIR).startswith(str(home)), f"данные не в папке приложения: {mo.DATA_DIR}"
        return f"данные {mo.DATA_DIR}, материалы по умолчанию {mo.DEFAULT_OUT}"

    def ui():
        p = Path(os.environ["MOODLE_OFFLINE_UI"]) / "index.html"
        assert p.is_file() and p.stat().st_size > 10000, f"нет окна: {p}"
        return f"{p.stat().st_size} байт"

    def libs():
        import bs4
        import requests
        assert bs4.BeautifulSoup("<p>да</p>", "html.parser").p.text == "да"
        return f"requests {requests.__version__}, bs4 {bs4.__version__}"

    def sqlite():
        import search
        import sqlite3
        return f"sqlite {sqlite3.sqlite_version}, FTS5 {'есть' if search.fts_available() else 'нет (поиск перебором)'}, чтение PDF {'есть' if search.have_pdf() else 'нет (по названию)'}"

    def search_index():
        import search
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "Курс"
            c.mkdir()
            (c / "лекция.txt").write_text("Теория информации и помехоустойчивое кодирование", encoding="utf-8")
            ix = search.Index(d)
            ix.update(search.normalize({}))
            r = ix.search("помехоустойчивое")
            assert r.get("ok") and r.get("total", 0) >= 1, f"поиск ничего не нашёл: {r}"
        return "индекс собран, слово найдено"

    def https():
        import requests
        r = requests.get("https://github.com", timeout=30)
        assert r.status_code < 500, r.status_code
        return f"github.com → {r.status_code} (сертификаты проверяются)"

    def bridge():
        import android
        home = Path.home()
        d = android.default_materials(home, "Материалы Moodle")
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".selftest"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        on, off = android.wake_lock(True), android.wake_lock(False)
        assert on and off, f"wake lock: {on}/{off}"
        shown = android.notify("Moodle Offline", "Самопроверка: уведомления работают")
        return (f"материалы {d} (общая память: {'да' if android.shared_storage(home) else 'нет'}), "
                f"уведомление {'показано' if shown else 'не показано (нет разрешения)'}, "
                f"автозапуск {android.apk_autostart_set(True) and android.apk_autostart_get()}")

    for name, fn in (("imports", imports), ("platform", platform), ("ui", ui), ("libs", libs), ("sqlite", sqlite),
                     ("search", search_index), ("https", https), ("bridge", bridge)):
        check(name, fn)
    print(f"SELFTEST DONE ok={sum(results)} fail={len(results) - sum(results)}", flush=True)

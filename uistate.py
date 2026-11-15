"""Настройки окна программы (вид разделов, тема, цвет, выбор в расписании) — в папке данных, а не только в браузере.

Зачем: окно — страница http://127.0.0.1:<порт>, а порт у каждого запуска свой. Браузер хранит localStorage отдельно для
каждого адреса, поэтому всё, что человек настроил в окне («Вид», тема, фильтры расписания), пропадало при каждом
перезапуске и обновлении программы. Теперь окно отдаёт изменения программе (POST /api/ui), а программа при открытии
окна возвращает их обратно (вписывает в страницу или GET /api/ui) — см. ui/index.html, блок «настройки окна».

Здесь же — сброс к настройкам по умолчанию (кнопки в «Настройки» → «Сброс»): что сбрасывается и что остаётся.
Без сети, сторонних библиотек и Moodle-кода. Файл — ui_state.json в папке данных; в «Перенос данных» не входит
(экраны телефона и компьютера разные, вид у каждого свой).
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import moodle_offline as mo

FILE = "ui_state.json"
KEY_RE = re.compile(r"(?:mo_[a-z0-9_]{1,40}|rs_[a-z0-9_]{1,40}|rs:[^\x00-\x1f]{1,160})")   # только ключи окна и просмотрщика расписания
SKIP = {"mo_t"}                  # ключ доступа окна — никогда не сохраняем
MAX_VALUE = 64 * 1024
MAX_TOTAL = 512 * 1024
MAX_KEYS = 400

# Сброс «всех настроек программы»: эти ключи settings.json остаются. Это не «предпочтения», а то, без чего человек
# потеряет данные или связь: где лежат материалы и куда входить, выбранные курсы и своя группа, подключения к нейросетям
# (их ключи и согласие), а также то, что программа уже записала в саму систему (автозапуск, ночная синхронизация) —
# если стереть только настройку, в системе останется задача, о которой программа «забудет».
RESET_KEEP = ("out_dir", "base_url", "username", "remember", "course_ids", "schedule_group", "schedule_excel",
              "ai", "mcp", "watch", "schedule_on", "schedule_time", "schedule_exe")


def syncable(key) -> bool:
    return isinstance(key, str) and key not in SKIP and bool(KEY_RE.fullmatch(key))


class Store:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / FILE
        self.lock = threading.Lock()
        self.init, self.data = False, {}
        try:
            with mo.fopen(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("data"), dict):
                self.data = {k: v for k, v in d["data"].items() if syncable(k) and isinstance(v, str) and len(v) <= MAX_VALUE}
                self.init = True
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            mo.keep_broken(self.path)     # битый файл не затираем молча — откладываем рядом, начинаем с чистого
            self.init = False

    def get(self) -> dict:
        with self.lock:
            return {"init": self.init, "data": dict(self.data)}

    def update(self, b: dict) -> dict:
        """b: {"set": {ключ: строка}, "del": [ключи], "init": True (окно передало всё, что было в браузере), "reset": True}."""
        if not isinstance(b, dict):
            return {"ok": False, "error": "неверный запрос"}
        with self.lock:
            new = {} if b.get("reset") else dict(self.data)
            for k in (b.get("del") or []) if isinstance(b.get("del"), list) else []:
                new.pop(k, None)
            sets = b.get("set") if isinstance(b.get("set"), dict) else {}
            for k, v in sets.items():
                if syncable(k) and isinstance(v, str) and len(v) <= MAX_VALUE:
                    new[k] = v
            if len(new) > MAX_KEYS or sum(len(k) + len(v) for k, v in new.items()) > MAX_TOTAL:
                return {"ok": False, "error": "слишком много настроек окна"}
            if new == self.data and self.init:
                return {"ok": True}
            self.data, self.init = new, True
            try:
                mo.write_text_atomic(self.path, json.dumps({"v": 1, "saved": int(time.time()), "data": new}, ensure_ascii=False),
                                     durable=True)
            except OSError as e:
                return {"ok": False, "error": mo.describe_error(e)}
        return {"ok": True}

    def reset(self) -> dict:
        return self.update({"reset": True})


def page_script(state: dict) -> bytes:
    """<script> со снимком настроек окна — программа вписывает его в начало страницы (до остальных скриптов)."""
    js = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f"<script>window.__MO_UI={js};</script>".encode("utf-8")


def reset_settings(settings: dict) -> tuple[dict, list[str]]:
    """→ (новые настройки, какие ключи сброшены). Остаётся только RESET_KEEP; остальное программа возьмёт по умолчанию."""
    kept = {k: settings[k] for k in RESET_KEEP if k in settings}
    return kept, sorted(k for k in settings if k not in kept)

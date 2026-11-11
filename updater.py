"""Проверка обновлений самой программы. Только `requests` и стандартная библиотека; без Moodle-кода.

Что делает: раз в сутки (или по кнопке) спрашивает у GitHub, какая версия последняя, и сравнивает со своей.
Ходит ТОЛЬКО на api.github.com (последний релиз репозитория программы). Ничего о вас не отправляет:
ни пароля, ни имени, ни курсов — обычный запрос «какая версия последняя». GitHub видит только IP-адрес.
Сама программа ничего не заменяет и не запускает: даёт ссылку на файл, а скачать и поставить — вам.
Ссылки берутся только с github.com/JINXPIL/Moodle-Offline — любые другие из ответа выбрасываются.
"""
from __future__ import annotations

import re
import time

REPO = "JINXPIL/Moodle-Offline"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"
SAFE_PREFIX = f"https://github.com/{REPO}/"

EVERY_HOURS = (6, 12, 24, 72, 168)         # как часто проверять само, часов
DEFAULT_EVERY = 24
RETRY_AFTER = 3600                          # не вышло (нет сети) — повторить через час, а не ждать сутки
ASSETS = {"exe": "MoodleOffline.exe", "apk": "MoodleOffline.apk"}


class UpdateError(Exception):
    pass


# ── версии ───────────────────────────────────────────────────────────────────
def parse_version(s) -> tuple | None:
    """'v1.12.1' → (1, 12, 1); '1.13.0-beta' → (1, 13, 0); мусор → None."""
    m = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", str(s or ""))
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ── где запущена программа ───────────────────────────────────────────────────
def kind(frozen: bool = False, apk: bool = False, android: bool = False) -> str:
    """apk — приложение для Android; termux — Termux; exe — Windows-программа; source — из исходников."""
    if apk:
        return "apk"
    if android:
        return "termux"
    return "exe" if frozen else "source"


def how_to(k: str, has_asset: bool = True) -> str:
    """Короткая подсказка, как обновиться именно здесь."""
    if k == "apk":
        return ("Нажмите «Скачать» — телефон скачает MoodleOffline.apk. Откройте скачанный файл и согласитесь на обновление: "
                "оно встанет поверх, вход, настройки и материалы останутся.")
    if k == "exe":
        return ("Нажмите «Скачать» — скачается новый MoodleOffline.exe. Закройте программу и замените им старый файл. "
                "Вход, настройки и данные лежат в папке пользователя и сохранятся.")
    if k == "termux":
        return ("В Termux выполните: cd ~/moodle-offline && git pull — и запустите программу заново. "
                "Если вы ставили приложение .apk, обновляйтесь им.")
    return "В папке программы выполните: git pull (или скачайте свежий архив с GitHub) и запустите заново."


# ── настройки ────────────────────────────────────────────────────────────────
def normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        every = int(raw.get("every", DEFAULT_EVERY))
    except (TypeError, ValueError):
        every = DEFAULT_EVERY
    if every not in EVERY_HOURS:
        every = DEFAULT_EVERY
    try:
        last = float(raw.get("last_check") or 0)
    except (TypeError, ValueError):
        last = 0.0
    latest = raw.get("latest") if isinstance(raw.get("latest"), dict) else None
    return {"auto": bool(raw.get("auto", True)), "every": every, "last_check": last,
            "skip": str(raw.get("skip") or ""), "notified": str(raw.get("notified") or ""),
            "latest": latest, "error": str(raw.get("error") or "")}


def due(cfg: dict, now: float | None = None) -> bool:
    """Пора ли проверять само. Выключено — никогда."""
    now = time.time() if now is None else now
    if not cfg.get("auto"):
        return False
    last = float(cfg.get("last_check") or 0)
    if last > now + 60:                       # часы переводили назад — не ждём «в будущее»
        return True
    return now - last >= cfg.get("every", DEFAULT_EVERY) * 3600


# ── запрос к GitHub и разбор ответа ──────────────────────────────────────────
def safe_url(u) -> str:
    u = str(u or "")
    return u if u.startswith(SAFE_PREFIX) else ""


def summarize(release: dict, k: str) -> dict:
    """Из ответа GitHub — только нужное (версия, ссылки, описание)."""
    if not isinstance(release, dict) or not parse_version(release.get("tag_name")):
        raise UpdateError("GitHub вернул непонятный ответ")
    want = ASSETS.get(k)
    asset = {}
    for a in release.get("assets") or []:
        if isinstance(a, dict) and want and str(a.get("name", "")).lower() == want.lower():
            asset = a
            break
    url = safe_url(asset.get("browser_download_url"))
    notes = str(release.get("body") or "").strip()
    return {"tag": str(release.get("tag_name")), "version": ".".join(map(str, parse_version(release["tag_name"]))),
            "page": safe_url(release.get("html_url")) or PAGE_URL,
            "asset_name": asset.get("name", "") if url else "", "asset_url": url,
            "size": int(asset.get("size") or 0) if url else 0,
            "published": str(release.get("published_at") or "")[:10],
            "notes": notes[:700] + ("…" if len(notes) > 700 else "")}


def fetch_latest(current: str, use_system_proxy: bool = False, url: str | None = None, timeout: int = 15) -> dict:
    """Один запрос «последний релиз». → ответ GitHub (dict). Ошибки — UpdateError с понятным текстом."""
    import requests
    url = url or API_URL                      # адрес подменяют только тесты
    s = requests.Session()
    s.trust_env = bool(use_system_proxy)
    try:
        r = s.get(url, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": f"MoodleOffline/{current}"})
    except requests.RequestException:
        raise UpdateError("нет связи с GitHub (проверьте интернет)") from None
    finally:
        s.close()
    if r.status_code == 404:
        raise UpdateError("на GitHub пока нет ни одного выпущенного релиза")
    if r.status_code in (403, 429):
        raise UpdateError("GitHub временно не отвечает (слишком много запросов) — попробуйте позже")
    if r.status_code != 200:
        raise UpdateError(f"GitHub ответил кодом {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise UpdateError("GitHub вернул непонятный ответ") from None


def run_check(cfg: dict, current: str, k: str, now: float | None = None, fetch=None) -> dict:
    """Проверка: обновляет `cfg` (когда проверяли, что нашли или какая ошибка). → тот же `cfg`.
    `fetch(current)` можно подменить в тестах."""
    now = time.time() if now is None else now
    try:
        rel = (fetch or fetch_latest)(current)
        cfg["latest"] = summarize(rel, k)
        cfg["error"] = ""
        cfg["last_check"] = now
    except UpdateError as e:
        cfg["error"] = str(e)
        # нет связи — повторим через час, а не через сутки
        cfg["last_check"] = now - max(0, cfg.get("every", DEFAULT_EVERY) * 3600 - RETRY_AFTER)
    return cfg


def status(cfg: dict, current: str, k: str) -> dict:
    """Всё, что нужно окну: версия, что найдено, новее ли, как обновиться."""
    lt = cfg.get("latest") or None
    newer = bool(lt and is_newer(lt.get("version"), current))
    return {"current": current, "kind": k, "auto": bool(cfg.get("auto")), "every": cfg.get("every", DEFAULT_EVERY),
            "every_options": list(EVERY_HOURS), "last_check": cfg.get("last_check", 0) or 0,
            "error": cfg.get("error", ""), "latest": lt, "newer": newer,
            "skipped": bool(newer and lt and cfg.get("skip") == lt.get("tag")),
            "how": how_to(k, bool(lt and lt.get("asset_url")))}


def should_notify(cfg: dict, st: dict) -> bool:
    """Сообщить о новой версии один раз на каждую версию; «пропущенную» — не сообщать."""
    lt = st.get("latest") or {}
    return bool(st.get("newer") and not st.get("skipped") and lt.get("tag") and cfg.get("notified") != lt.get("tag"))


def notice_text(st: dict) -> str:
    lt = st.get("latest") or {}
    return f"Вышла новая версия {lt.get('version', '')} (у вас {st.get('current', '')}). Откройте «Настройки» → «Обновление программы»."

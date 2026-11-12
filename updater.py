"""Проверка обновлений самой программы. Только `requests` и стандартная библиотека; без Moodle-кода.

Что делает: раз в сутки (или по кнопке) спрашивает у GitHub, какая версия последняя, и сравнивает со своей.
Ходит ТОЛЬКО на api.github.com (последний релиз репозитория программы; через системный прокси, если он есть). Ничего о вас не отправляет:
ни пароля, ни имени, ни курсов — обычный запрос «какая версия последняя». GitHub видит только IP-адрес.
Windows-программа (.exe) может обновить себя сама, но только по нажатию кнопки «Обновить сейчас»: скачивает свежий файл с
github.com/JINXPIL/Moodle-Offline, сверяет контрольную сумму SHA-256 из того же релиза, подменяет им себя и перезапускается.
В остальных случаях (apk, Termux, исходники) даёт ссылку на файл, а скачать и поставить — вам.
Ссылки берутся только с github.com/JINXPIL/Moodle-Offline — любые другие из ответа выбрасываются.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time

REPO = "JINXPIL/Moodle-Offline"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"
SAFE_PREFIX = f"https://github.com/{REPO}/"

EVERY_HOURS = (6, 12, 24, 72, 168)         # как часто проверять само, часов
DEFAULT_EVERY = 24
RETRY_AFTER = 3600                          # не вышло (нет сети) — повторить через час, а не ждать сутки
ASSETS = {"exe": "MoodleOffline.exe", "apk": "MoodleOffline.apk"}
DL_HOSTS = ("github.com", "githubusercontent.com")   # куда может вести перенаправление при скачивании файла релиза
MAX_DL = 300 * 1024 * 1024                          # файл больше — это не наша программа


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
        return ("Нажмите «Обновить сейчас»: программа сама скачает новую версию, проверит файл и перезапустится. "
                "Вход, настройки и данные лежат в папке пользователя и сохранятся. "
                "Если делаете вручную («Скачать файл»): запускайте именно новый файл (в имени может быть «(1)»), "
                "старую копию программы закрывать не обязательно — новая сама попросит её закрыться.")
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
    sha_url = ""
    for a in release.get("assets") or []:       # контрольная сумма лежит рядом: «MoodleOffline.exe.sha256»
        if isinstance(a, dict) and want and str(a.get("name", "")).lower() == want.lower() + ".sha256":
            sha_url = safe_url(a.get("browser_download_url"))
            break
    notes = str(release.get("body") or "").strip()
    return {"tag": str(release.get("tag_name")), "version": ".".join(map(str, parse_version(release["tag_name"]))),
            "page": safe_url(release.get("html_url")) or PAGE_URL,
            "asset_name": asset.get("name", "") if url else "", "asset_url": url, "sha_url": sha_url if url else "",
            "size": int(asset.get("size") or 0) if url else 0,
            "published": str(release.get("published_at") or "")[:10],
            "notes": notes[:700] + ("…" if len(notes) > 700 else "")}


def _reason(e: Exception) -> str:
    """Короткая причина сбоя связи простыми словами — чтобы было видно, что чинить."""
    import requests
    if isinstance(e, requests.exceptions.SSLError):
        return "ошибка защищённого соединения (проверьте дату и время на компьютере, антивирус, прокси)"
    if isinstance(e, requests.exceptions.ProxyError):
        return "не работает прокси"
    if isinstance(e, requests.exceptions.ConnectTimeout) or isinstance(e, requests.exceptions.ReadTimeout):
        return "GitHub не ответил вовремя (возможно, он у вас не открывается без VPN/прокси)"
    if isinstance(e, requests.exceptions.ConnectionError):
        t = str(e).lower()
        if "name or service" in t or "getaddrinfo" in t or "resolve" in t:
            return "не нашёл адрес api.github.com (DNS или нет интернета)"
        return "не удалось соединиться с api.github.com (нет интернета или он заблокирован)"
    return type(e).__name__


def fetch_latest(current: str, use_system_proxy: bool = True, url: str | None = None, timeout: int = 15) -> dict:
    """Один запрос «последний релиз». → ответ GitHub (dict). Ошибки — UpdateError с понятным текстом.
    GitHub — не сайт вуза, поэтому системный прокси/VPN используем (на Windows берётся из настроек системы);
    если через прокси не вышло — один раз пробуем напрямую."""
    import requests
    url = url or API_URL                      # адрес подменяют только тесты
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": f"MoodleOffline/{current}"}
    r, err = None, None
    for env in ((True, False) if use_system_proxy else (False,)):
        s = requests.Session()
        s.trust_env = env
        try:
            r = s.get(url, timeout=timeout, headers=hdr)
            break
        except requests.RequestException as e:
            err = e
        finally:
            s.close()
    if r is None:
        raise UpdateError("нет связи с GitHub: " + _reason(err)) from None
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


# ── обновление .exe прямо из программы ───────────────────────────────────────
def host_ok(url) -> bool:
    """Скачивать можно только с GitHub (и его хранилища файлов релизов), только по https."""
    from urllib.parse import urlparse
    u = urlparse(str(url or ""))
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == d or h.endswith("." + d) for d in DL_HOSTS)


def parse_sha256(text) -> str:
    """'abc…  MoodleOffline.exe' → 'abc…' (64 шестнадцатеричных знака) или ''."""
    m = re.match(r"\s*([0-9a-fA-F]{64})(?:\s|$)", str(text or ""))
    return m.group(1).lower() if m else ""


def part_path(exe) -> str:
    return str(exe) + ".part"


def new_path(exe) -> str:
    return str(exe) + ".new"


def old_path(exe) -> str:
    return str(exe) + ".old"


def can_write_next_to(exe) -> bool:
    """Можно ли класть файлы рядом с программой (в «Program Files» обычно нельзя — тогда обновляем вручную)."""
    d = os.path.dirname(os.path.abspath(str(exe))) or "."
    probe = os.path.join(d, f".mo_write_test_{os.getpid()}")
    try:
        with open(probe, "wb") as f:
            f.write(b"x")
        os.remove(probe)
        return True
    except OSError:
        return False


def _get(url: str, use_system_proxy: bool, timeout: int, stream: bool, current: str):
    """GET с той же логикой прокси, что у проверки версии. → ответ (его закрывает вызывающий)."""
    import requests
    hdr = {"User-Agent": f"MoodleOffline/{current}", "Accept": "application/octet-stream"}
    err = None
    for env in ((True, False) if use_system_proxy else (False,)):
        s = requests.Session()
        s.trust_env = env
        try:
            r = s.get(url, timeout=timeout, headers=hdr, stream=stream)
            r._mo_session = s
            return r
        except requests.RequestException as e:
            err = e
            s.close()
    raise UpdateError("нет связи с GitHub: " + _reason(err)) from None


def fetch_expected_sha(sha_url: str, current: str = "", use_system_proxy: bool = True) -> str:
    if not sha_url or not safe_url(sha_url):
        raise UpdateError("в релизе нет контрольной суммы — обновите вручную")
    r = _get(sha_url, use_system_proxy, 20, False, current)
    try:
        if not all(host_ok(x.url) for x in list(r.history) + [r]) or r.status_code != 200:
            raise UpdateError("не удалось получить контрольную сумму файла")
        sha = parse_sha256(r.text[:400])
    finally:
        r._mo_session.close()
    if not sha:
        raise UpdateError("контрольная сумма в релизе записана непонятно — обновите вручную")
    return sha


def download_verified(url: str, dest, expected_sha: str, size_hint: int = 0, progress=None, stop=None,
                      current: str = "", use_system_proxy: bool = True) -> str:
    """Скачать файл релиза в dest, сверить SHA-256 и размер. → путь. Любая проблема — UpdateError, недокачанное удаляется.
    progress(done, total) вызывается по ходу; stop() → True прерывает."""
    if not safe_url(url):
        raise UpdateError("адрес файла не с GitHub программы — отказываюсь качать")
    dest = str(dest)
    part = dest + ".part"
    r = _get(url, use_system_proxy, 30, True, current)
    try:
        if not all(host_ok(x.url) for x in list(r.history) + [r]):
            raise UpdateError("GitHub перенаправил на неожиданный адрес — отказываюсь качать")
        if r.status_code != 200:
            raise UpdateError(f"GitHub ответил кодом {r.status_code}")
        total = int(r.headers.get("Content-Length") or size_hint or 0)
        if total > MAX_DL or (size_hint and total and abs(total - size_hint) > max(1024, size_hint // 20)):
            raise UpdateError("размер файла не совпадает с описанием релиза")
        h = hashlib.sha256()
        done = 0
        try:
            with open(part, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if stop and stop():
                        raise UpdateError("скачивание остановлено")
                    if not chunk:
                        continue
                    done += len(chunk)
                    if done > MAX_DL:
                        raise UpdateError("файл слишком большой")
                    f.write(chunk)
                    h.update(chunk)
                    if progress:
                        progress(done, total)
        except OSError as e:
            raise UpdateError(f"не удалось записать файл: {e.strerror or e}") from None
        except Exception as e:
            if isinstance(e, UpdateError):
                raise
            import requests
            if isinstance(e, requests.RequestException):
                raise UpdateError("связь оборвалась при скачивании — попробуйте ещё раз") from None
            raise
        if total and done != total:
            raise UpdateError("файл скачался не полностью — попробуйте ещё раз")
        if h.hexdigest() != expected_sha.lower():
            raise UpdateError("контрольная сумма скачанного файла не совпала — файл не используем")
        with open(part, "rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateError("скачанный файл не похож на программу Windows")
        os.replace(part, dest)
        return dest
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    finally:
        r.close()
        r._mo_session.close()


def swap_exe(exe, new) -> None:
    """Подменить работающую программу новым файлом. Windows не даёт перезаписать запущенный .exe, но даёт его переименовать:
    прежний уходит в «….exe.old», новый встаёт на его место. Не вышло — всё возвращается как было."""
    exe, new = str(exe), str(new)
    old = old_path(exe)
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        raise UpdateError("рядом с программой остался занятый файл .old — закройте все копии программы и повторите") from None
    try:
        os.replace(exe, old)
    except OSError as e:
        raise UpdateError(f"не удалось переименовать программу: {e.strerror or e}") from None
    try:
        os.replace(new, exe)
    except OSError as e:
        try:
            os.replace(old, exe)           # возвращаем прежнюю
        except OSError:
            pass
        raise UpdateError(f"не удалось поставить новый файл: {e.strerror or e}") from None


def launch(exe) -> None:
    """Запустить программу заново как отдельный процесс. У собранного PyInstaller'ом exe нужно сбросить окружение
    (PYINSTALLER_RESET_ENVIRONMENT), иначе новая копия возьмёт временную папку этой, которая исчезнет при её закрытии."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL"):
        env.pop(k, None)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    try:
        subprocess.Popen([str(exe)], env=env, close_fds=True, creationflags=flags, cwd=os.path.dirname(os.path.abspath(str(exe))) or None,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        raise UpdateError(f"новая версия установлена, но не запустилась: {e.strerror or e}. Откройте программу сами.") from None


def cleanup(exe) -> bool:
    """Убрать остатки обновления рядом с программой (прежняя копия, недокачанное). Занятое пока не трогаем.
    → True, если ничего не осталось."""
    left = False
    for p in (old_path(exe), new_path(exe), part_path(exe), str(exe) + ".new.part"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            left = True
    return not left


def cleanup_later(exe, tries: int = 40, pause: float = 2.0, sleep=time.sleep):
    """То же в фоне с повторами. Сразу после обновления прежняя копия ещё несколько секунд завершается, и Windows не отдаёт
    её файл «….exe.old» — одной попытки при запуске мало, файл оставался рядом с программой до следующего запуска.
    Пробуем каждые `pause` секунд, пока не уберём (по умолчанию до ~80 с). → поток (для тестов)."""
    def run():
        for _ in range(tries):
            try:
                if cleanup(exe):
                    return
            except Exception:                  # noqa: BLE001 — уборка не должна ничего ронять
                return
            sleep(pause)
    th = threading.Thread(target=run, daemon=True, name="update-cleanup")
    th.start()
    return th


def should_notify(cfg: dict, st: dict) -> bool:
    """Сообщить о новой версии один раз на каждую версию; «пропущенную» — не сообщать."""
    lt = st.get("latest") or {}
    return bool(st.get("newer") and not st.get("skipped") and lt.get("tag") and cfg.get("notified") != lt.get("tag"))


def notice_text(st: dict) -> str:
    lt = st.get("latest") or {}
    return f"Вышла новая версия {lt.get('version', '')} (у вас {st.get('current', '')}). Откройте «Настройки» → «Обновление программы»."

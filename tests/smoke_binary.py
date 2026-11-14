"""Проверка собранной программы (Linux, macOS) на сервере сборки: запускается ли она, отдаёт ли окно и интерфейс, правильно ли
определяет систему и вид сборки, где держит данные, закрывается ли по просьбе. Не «test_*.py» и в run_all.py не входит — ей нужен
готовый файл. Настоящих сайтов и входа не трогает.

    python tests/smoke_binary.py --system linux --kind linux --data-rel .local/share/moodle-offline -- ./dist/MoodleOffline
    python tests/smoke_binary.py --system macos --kind macos --data-rel "Library/Application Support/MoodleOffline" \\
        -- dist/MoodleOffline.app/Contents/MacOS/MoodleOffline
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []


def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""), flush=True)
    if not c:
        FAILS.append(n)


SETTINGS = {"no_geo_check": True, "update": {"auto": False}, "watch": {"check": {"enabled": False}}}


def get(port, token, path, timeout=20):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}{'&' if '?' in path else '?'}t={token}", timeout=timeout) as r:
        return r.status, r.read()


def post(port, token, path, body=None, timeout=20):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}?t={token}", data=json.dumps(body or {}).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def run_app(cmd, env, data_dir, wait=120):
    """Запустить программу без окна. → (процесс, port, token) или (процесс, None, None), если за wait секунд не поднялась."""
    (data_dir).mkdir(parents=True, exist_ok=True)
    (data_dir / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    p = subprocess.Popen(cmd + ["--no-window"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    gj = data_dir / "gui.json"
    end = time.time() + wait
    while time.time() < end:
        if p.poll() is not None:
            return p, None, None
        if gj.is_file():
            try:
                d = json.loads(gj.read_text(encoding="utf-8"))
                return p, d["port"], d["token"]
            except (ValueError, KeyError):
                pass
        time.sleep(0.5)
    return p, None, None


def stop_app(p, port, token):
    try:
        post(port, token, "/api/quit")
    except Exception:
        pass
    try:
        return p.wait(timeout=40)
    except subprocess.TimeoutExpired:
        p.kill()
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["linux", "macos"])
    ap.add_argument("--kind", required=True)
    ap.add_argument("--data-rel", required=True, help="где в домашней папке должны лежать данные")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = [c for c in a.cmd if c != "--"]
    if not cmd:
        ap.error("после -- нужна команда запуска программы")
    version = re.search(r'^VERSION = "([^"]+)"', (ROOT / "moodle_offline.py").read_text(encoding="utf-8"), re.M).group(1)
    tmp = Path(tempfile.mkdtemp(prefix="mo_smoke_"))
    home = tmp / "home"
    home.mkdir()
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("MOODLE_") and k not in ("XDG_DATA_HOME", "XDG_CONFIG_HOME")}
    base_env.update(HOME=str(home), PYTHONUNBUFFERED="1")

    # 1. консольные ключи собранной программы
    r = subprocess.run(cmd + ["--help"], env=base_env, capture_output=True, text=True, timeout=120)
    check("--help: собранная программа понимает ключи консольной версии", r.returncode == 0 and "usage" in (r.stdout + r.stderr).lower(), (r.stdout + r.stderr)[-300:])

    # 2. запуск без окна, данные в своей папке
    data = tmp / "данные"
    env = dict(base_env, MOODLE_OFFLINE_DATA=str(data))
    t0 = time.time()
    p, port, token = run_app(cmd, env, data)
    print(f"INFO программа поднялась за {time.time() - t0:.1f} с", flush=True)
    out = ""
    if port is None:
        try:
            p.kill()
            out = (p.communicate(timeout=10)[0] or "")[-1500:]
        except Exception:
            pass
    check("программа поднялась и записала gui.json", port is not None, out)
    if port is not None:
        st, body = get(port, token, "/api/ping")
        j = json.loads(body)
        check("ping: версия из файла сборки", st == 200 and j.get("ok") and j.get("version") == version, str(j))
        st, body = get(port, token, "/")
        check("окно отдаётся: интерфейс вложен в сборку", st == 200 and b"Moodle Offline" in body and len(body) > 100_000, f"{st} {len(body)}")
        st, body = get(port, token, "/api/state")
        s = json.loads(body)
        check("состояние: система определена верно", s.get("system") == a.system, str(s.get("system")))
        st, body = get(port, token, "/api/update")
        u = json.loads(body)
        check("обновление: сборка определена как «%s»" % a.kind, u.get("kind") == a.kind, str(u.get("kind")))
        check("обновление: сам себя заменяет только Linux; macOS — вручную", ("can_install" in u) and (u.get("can_install") is False or a.kind == "linux"))
        code = stop_app(p, port, token)
        check("по /api/quit закрывается сам, код 0", code == 0, str(code))
        check("gui.json убран за собой", not (data / "gui.json").exists())

    # 3. данные по умолчанию: в домашней папке пользователя (как у настоящего человека)
    default = home / a.data_rel
    p, port, token = run_app(cmd, base_env, default)
    check("без своей папки данные идут в «%s»" % a.data_rel, port is not None and (default / "gui.json").is_file(),
          (p.communicate(timeout=10)[0] or "")[-800:] if port is None and p.poll() is not None else "")
    if port is not None:
        code = stop_app(p, port, token)
        check("и тут закрывается по просьбе", code == 0, str(code))
        check("ни секретов, ни паролей в папке данных нет", not any(f.suffix in (".bin",) for f in default.rglob("*") if f.is_file()))

    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILS:", FAILS, flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

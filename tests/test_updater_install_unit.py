"""Обновление .exe из программы: безопасность адресов, скачивание с проверкой, подмена запущенного файла (на локальном «GitHub»)."""
import sys, os, hashlib, threading, time, subprocess, tempfile, shutil, stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import _root
import updater as u

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

# ── адреса ──
check("host_ok: github.com", u.host_ok("https://github.com/JINXPIL/Moodle-Offline/releases/download/v1/x.exe"))
check("host_ok: objects.githubusercontent.com", u.host_ok("https://objects.githubusercontent.com/abc"))
for bad in ("http://github.com/x", "https://github.com.evil.com/x", "https://evil.com/github.com", "https://notgithub.com/x", "ftp://github.com/x", "", None, "https://githubusercontent.com.ru/x"):
    check(f"host_ok отказывает: {bad!r}", not u.host_ok(bad))
check("safe_url чужого репозитория пуст", u.safe_url("https://github.com/evil/Moodle-Offline/releases/x.exe") == "")
check("parse_sha256 из файла релиза", u.parse_sha256("a"*64 + "  MoodleOffline.exe\n") == "a"*64)
check("parse_sha256 мусор", u.parse_sha256("zzz") == "" and u.parse_sha256("a"*63) == "" and u.parse_sha256("") == "")

# ── summarize: sha_url ──
rel = {"tag_name": "v1.2.3", "html_url": "https://github.com/JINXPIL/Moodle-Offline/releases/tag/v1.2.3", "assets": [
    {"name": "MoodleOffline.exe", "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.2.3/MoodleOffline.exe", "size": 100},
    {"name": "MoodleOffline.exe.sha256", "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.2.3/MoodleOffline.exe.sha256", "size": 90},
    {"name": "MoodleOffline.apk", "browser_download_url": "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.2.3/MoodleOffline.apk", "size": 5}]}
sm = u.summarize(rel, "exe")
check("summarize: файл и сумма для exe", sm["asset_url"].endswith("MoodleOffline.exe") and sm["sha_url"].endswith("MoodleOffline.exe.sha256"), str(sm))
check("summarize: для apk sha_url пуст", u.summarize(rel, "apk")["sha_url"] == "")
rel2 = dict(rel, assets=[rel["assets"][0]])
check("summarize: без .sha256 — sha_url пуст", u.summarize(rel2, "exe")["sha_url"] == "")

# ── локальный «GitHub» ──
GOOD = b"MZ" + os.urandom(1_500_000)
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/good.exe": body = GOOD
        elif self.path == "/good.sha": body = (GOOD_SHA + "  MoodleOffline.exe\n").encode()
        elif self.path == "/bad.exe": body = b"MZ" + os.urandom(1_500_000)            # сумма не совпадёт
        elif self.path == "/notexe.bin": body = b"PK" + b"x" * 1000
        elif self.path == "/short.exe": body = GOOD
        else: self.send_error(404); return
        self.send_response(200); self.send_header("Content-Length", str(len(body) + (500 if self.path == "/short.exe" else 0)))
        self.end_headers()
        try:
            for i in range(0, len(body), 50_000):
                self.wfile.write(body[i:i + 50_000]); time.sleep(0.01)
        except Exception: pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{srv.server_address[1]}"
# для проверки логики потока адреса «github» подменяем на локальные (сами правила адресов проверены выше)
u.safe_url = lambda x: x if str(x).startswith(B) else ""
u.host_ok = lambda x: str(x).startswith(B)

d = Path(tempfile.mkdtemp(prefix="upd_"))
seen = []
p = u.download_verified(B + "/good.exe", d / "a.new", GOOD_SHA, len(GOOD), progress=lambda a, t: seen.append((a, t)), use_system_proxy=False)
check("скачивание: файл на месте и совпадает", Path(p).read_bytes() == GOOD)
check("скачивание: ход виден и растёт", len(seen) >= 3 and seen[-1][0] == len(GOOD) and all(seen[i][0] <= seen[i + 1][0] for i in range(len(seen) - 1)), str(seen[-2:]))
check("скачивание: .part не остался", not (d / "a.new.part").exists())

def fails(url, sha, name, size=0):
    try:
        u.download_verified(url, d / name, sha, size, use_system_proxy=False)
    except u.UpdateError as e:
        return str(e)
    return None
e = fails(B + "/bad.exe", GOOD_SHA, "b.new")
check("не та сумма — отказ и ничего не осталось", e and "контрольная сумма" in e and not (d / "b.new").exists() and not (d / "b.new.part").exists(), str(e))
e = fails(B + "/notexe.bin", hashlib.sha256(b"PK" + b"x" * 1000).hexdigest(), "c.new")
check("не программа Windows — отказ", e and "не похож" in e and not (d / "c.new").exists(), str(e))
e = fails(B + "/short.exe", GOOD_SHA, "s.new")
check("оборванная загрузка — отказ", e is not None and not (d / "s.new").exists(), str(e))
e = fails(B + "/nope.exe", GOOD_SHA, "n.new")
check("404 — отказ с понятным текстом", e and "404" in e, str(e))
e = fails(B + "/good.exe", GOOD_SHA, "z.new", size=len(GOOD) * 3)
check("размер не как в описании релиза — отказ", e and "размер" in e, str(e))
e = fails("https://evil.example/x.exe", GOOD_SHA, "w.new")
check("чужой адрес — отказ до запроса", e and "не с GitHub" in e, str(e))
stop_after = {"n": 0}
def stop():
    stop_after["n"] += 1
    return stop_after["n"] > 2
try:
    u.download_verified(B + "/good.exe", d / "st.new", GOOD_SHA, len(GOOD), stop=stop, use_system_proxy=False); r = None
except u.UpdateError as ex:
    r = str(ex)
check("остановка по просьбе", r and "остановлено" in r and not (d / "st.new.part").exists(), str(r))
sha = u.fetch_expected_sha(B + "/good.sha", "t", False)
check("fetch_expected_sha читает сумму", sha == GOOD_SHA, sha)
try:
    u.fetch_expected_sha("", "t", False); r = None
except u.UpdateError as ex: r = str(ex)
check("нет sha_url — отказ", r is not None)

# ── подмена работающего файла ──
exe = d / "prog"; new = d / "prog.new"
exe.write_text("#!/bin/sh\necho OLD > %s/ran.txt\nexec sleep 30\n" % d); exe.chmod(0o755)
new.write_text("#!/bin/sh\necho NEW > %s/ran.txt\n" % d); new.chmod(0o755)
proc = subprocess.Popen([str(exe)]); time.sleep(0.5)       # «старая» копия запущена и держит файл
check("до подмены работает старая", (d / "ran.txt").read_text().strip() == "OLD")
u.swap_exe(exe, new)
check("после подмены на месте новый файл, прежний — .old", "NEW" in exe.read_text() and "OLD" in Path(u.old_path(exe)).read_text() and not new.exists())
u.launch(exe); time.sleep(0.8)
check("launch запускает новый файл", (d / "ran.txt").read_text().strip() == "NEW")
proc.kill(); proc.wait()
u.cleanup(exe)
check("cleanup убирает .old", not Path(u.old_path(exe)).exists() and exe.exists())
# откат
exe2 = d / "p2"; exe2.write_text("A"); 
try:
    u.swap_exe(exe2, d / "нет_такого"); r = None
except u.UpdateError as ex: r = str(ex)
check("нет нового файла — откат, прежний цел", r is not None and exe2.read_text() == "A" and not Path(u.old_path(exe2)).exists(), str(r))
# запуск несуществующего
try:
    u.launch(d / "нет"); r = None
except u.UpdateError as ex: r = str(ex)
check("launch несуществующего — понятная ошибка", r and "не запустилась" in r, str(r))
check("can_write_next_to: можно", u.can_write_next_to(d / "x"))
check("can_write_next_to: нельзя", not u.can_write_next_to("/proc/nonexistent/x"))
shutil.rmtree(d, ignore_errors=True)
print("FAILS:", FAILS)
sys.exit(1 if FAILS else 0)

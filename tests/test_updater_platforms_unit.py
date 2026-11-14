"""Обновление программы на Linux и macOS: виды сборок, имена файлов релиза по архитектуре, проверка «это программа Linux» (ELF),
права на запуск после подмены, подсказки. Локальный «GitHub» — как в test_updater_install_unit.py."""
import hashlib, os, stat, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import _root
import updater as u

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

# ── виды сборок ──
check("kind: Windows-программа — exe, как раньше", u.kind(frozen=True, system="windows") == "exe")
check("kind: собранная программа Linux / macOS", u.kind(frozen=True, system="linux") == "linux" and u.kind(frozen=True, system="macos") == "macos")
check("kind: из исходников и на Android не зависит от системы", u.kind(system="linux") == "source" and u.kind(system="macos") == "source"
      and u.kind(apk=True, system="linux") == "apk" and u.kind(android=True, system="linux") == "termux")
check("kind без system берёт текущую систему и не падает", u.kind(frozen=True) in ("exe", "linux", "macos"))
check("сам обновить можно exe и Linux-файл, macOS — вручную", u.CAN_SWAP == ("exe", "linux") and "macos" not in u.CAN_SWAP)

# ── имена файлов релиза ──
check("файл Linux по архитектуре", u.asset_for("linux", "x86_64") == "MoodleOffline-linux-x86_64" and u.asset_for("linux", "AMD64") == "MoodleOffline-linux-x86_64"
      and u.asset_for("linux", "aarch64") == "MoodleOffline-linux-arm64")
check("файл macOS по архитектуре (архив .zip)", u.asset_for("macos", "arm64") == "MoodleOffline-macos-arm64.zip" and u.asset_for("macos", "x86_64") == "MoodleOffline-macos-x86_64.zip")
check("файлы Windows и Android — прежние", u.asset_for("exe") == "MoodleOffline.exe" and u.asset_for("apk") == "MoodleOffline.apk" and u.asset_for("source") == "")

BASE = "https://github.com/JINXPIL/Moodle-Offline/releases/download/v1.2.3/"
def asset(n, size=10): return {"name": n, "browser_download_url": BASE + n, "size": size}
rel = {"tag_name": "v1.2.3", "html_url": "https://github.com/JINXPIL/Moodle-Offline/releases/tag/v1.2.3", "assets": [
    asset("MoodleOffline.exe"), asset("MoodleOffline.exe.sha256"), asset("MoodleOffline.apk"),
    asset("MoodleOffline-linux-x86_64", 777), asset("MoodleOffline-linux-x86_64.sha256"),
    asset("MoodleOffline-macos-arm64.zip", 888), asset("MoodleOffline-macos-arm64.zip.sha256"),
    asset("MoodleOffline-macos-x86_64.zip", 999), asset("MoodleOffline-macos-x86_64.zip.sha256")]}
sm = u.summarize(rel, "linux", "x86_64")
check("summarize Linux: нужный файл и его сумма", sm["asset_name"] == "MoodleOffline-linux-x86_64" and sm["sha_url"].endswith("MoodleOffline-linux-x86_64.sha256") and sm["size"] == 777, str(sm))
sm = u.summarize(rel, "macos", "arm64")
check("summarize macOS arm64: свой архив, не Intel", sm["asset_name"] == "MoodleOffline-macos-arm64.zip" and sm["size"] == 888 and sm["sha_url"].endswith("arm64.zip.sha256"), str(sm))
check("summarize macOS Intel: свой архив", u.summarize(rel, "macos", "x86_64")["asset_name"] == "MoodleOffline-macos-x86_64.zip")
check("нет файла под эту архитектуру — ссылки пустые, страница релиза есть", u.summarize(rel, "linux", "aarch64")["asset_url"] == "" and u.summarize(rel, "linux", "aarch64")["page"].endswith("v1.2.3"))
check("exe по-прежнему берёт exe, а не Linux-файл", u.summarize(rel, "exe")["asset_name"] == "MoodleOffline.exe")
st = u.status({"latest": u.summarize(rel, "linux", "x86_64"), "auto": True}, "1.0.0", "linux")
check("status: подсказка для Linux есть и говорит про кнопку", st["newer"] and "Обновить сейчас" in st["how"], st["how"])
st = u.status({"latest": u.summarize(rel, "macos", "arm64"), "auto": True}, "1.0.0", "macos")
check("status: подсказка для macOS — вручную, с «Программы» и разрешением запуска", "Программы" in st["how"] and "Обновить сейчас" not in st["how"], st["how"])
check("подсказки не обещают Windows на других системах", "Windows" not in u.how_to("linux") and "Windows" not in u.how_to("macos"))

# ── скачивание с проверкой «это ELF» ──
ELF = b"\x7fELF" + os.urandom(1_200_000)
ELF_SHA = hashlib.sha256(ELF).hexdigest()
EXE = b"MZ" + os.urandom(100_000)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = {"/linux.bin": ELF, "/win.exe": EXE}.get(self.path)
        if body is None: self.send_error(404); return
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{srv.server_address[1]}"
u.safe_url = lambda x: x if str(x).startswith(B) else ""
u.host_ok = lambda x: str(x).startswith(B)
d = Path(tempfile.mkdtemp(prefix="upd_pl_"))

p = u.download_verified(B + "/linux.bin", d / "m.new", ELF_SHA, len(ELF), magic=u.MAGIC["linux"], use_system_proxy=False)
check("Linux: файл с ELF-началом принят", Path(p).read_bytes() == ELF)
try:
    u.download_verified(B + "/win.exe", d / "w.new", hashlib.sha256(EXE).hexdigest(), len(EXE), magic=u.MAGIC["linux"], use_system_proxy=False); e = None
except u.UpdateError as ex:
    e = str(ex)
check("Linux: чужая программа (Windows) с верной суммой отвергнута", e and "не похож" in e and not (d / "w.new").exists() and not (d / "w.new.part").exists(), str(e))
check("для Windows проверка прежняя — «MZ»", u.MAGIC["exe"] == b"MZ" and u.MAGIC["linux"] == b"\x7fELF")
try:
    u.download_verified(B + "/linux.bin", d / "x.new", ELF_SHA, len(ELF), use_system_proxy=False); e = None
except u.UpdateError as ex:
    e = str(ex)
check("без magic → MZ: ELF не проходит как Windows-программа", e and "не похож" in e, str(e))

# ── подмена с правом на запуск ──
if os.name != "nt":
    exe = d / "MoodleOffline"; exe.write_bytes(b"\x7fELFold"); exe.chmod(0o755)
    new = d / "MoodleOffline.new"; new.write_bytes(ELF); new.chmod(0o600)          # скачанный файл — без права на запуск
    u.swap_exe(exe, new)
    mode = stat.S_IMODE(exe.stat().st_mode)
    check("Linux: новый файл на месте, исполняемый", exe.read_bytes() == ELF and mode & 0o100 and mode & 0o010 and mode & 0o001, oct(mode))
    check("Linux: прежний ушёл в .old, остатков .new нет", (d / "MoodleOffline.old").read_bytes() == b"\x7fELFold" and not new.exists())
    u.cleanup(exe); check("уборка убирает .old", not (d / "MoodleOffline.old").exists())
    # у запущенной программы переименование разрешено и на Linux: открытый файл живёт, пока открыт
    exe.write_bytes(b"A" * 10); exe.chmod(0o755); new.write_bytes(ELF)
    with open(exe, "rb") as running:
        u.swap_exe(exe, new)
        check("открытый (запущенный) файл подменяется, старый читается до конца", running.read() == b"A" * 10 and exe.read_bytes() == ELF)

print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

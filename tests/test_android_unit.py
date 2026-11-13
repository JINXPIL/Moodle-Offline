import os, sys, stat, shutil, tempfile, subprocess, time
from pathlib import Path
import _root
import android, datadir as dd

FAILS = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    if not cond: FAILS.append(name)

TMP = Path(tempfile.mkdtemp(prefix="and_unit_"))

# ── определение платформы ──
check("TERMUX_VERSION → Android", android.is_android({"TERMUX_VERSION": "0.118"}, "linux"))
check("PREFIX из com.termux → Android", android.is_android({"PREFIX": "/data/data/com.termux/files/usr"}, "linux"))
check("ANDROID_ROOT+ANDROID_DATA → Android", android.is_android({"ANDROID_ROOT": "/system", "ANDROID_DATA": "/data"}, "linux"))
check("sys.platform == android → Android", android.is_android({}, "android"))
check("обычный Linux → не Android", not android.is_android({"HOME": "/home/u", "PATH": "/usr/bin"}, "linux"))
check("только ANDROID_ROOT без DATA → не Android", not android.is_android({"ANDROID_ROOT": "/system"}, "linux"))
check("Windows → не Android", not android.is_android({"LOCALAPPDATA": "C:\\x"}, "win32"))

# ── папки ──
home = TMP / "home"; home.mkdir()
check("нет ~/storage/shared → None", android.shared_storage(home) is None)
check("материалы по умолчанию без общей памяти — в домашней", android.default_materials(home, "Материалы Moodle") == home / "Материалы Moodle")
(home / "storage" / "shared").mkdir(parents=True)
check("есть общая память → найдена", android.shared_storage(home) == home / "storage" / "shared")
check("материалы по умолчанию — в Documents общей памяти", android.default_materials(home, "Материалы Moodle") == home / "storage" / "shared" / "Documents" / "Материалы Moodle")
check("папка внутри Termux — «закрытая»", android.in_private_storage(home / "x" / "y", home))
check("папка в общей памяти (симлинк наружу) — не закрытая", not android.in_private_storage(TMP / "elsewhere", home))
ext = TMP / "external_storage"; ext.mkdir()
(home / "storage" / "shared").rmdir(); (home / "storage" / "shared").parent.rmdir() if False else None
os.symlink(ext, home / "storage" / "shared")      # как в настоящем Termux: ~/storage/shared → /storage/emulated/0
check("симлинк shared → внешняя память: не «закрытая»", not android.in_private_storage(home / "storage" / "shared" / "Documents" / "Материалы Moodle", home))
check("и находится как общая память", android.shared_storage(home) == home / "storage" / "shared")

# ── Termux:Boot ──
sc = android.boot_script(["/data/data/com.termux/files/usr/bin/python3", "/home/u/My App/moodle_offline.py", "--watch", "-y"], prefix="/data/data/com.termux/files/usr")
check("boot: шебанг и wake-lock", sc.startswith("#!/data/data/com.termux/files/usr/bin/sh\n") and "termux-wake-lock\n" in sc, sc)
check("boot: путь с пробелом в кавычках, параметры на месте", "'/home/u/My App/moodle_offline.py' --watch -y" in sc and "exec " in sc, sc)
check("boot: путь скрипта в ~/.termux/boot", android.boot_path(home) == home / ".termux" / "boot" / android.BOOT_NAME)

# ── внешние команды (подставные termux-*) ──
fb = TMP / "bin"; fb.mkdir(); log = TMP / "calls.log"
def fake(name, body=None):
    p = fb / name
    p.write_text(body or f'#!/bin/sh\necho "{name} $@" >> {log}\nexit 0\n'); p.chmod(0o755)
old_path = os.environ["PATH"]
os.environ["PATH"] = str(fb) + ":/usr/bin:/bin"
check("нет termux-open-url и am → False", android.open_url("https://example.org") is False)
for n in ("termux-open-url", "termux-open", "termux-notification", "termux-wake-lock", "termux-wake-unlock"): fake(n)
check("open_url → termux-open-url с адресом", android.open_url("https://example.org/a?b=1") is True and "termux-open-url https://example.org/a?b=1" in log.read_text())
check("open_url не открывает не-http", android.open_url("file:///etc/passwd") is False and android.open_url("javascript:alert(1)") is False)
check("open_file → termux-open с путём", android.open_file(Path("/x/y z.pdf")) is True and "termux-open /x/y z.pdf" in log.read_text())
fake("termux-share")
check("share_file без termux-share → False", True)
check("share_file → termux-share с файлом", android.share_file(Path("/x/Расписание.zip")) is True and "termux-share -a send /x/Расписание.zip" in log.read_text(), log.read_text())
check("notify → termux-notification с заголовком и текстом", android.notify("Moodle Offline", "Новый срок") is True and "termux-notification --title Moodle Offline --content Новый срок" in log.read_text(), log.read_text())
check("wake_lock on/off", android.wake_lock(True) and android.wake_lock(False) and "termux-wake-lock" in log.read_text() and "termux-wake-unlock" in log.read_text())
fake("termux-notification", "#!/bin/sh\nexit 1\n")
check("команда вернула ошибку → False", android.notify("a", "b") is False)
fake("termux-notification", "#!/bin/sh\nsleep 5\n")
t0 = time.time()
check("зависшая команда (нет Termux:API) → False по таймауту", android._run(["termux-notification", "x"], timeout=1) is False and time.time() - t0 < 4, str(time.time() - t0))
os.environ["PATH"] = old_path

# ── пути данных ──
app = TMP / "app"; app.mkdir()
env = {"TERMUX_VERSION": "0.118", "HOME": str(home)}
pl = dd.make_plan(app, False, env, home)
check("Android из исходников: режим user (а не dev)", pl.mode == "user", pl.mode)
check("данные в ~/.local/share/moodle-offline", pl.data_dir == home / ".local" / "share" / "moodle-offline", str(pl.data_dir))
check("материалы по умолчанию — в общей памяти телефона", pl.default_out == home / "storage" / "shared" / "Documents" / "Материалы Moodle", str(pl.default_out))
pl2 = dd.make_plan(app, False, {"HOME": str(home)}, home)
check("не Android из исходников: как раньше — dev рядом с кодом", pl2.mode == "dev" and pl2.data_dir == app / ".moodle_offline", str(pl2))
pl3 = dd.make_plan(app, False, {**env, "MOODLE_OFFLINE_DATA": str(TMP / "mine")}, home)
check("MOODLE_OFFLINE_DATA главнее", pl3.mode == "custom" and pl3.data_dir == TMP / "mine")
(app / "portable.txt").write_text("x")
pl4 = dd.make_plan(app, False, env, home)
check("portable.txt рядом с программой на Android → portable/dev (как просили)", pl4.mode == "dev" and pl4.data_dir == app / ".moodle_offline", str(pl4))
(app / "portable.txt").unlink()
home2 = TMP / "home2"; home2.mkdir()
pl5 = dd.make_plan(app, False, {"TERMUX_VERSION": "1", "HOME": str(home2)}, home2)
check("без общей памяти материалы в домашней папке Termux", pl5.default_out == home2 / "Материалы Moodle", str(pl5.default_out))
pl6 = dd.make_plan(app, True, {"HOME": str(home)}, home, "posix", on_android=False)
check("frozen Linux по-прежнему user: ~/Documents или ~", pl6.mode == "user" and pl6.default_out.name == "Материалы Moodle")

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL PASS" if not FAILS else f"\nFAILED: {FAILS}")
sys.exit(1 if FAILS else 0)

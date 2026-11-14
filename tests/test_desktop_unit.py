"""desktop.py — Linux и macOS: уведомления, выбор папки, автозапуск, ночная синхронизация, «снаповый» браузер.
Настоящих macOS, systemd и osascript здесь нет: команды проверяются как текст и на подставных запусках."""
import plistlib, shutil, subprocess, sys, tempfile
from pathlib import Path
import _root
import desktop as D

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

BASE = Path(tempfile.gettempdir()) / "mo_desktop_t"; shutil.rmtree(BASE, ignore_errors=True); BASE.mkdir()
have = lambda *names: (lambda n: "/usr/bin/" + n if n in names else None)

# 1. система
check("система: уже нормализованное имя не ломается (gui передаёт ответ system() дальше)", all(D.system(D.system(p)) == D.system(p) for p in ("win32", "darwin", "linux", "freebsd13")) and D.system("macos") == "macos" and D.system("windows") == "windows")
check("пути для macOS берутся и по «macos», а не только по «darwin»", D.autostart_path(D.system("darwin"), Path("/h")) == D.autostart_path("darwin", Path("/h")) == Path("/h/Library/LaunchAgents/org.moodleoffline.watch.plist")
      and D.sync_paths(D.system("darwin"), Path("/h")) == D.sync_paths("darwin", Path("/h")) and D.kind_of("macos") == "launchd")
check("определение системы", D.system("win32") == "windows" and D.system("darwin") == "macos" and D.system("linux") == "linux" and D.system("freebsd13") == "linux")

# 2. уведомления
EVIL = 'Курс "Сети"; do shell script "rm -rf ~" \\ \n-e -x'
c = D.notify_command(EVIL, "сообщение", "darwin", have("osascript"))
check("macOS: osascript, текст — аргументами, а не внутри скрипта", c[0] == "osascript" and c[-2].startswith("Курс") and c[-1] == "сообщение")
scripts = [c[i + 1] for i, x in enumerate(c) if x == "-e"]
check("macOS: в скрипте нет ни кусочка пользовательского текста", all("rm -rf" not in s and "Курс" not in s and "сообщение" not in s for s in scripts), str(scripts))
check("macOS: перевод строки в тексте заменён, текст не уходит в параметры", "\n" not in c[-2] and not c[-2].startswith("-"))
check("macOS: заголовок, начинающийся с «-», не станет параметром", not D.notify_command("-x", "m", "darwin", have("osascript"))[-2].startswith("-"))
check("macOS: нет osascript — None", D.notify_command("t", "m", "darwin", have()) is None)
c = D.notify_command("Заголовок", "Текст", "linux", have("notify-send"))
check("Linux: notify-send с «--» перед текстом", c[:3] == ["notify-send", "-a", D.APP_NAME] and "--" in c and c[-2:] == ["Заголовок", "Текст"])
check("Linux: нет notify-send — None; Windows сюда не относится", D.notify_command("t", "m", "linux", have()) is None)
check("длинный текст обрезается", len(D.notify_command("т", "я" * 5000, "linux", have("notify-send"))[-1]) <= 400)

# 3. выбор папки
cm = D.folder_dialog_commands("Куда сохранять", str(BASE), "darwin", have("osascript"))
check("macOS: одна команда osascript; путь и заголовок — аргументами", len(cm) == 1 and cm[0][-2:] == ["Куда сохранять", str(BASE)])
check("macOS: без существующей стартовой папки — без «default location»", all("default location" not in x for x in D.folder_dialog_commands("т", str(BASE / "нет"), "darwin", have("osascript"))[0]))
check("macOS: tkinter не используется (падает не в главном потоке)", all("tk" not in x.lower() for x in cm[0]))
z = D.folder_dialog_commands("т", str(BASE), "linux", have("zenity", "kdialog"), env={})
check("Linux: по умолчанию сначала zenity, потом kdialog", [x[0] for x in z] == ["zenity", "kdialog"] and "--directory" in z[0])
z = D.folder_dialog_commands("т", str(BASE), "linux", have("zenity", "kdialog"), env={"XDG_CURRENT_DESKTOP": "KDE"})
check("Linux в KDE: сначала kdialog", [x[0] for x in z] == ["kdialog", "zenity"])
check("Linux: ничего нет → пустой список; Windows → пустой", D.folder_dialog_commands("т", "", "linux", have(), env={}) == [] and D.folder_dialog_commands("т", "", "win32", have("zenity")) == [])
Rs = lambda rc, out="", err="": (lambda cmd, **k: subprocess.CompletedProcess(cmd, rc, out, err))
check("выбрали папку: путь без хвостового «/» и переноса", D.pick_folder("т", str(BASE), "darwin", have("osascript"), Rs(0, "/Users/ab/Документы/\n")) == "/Users/ab/Документы")
check("корень остаётся «/»", D.pick_folder("т", "", "darwin", have("osascript"), Rs(0, "/\n")) == "/")
check("«Отмена» → пустая строка (не None)", D.pick_folder("т", "", "darwin", have("osascript"), Rs(1, "", "execution error: User canceled. (-128)")) == "")
check("zenity: код 1 без текста — тоже «Отмена»", D.pick_folder("т", "", "linux", have("zenity"), Rs(1), env={}) == "")
check("нет программы выбора → None (вызывающий берёт запасной способ)", D.pick_folder("т", "", "linux", have(), Rs(0, "/x"), env={}) is None)
def boom(cmd, **k): raise OSError("нет")
check("не запустилась → пустая строка, а не исключение", D.pick_folder("т", "", "linux", have("zenity"), boom, env={}) == "")
seq = []
def first_fails(cmd, **k):
    seq.append(cmd[0]); return subprocess.CompletedProcess(cmd, 127 if cmd[0] == "zenity" else 0, "/home/a/Мат\n", "")
check("zenity не запустился — пробуем kdialog", D.pick_folder("т", "", "linux", have("zenity", "kdialog"), first_fails, env={}) == "/home/a/Мат" and seq == ["zenity", "kdialog"], str(seq))

# 4. команды запуска и время
check("собранный файл запускается сам, исходники — через Python", D.program_command(True, "/a/MoodleOffline", "py", "/a/moodle_offline.py", "--watch", "-y") == ["/a/MoodleOffline", "--watch", "-y"]
      and D.program_command(False, "x", "/usr/bin/python3", "/a/moodle_offline.py", "--sync") == ["/usr/bin/python3", "/a/moodle_offline.py", "--sync"])
check("время ЧЧ:ММ", D.parse_hhmm("3:10") == (3, 10) and D.parse_hhmm(" 23:59 ") == (23, 59))
for bad in ("24:00", "12:60", "abc", "", None, "1:1"):
    try:
        D.parse_hhmm(bad); check("время %r отклонено" % (bad,), False)
    except ValueError:
        check("время %r отклонено" % (bad,), True)
check("строка для cron", D.cron_line("03:10", ["/usr/bin/python3", "/my dir/moodle_offline.py", "--sync", "-y"]) == "10 3 * * * /usr/bin/python3 '/my dir/moodle_offline.py' --sync -y")
check("кавычка в пути для cron не ломает строку", "'/a'\\''b'" in D.cron_line("03:10", ["/a'b"]))

# 5. .desktop и systemd
check("Exec: пробелы и «%» экранируются", D.desktop_exec(["/a b/x", "--w", "50%"]) == '"/a b/x" --w 50%%')
check("Exec: $ ` \" \\ экранируются по спецификации", D.desktop_exec(['q"$`\\z']) == '"q\\"\\$\\`\\\\\\\\z"')
ent = D.desktop_entry(["/opt/Moodle Offline/MoodleOffline", "--watch", "-y"])
check("файл автозапуска: обязательные поля, Exec в одну строку", ent.startswith("[Desktop Entry]\n") and "Type=Application\n" in ent and "Terminal=false" in ent
      and ent.count("Exec=") == 1 and 'Exec="/opt/Moodle Offline/MoodleOffline" --watch -y\n' in ent, ent)
check("перевод строки в пути не создаёт лишних полей", not any(l.startswith("Evil=") for l in D.desktop_entry(["/a\nEvil=1"]).splitlines()) and D.desktop_entry(["/a\nEvil=1"]).count("\n") == ent.count("\n"))
check("systemd: пробелы в кавычках, % и $ удваиваются", D.systemd_quote("/a b/x") == '"/a b/x"' and D.systemd_quote("50%") == "50%%" and D.systemd_quote("$HOME") == "$$HOME" and D.systemd_quote("/usr/bin/py") == "/usr/bin/py")
sv = D.systemd_service(["/usr/bin/python3", "/my dir/moodle_offline.py", "--sync", "-y"])
check("служба systemd: oneshot и команда", "Type=oneshot" in sv and 'ExecStart=/usr/bin/python3 "/my dir/moodle_offline.py" --sync -y\n' in sv, sv)
tm = D.systemd_timer("3:05")
check("таймер systemd: ежедневно в 03:05, наверстать пропущенное", "OnCalendar=*-*-* 03:05:00" in tm and "Persistent=true" in tm and "WantedBy=timers.target" in tm and "moodle-offline-sync.service" in tm)

# 6. LaunchAgent
pl = plistlib.loads(D.launch_agent("org.moodleoffline.sync", ["/Applications/MoodleOffline.app/Contents/MacOS/MoodleOffline", "--sync", "-y"], hhmm="03:10"))
check("LaunchAgent: метка, команда списком, время", pl["Label"] == "org.moodleoffline.sync" and pl["ProgramArguments"][1:] == ["--sync", "-y"] and pl["StartCalendarInterval"] == {"Hour": 3, "Minute": 10} and pl["RunAtLoad"] is False)
pl = plistlib.loads(D.launch_agent(D.LABEL_WATCH, ["/x y/p", "--watch"], run_at_load=True))
check("LaunchAgent автозапуска: RunAtLoad, пробел в пути — одним аргументом", pl["RunAtLoad"] is True and pl["ProgramArguments"][0] == "/x y/p" and "StartCalendarInterval" not in pl)

# 7. пути
H = BASE / "home"
check("пути автозапуска", D.autostart_path("linux", H, {}) == H / ".config/autostart/moodle-offline-watch.desktop" and D.autostart_path("darwin", H) == H / "Library/LaunchAgents/org.moodleoffline.watch.plist"
      and D.autostart_path("win32", H) is None and D.autostart_path("linux", H, {"XDG_CONFIG_HOME": "/xdg/cfg"}) == Path("/xdg/cfg/autostart/moodle-offline-watch.desktop"))
check("XDG_CONFIG_HOME не из абсолютного пути игнорируется", D.autostart_path("linux", H, {"XDG_CONFIG_HOME": "относительный"}) == H / ".config/autostart/moodle-offline-watch.desktop")
check("пути ночной синхронизации", [p.name for p in D.sync_paths("linux", H, {})] == ["moodle-offline-sync.service", "moodle-offline-sync.timer"] and [p.name for p in D.sync_paths("darwin", H)] == ["org.moodleoffline.sync.plist"] and D.sync_paths("win32", H) == [])
check("вид автозапуска для окна", D.kind_of("linux") == "xdg" and D.kind_of("darwin") == "launchd")

# 8. автозапуск: включить и выключить
calls = []
def rec(cmd, **k):
    calls.append(cmd); return subprocess.CompletedProcess(cmd, 0, "", "")
r = D.autostart_set(True, ["/opt/m/MoodleOffline", "--watch", "-y"], "linux", H, {}, rec)
p = D.autostart_path("linux", H, {})
check("Linux: файл автозапуска создан", r["ok"] and p.is_file() and "Exec=/opt/m/MoodleOffline --watch -y" in p.read_text(encoding="utf-8"))
check("Linux: повторное включение не ломает файл, временных остатков нет", D.autostart_set(True, ["/opt/m/MoodleOffline", "--watch", "-y"], "linux", H, {}, rec)["ok"] and not list(p.parent.glob("*.tmp")))
r = D.autostart_set(False, [], "linux", H, {}, rec)
check("Linux: выключение удаляет файл; повторное — тоже без ошибки", r["ok"] and not p.exists() and D.autostart_set(False, [], "linux", H, {}, rec)["ok"])
calls.clear()
r = D.autostart_set(True, ["/Applications/M.app/Contents/MacOS/M", "--watch", "-y"], "darwin", H, {}, rec)
p = D.autostart_path("darwin", H)
check("macOS: plist создан, launchctl при включении не зовём", r["ok"] and p.is_file() and plistlib.loads(p.read_bytes())["RunAtLoad"] is True and calls == [])
r = D.autostart_set(False, [], "darwin", H, {}, rec)
check("macOS: выключение — launchctl bootout и удаление файла", r["ok"] and not p.exists() and calls and calls[0][:2] == ["launchctl", "bootout"] and calls[0][2].endswith("/" + D.LABEL_WATCH), str(calls))
check("Windows: автозапуск этим модулем не делается", D.autostart_set(True, ["x"], "win32", H, {}, rec)["ok"] is False)
ro = BASE / "ro"; ro.write_text("файл вместо папки")
r = D.autostart_set(True, ["x"], "linux", ro, {}, rec)
check("нельзя создать папку → ошибка текстом, не исключение", r["ok"] is False and r["error"])

# 9. ночная синхронизация
CMD = ["/usr/bin/python3", "/opt/m/moodle_offline.py", "--sync", "-y"]
calls.clear()
r = D.schedule_set(True, "03:10", CMD, "linux", H, {}, rec, have("systemctl"))
sv, tm = D.sync_paths("linux", H, {})
check("Linux: оба файла записаны, потом daemon-reload и enable --now", r["ok"] and sv.is_file() and tm.is_file() and "OnCalendar=*-*-* 03:10:00" in tm.read_text(encoding="utf-8")
      and [c[1:] for c in calls] == [["--user", "daemon-reload"], ["--user", "enable", "--now", "moodle-offline-sync.timer"]], str(calls))
calls.clear()
r = D.schedule_set(False, "", CMD, "linux", H, {}, rec, have("systemctl"))
check("Linux: выключение — disable --now, файлы удалены", r["ok"] and not sv.exists() and not tm.exists() and calls[0][2:] == ["disable", "--now", "moodle-offline-sync.timer"], str(calls))
r = D.schedule_set(True, "03:10", CMD, "linux", H, {}, rec, have())
check("Linux без systemd: понятная ошибка со строкой для cron", r["ok"] is False and "crontab" in r["error"] and "10 3 * * *" in r["error"] and not sv.exists())
fail = lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", "Failed to connect to bus")
r = D.schedule_set(True, "03:10", CMD, "linux", H, {}, fail, have("systemctl"))
check("Linux: systemctl отказал — ошибка с причиной и запасным путём", r["ok"] is False and "Failed to connect to bus" in r["error"] and "crontab" in r["error"])
check("неверное время отклонено до записи файлов", D.schedule_set(True, "25:99", CMD, "linux", H, {}, rec, have("systemctl"))["ok"] is False)
calls.clear()
r = D.schedule_set(True, "04:20", CMD, "darwin", H, {}, rec)
pf = D.sync_paths("darwin", H)[0]
check("macOS: plist записан, сначала bootout, потом bootstrap", r["ok"] and plistlib.loads(pf.read_bytes())["StartCalendarInterval"] == {"Hour": 4, "Minute": 20}
      and [c[1] for c in calls] == ["bootout", "bootstrap"] and calls[1][3] == str(pf), str(calls))
r = D.schedule_set(True, "04:20", CMD, "darwin", H, {}, lambda cmd, **k: subprocess.CompletedProcess(cmd, 0 if cmd[1] == "bootout" else 5, "", "Bootstrap failed: 5: Input/output error"))
check("macOS: bootstrap не удался — ошибка с текстом launchctl", r["ok"] is False and "Input/output error" in r["error"])
calls.clear()
r = D.schedule_set(False, "", CMD, "darwin", H, {}, rec)
check("macOS: выключение — bootout и удаление plist", r["ok"] and not pf.exists() and calls[0][1] == "bootout")
check("Windows: планировщик этим модулем не трогаем", D.schedule_set(True, "03:10", CMD, "win32", H, {}, rec)["ok"] is False)

# 10. «снаповый» браузер
check("snap-браузер определяется (по настоящему пути)", D.is_snap_browser("/usr/bin/chromium-browser", lambda p: "/snap/chromium/3000/usr/lib/chromium-browser/chrome")
      and D.is_snap_browser("/usr/bin/chromium", lambda p: "/usr/bin/snap"))
check("обычный Chrome — не snap", not D.is_snap_browser("/usr/bin/google-chrome", lambda p: "/opt/google/chrome/chrome") and not D.is_snap_browser("/x", lambda p: (_ for _ in ()).throw(OSError())))

import re as _re
check("модуль не подключает сеть и Moodle-код", not _re.search(r"^\s*(import|from)\s+(requests|urllib|http|socket|moodle_offline|gui)\b", Path(_root.ROOT / "desktop.py").read_text(encoding="utf-8"), _re.M))
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

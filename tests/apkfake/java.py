"""Подделка модуля java из Chaquopy для проверки моста на компьютере."""
import json, os

LOG = os.environ.get("FAKE_BRIDGE_LOG", os.path.join(__import__("tempfile").gettempdir(), "apkfake_run", "calls.log"))
STATE = {"all_files": False, "battery": False, "autostart": False, "wake": False}


def _log(name, *args):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps([name, *args], ensure_ascii=False) + "\n")


class Bridge:
    @staticmethod
    def openUrl(u): _log("openUrl", u); return True
    @staticmethod
    def openFile(p): _log("openFile", p); return True
    @staticmethod
    def shareFile(p): _log("shareFile", p); return True
    @staticmethod
    def _folder_ok(p):
        # как настоящий мост: папка должна существовать и лежать в общей памяти (внутреннюю память приложения «Файлы» не открывают)
        root = os.path.realpath(Bridge.sharedRoot()); q = os.path.realpath(p)
        return os.path.isdir(q) and (q == root or q.startswith(root + os.sep))
    @staticmethod
    def openFolder(p): _log("openFolder", p); return Bridge._folder_ok(p)
    @staticmethod
    def canOpenFolder(p): return Bridge._folder_ok(p)
    @staticmethod
    def notify(t, x): _log("notify", t, x); return True
    @staticmethod
    def setWakeLock(on): _log("setWakeLock", on); STATE["wake"] = on; return True
    @staticmethod
    def isBatteryExempt(): return STATE["battery"]
    @staticmethod
    def requestBatteryExemption(): _log("requestBatteryExemption"); return True
    @staticmethod
    def hasAllFilesAccess(): return STATE["all_files"]
    @staticmethod
    def requestAllFilesAccess(): _log("requestAllFilesAccess"); return True
    @staticmethod
    def sharedRoot(): return os.environ.get("FAKE_SHARED", os.path.join(__import__("tempfile").gettempdir(), "apkfake_run", "sdcard"))
    @staticmethod
    def materialsDir(name):
        if STATE["all_files"]:
            return os.path.join(Bridge.sharedRoot(), "Documents", name)
        return os.path.join(Bridge.sharedRoot(), "Android", "data", "ru.sevsu.moodleoffline", "files", name)
    @staticmethod
    def getAutostart(): return STATE["autostart"]
    @staticmethod
    def setAutostart(on): _log("setAutostart", on); STATE["autostart"] = on; return True


def jclass(name):
    assert name == "ru.sevsu.moodleoffline.Bridge", name
    return Bridge

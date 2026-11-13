"""Запуск программы для сквозной проверки перезапуска (test_restart_e2e.py): та же `gui.gui_main`, но окно не открывается —
вместо него в папку RESTART_MARK_DIR кладётся файл-метка, а «перезапуск» запускает этот же файл."""
import os, sys
import _root
import gui

MARK = os.environ["RESTART_MARK_DIR"]
gui.open_window = lambda url: open(os.path.join(MARK, f"opened-{os.getpid()}"), "w").write("1")
gui.App.restart_cmd = lambda self: [sys.executable, os.path.abspath(__file__)]
sys.exit(gui.gui_main(keep_running=True))

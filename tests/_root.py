"""Общая подготовка для тестов: корень проекта и папка tests попадают в sys.path.

Каждый тест начинается с `import _root` — так он запускается откуда угодно
(`python tests/test_news_unit.py` из корня или из самой папки tests).
"""
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
# Рабочая папка проверок приложения .apk (журнал вызовов подделанного моста, «общая память»…) — во временной папке.
APK_RUN = Path(tempfile.gettempdir()) / "apkfake_run"
FAKE_JAVA = TESTS / "apkfake"      # подделка модуля java (мост Android)

for p in (str(ROOT), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

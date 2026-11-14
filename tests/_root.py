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

# Серверы самих тестов (и макеты mock_*.py) — без обратного DNS при запуске: `HTTPServer.server_bind` зовёт `socket.getfqdn`, а на
# macOS и на серверах сборки это висит десятки секунд на каждый сервер. Сами серверы программы от этого не зависят (httpbind.py);
# test_httpbind_unit.py возвращает настоящий `server_bind` и проверяет именно их.
import http.server
import socketserver

ORIG_SERVER_BIND = http.server.HTTPServer.server_bind


def _fast_server_bind(self):
    socketserver.TCPServer.server_bind(self)
    host, port = self.server_address[:2]
    self.server_name = str(host)
    self.server_port = port


http.server.HTTPServer.server_bind = _fast_server_bind

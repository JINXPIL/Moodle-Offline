"""HTTP-сервер программы без лишнего ожидания при запуске. Только стандартная библиотека; без Moodle-кода.

Обычный `http.server.HTTPServer.server_bind` после привязки порта вызывает `socket.getfqdn(host)` — обратный DNS-запрос «как зовут
этот адрес». Нужен он только для CGI, которым мы не пользуемся, а на компьютере с настроенным по-особому DNS (корпоративный
VPN, macOS без сети, сервер сборки GitHub) он может висеть десятки секунд: окно программы, доступ для нейросетей и перенос
данных просто не отвечали бы всё это время. Здесь тот же сервер без этого запроса."""
from __future__ import annotations

import socketserver
from http.server import ThreadingHTTPServer


class QuickHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)        # вместо socket.getfqdn(host): имя нужно только CGI
        self.server_port = port

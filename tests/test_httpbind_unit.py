"""Серверы программы не ждут обратного DNS при запуске (на macOS и за особым DNS `socket.getfqdn` висит десятки секунд).
Подменяем `getfqdn` на «зависание» и смотрим, что окно, доступ для нейросетей и перенос данных поднимаются сразу."""
import json, socket, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import _root

FAILS = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{x}]" if (x and not c) else ""))
    if not c: FAILS.append(n)

import http.server, inspect
http.server.HTTPServer.server_bind = _root.ORIG_SERVER_BIND     # настоящий серверный код: общая подмена из _root.py здесь не нужна
calls = []
real = socket.getfqdn
def slow_getfqdn(*a, **k):
    calls.append(a); time.sleep(5); return real(*a, **k)
socket.getfqdn = slow_getfqdn

import httpbind, gui, mcp_server, transfer
check("gui.py, mcp_server.py и transfer.py берут общий быстрый сервер", gui.QuickHTTPServer is httpbind.QuickHTTPServer
      and transfer.QuickHTTPServer is httpbind.QuickHTTPServer and issubclass(mcp_server._Httpd, httpbind.QuickHTTPServer))

check("окно запускается на быстром сервере (а не на стандартном)", "QuickHTTPServer(" in inspect.getsource(gui.gui_main) and "ThreadingHTTPServer(" not in inspect.getsource(gui.gui_main))
check("раздача переноса — тоже", "QuickHTTPServer(" in inspect.getsource(transfer.Sender.start))

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")

t0 = time.time()
srv = httpbind.QuickHTTPServer(("127.0.0.1", 0), H)
dt = time.time() - t0
check("сервер поднимается сразу, обратный DNS не спрашивает", dt < 2 and not calls, f"{dt:.1f} с, вызовов getfqdn: {len(calls)}")
check("адрес и порт на месте", srv.server_name == "127.0.0.1" and srv.server_port == srv.server_address[1] > 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
check("и отвечает", urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/", timeout=5).read() == b"ok")
srv.shutdown()

# MCP-сервер: стабильный порт, запуск и остановка без getfqdn
import socket as _s
def free():
    s = _s.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
srvm = mcp_server._Httpd(("127.0.0.1", free()), H)
check("MCP-сервер (_Httpd) тоже без getfqdn", not calls, str(calls))
srvm.server_close()

# имя компьютера в системе отвечает долго (macOS без сети): список адресов для переноса не должен ждать
real_gai = socket.getaddrinfo
gai_calls = []
def slow_gai(host, *a, **k):
    if host in (None, "") or str(host).startswith(("127.", "10.255")): return real_gai(host, *a, **k)
    gai_calls.append(host); time.sleep(6); return real_gai(host, *a, **k)
socket.getaddrinfo = slow_gai
transfer._HOST_SLOW_UNTIL = 0.0
t0 = time.time(); ips = transfer.local_ips(); dt1 = time.time() - t0
check("адреса для переноса: не ждём ответа системы по имени компьютера дольше пары секунд", dt1 < 3.5 and gai_calls, f"{dt1:.1f} с, обращений {len(gai_calls)}")
t0 = time.time(); transfer.local_ips(); dt2 = time.time() - t0
check("повторно — мгновенно (медленный способ на время отложен)", dt2 < 0.5 and len(gai_calls) == 1, f"{dt2:.1f} с, обращений {len(gai_calls)}")

# перенос данных: Sender.start() привязывает порт синхронно
tmp = Path(tempfile.mkdtemp(prefix="hb_"))
class Rep:
    def log(self, *a, **k): pass
    def __getattr__(self, n): return lambda *a, **k: None
t0 = time.time()
snd = transfer.Sender(tmp, tmp, ["materials"], {}, Rep(), ttl=30, bind="127.0.0.1")
info = snd.start(); dt = time.time() - t0
check("перенос по Wi-Fi: раздача стартует сразу", dt < 2 and not calls and info.get("port"), f"{dt:.1f} с {calls}")
socket.getaddrinfo = real_gai
snd.stop()
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)

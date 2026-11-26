"""Предпросмотр скачанных файлов и ручная проверка Moodle через локальный сервер."""
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

import _root
from pypdf import PdfWriter


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        out = Path(tmp.name)
        os.environ["MOODLE_OFFLINE_DATA"] = str(out / "data")
        import gui

        cls.pdf = "ЛР 4.pdf"
        writer = PdfWriter()
        for _ in range(2):
            writer.add_blank_page(width=120, height=160)
        writer.write(out / cls.pdf)
        (out / "index.html").write_text("<p>Лекция</p>", encoding="utf-8")
        (out / "slide.css").write_text("p { color: black; }", encoding="utf-8")
        (out / "slide.woff").write_bytes(b"wOFF")
        cls.app = gui.APP = gui.App()
        cls.app.settings.update(out_dir=str(out), watch={"check": {"enabled": False}})
        server = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
        cls.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)

    def request(self, path, method="GET", headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, headers={"X-Token": self.app.token, **(headers or {})})
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def test_pdf_can_open_on_a_selected_page(self):
        query = urlencode({"path": self.pdf, "page": 2})
        code, headers, body = self.request("/api/pdf/page?" + query)
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-PDF-Page"], "2")
        self.assertEqual(headers["X-PDF-Pages"], "2")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_styles_and_fonts_work_in_the_sandbox(self):
        base = "/f/" + quote(self.app.token) + "/"
        origin = {"Origin": "null"}
        for name in ("slide.css", "slide.woff"):
            code, headers, _ = self.request(base + name, headers=origin)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "null")
        page = self.request(base + "index.html", headers=origin)
        self.assertNotIn("Access-Control-Allow-Origin", page[1])
        self.assertTrue(page[1]["Content-Security-Policy"].startswith("sandbox "))
        self.assertNotIn("allow-same-origin", page[1]["Content-Security-Policy"])

    def test_manual_check_with_timer_off_and_deduplicates_an_in_flight_check(self):
        started, finished = threading.Event(), threading.Event()

        class Client:
            def probe(self):
                started.set()
                if not finished.wait(5):
                    raise TimeoutError()
                return "ok", 0.5

        self.app.new_client = Client
        path = "/api/moodle/check"
        with ThreadPoolExecutor() as executor:
            pending = executor.submit(self.request, path, "POST")
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(json.loads(self.request(path, "POST")[2]), {"ok": True, "checking": True})
            finally:
                finished.set()
            code, _, body = pending.result()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["net"]["status"], "ok")
        self.assertEqual(self.app.net["latency"], 0.5)


if __name__ == "__main__":
    unittest.main()

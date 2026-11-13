"""Фейковые нейросети для проверок: Claude (/v1/messages), Gemini (…:generateContent), OpenAI и совместимые (/v1/chat/completions),
плюс списки моделей. Запросы запоминаются в .log. Ключи и тексты вымышленные. Запускается в потоке: MockLLM().start()."""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS = {"claude": "sk-ant-TESTKEY-0123456789", "gemini": "AIzaTESTKEY0123456789abcdefgh", "openai": "sk-TESTKEY-0123456789"}


class MockLLM:
    def __init__(self):
        self.log = []                 # (метод, путь, заголовки, тело)
        self.mode = {}                # поведение: "strict_new" — отвергает max_tokens; "legacy" — отвергает max_completion_tokens; "fail_once": код
        self.fail_count = 0
        self.reply = None             # подменить текст ответа
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                try: return json.loads(self.rfile.read(n) or b"{}")
                except ValueError: return {}

            def do_GET(self):
                outer.log.append(("GET", self.path, dict(self.headers), None))
                if self.path.startswith("/v1beta/models"):
                    if self.headers.get("x-goog-api-key") != KEYS["gemini"]: return self._json(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
                    return self._json(200, {"models": [{"name": "models/gemini-x-flash", "supportedGenerationMethods": ["generateContent"]},
                                                       {"name": "models/embedding-1", "supportedGenerationMethods": ["embedContent"]}]})
                if self.path.startswith("/v1/models"):
                    if "x-api-key" in self.headers:
                        return self._json(200, {"data": [{"id": "claude-b"}, {"id": "claude-a"}]})
                    return self._json(200, {"data": [{"id": "gpt-x"}, {"id": "text-embedding-3"}, {"id": "whisper-1"}, {"id": "gpt-y"}]})
                self._json(404, {"error": {"message": "no route"}})

            def do_POST(self):
                body = self._body()
                outer.log.append(("POST", self.path, dict(self.headers), body))
                if outer.mode.get("delay"): time.sleep(outer.mode["delay"])
                text = json.dumps(body, ensure_ascii=False)
                if outer.mode.get("fail_once") and outer.fail_count == 0:
                    outer.fail_count = 1
                    return self._json(outer.mode["fail_once"], {"error": {"message": "overloaded"}})
                if "FORCE_429" in text: return self._json(429, {"error": {"message": "Rate limit reached"}})
                if "FORCE_500" in text: return self._json(500, {"error": {"message": "internal"}})
                if "FORCE_REGION" in text: return self._json(403, {"error": {"message": "Request not allowed from your region"}})
                if "FORCE_ECHOKEY" in text: return self._json(401, {"error": {"message": "Incorrect API key provided: " + (self.headers.get("Authorization") or self.headers.get("x-api-key") or self.headers.get("x-goog-api-key") or "")}})
                if "FORCE_HTML" in text:
                    b = b"<html>oops</html>"; self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
                ans = outer.reply or ("Ответ нейросети [1]." if "Вопрос студента" in text else "## Вопросы\n1. Что такое X?\n\n=== ОТВЕТЫ ===\n\n## Ответы\n1. Это Y." if "вопросов для подготовки" in text else "Конспект: главное.")
                if self.path == "/v1/messages":
                    if self.headers.get("x-api-key") != KEYS["claude"]: return self._json(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
                    if body.get("model") == "nope": return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "model: nope"}})
                    return self._json(200, {"content": [{"type": "text", "text": ans}], "stop_reason": "max_tokens" if "TRUNC" in text else "end_turn", "usage": {"input_tokens": 5, "output_tokens": 7}})
                if self.path.startswith("/v1beta/models/") and self.path.endswith(":generateContent"):
                    if self.headers.get("x-goog-api-key") != KEYS["gemini"]: return self._json(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
                    if "FORCE_BLOCK" in text: return self._json(200, {"promptFeedback": {"blockReason": "SAFETY"}})
                    if "FORCE_NOCAND" in text: return self._json(200, {"candidates": []})
                    parts = [{"text": "размышление", "thought": True}, {"text": ans}]
                    return self._json(200, {"candidates": [{"content": {"parts": parts}, "finishReason": "MAX_TOKENS" if "TRUNC" in text else "STOP"}], "usageMetadata": {"promptTokenCount": 5}})
                if self.path in ("/v1/chat/completions", "/openai/v1/chat/completions", "/chat/completions"):
                    if self.headers.get("Authorization") != "Bearer " + KEYS["openai"] and outer.mode.get("need_key", True): return self._json(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})
                    if outer.mode.get("strict_new") and "max_tokens" in body: return self._json(400, {"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."}})
                    if outer.mode.get("legacy") and "max_completion_tokens" in body: return self._json(400, {"error": {"message": "Unsupported parameter: max_completion_tokens"}})
                    return self._json(200, {"choices": [{"message": {"content": ans}, "finish_reason": "length" if "TRUNC" in text else "stop"}], "usage": {"prompt_tokens": 5}})
                self._json(404, {"error": {"message": "no route " + self.path}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.srv.shutdown()

"""push_run.py: отправка прогона на стенд."""
import contextlib
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import push_run as pr  # noqa: E402

STATUS = {
    "run": "2026-10-01-test-lena",
    "title": "Тестовый прогон",
    "steps": [
        {"id": "brief", "n": 0, "title": "Бриф", "status": "done", "file": "brief.md"},
        {"id": "collect", "n": 1, "title": "Сбор", "status": "done", "files": ["evidence/job.md"]},
        {"id": "check", "n": 2, "title": "Сверка цитат", "status": "running", "file": "check.md"},
        {"id": "card", "n": 3, "title": "Карточка решения", "status": "next", "file": "card.md"},
    ],
    "question": None,
    "answers": [],
}
BRIEF = "# Бриф: тест\n\n## Идея словами автора\nСвопы «прямо в виджете».\n"
EVIDENCE = "# Сбор: I want to тест\n\nСчёт: эпизодов о работе — 2.\n"
NOTES = ("# Заметки прогона: тест\n\n"
         "Ведёт: Лена. Движок: Codex, главная модель: gpt-6-sol.\n\n"
         "01.10 10:05 — шаг 0 — бриф собран — gpt-6-sol — 10 — неизвестно\n")
CARD = ("# Карточка решения\n\n## Ответ\n\n  Не погружаться сейчас — живого сегмента нет.\n"
        "Одно действие: опрос.\n\n## Идея автора как есть\nСвопы.\n")


class Stand:
    """Заглушка стенда: копит запросы, отвечает кодом code и телом reply(тело запроса)."""

    def __init__(self, code=200, reply=None):
        self.code, self.requests = code, []
        self.reply = reply or self.created
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                req = {"path": self.path, "type": self.headers["Content-Type"], "raw": raw, "body": json.loads(raw)}
                stand.requests.append(req)
                out = stand.reply(req["body"])
                out = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(stand.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def created(self, body):
        if body["id"]:
            return {"id": body["id"], "url": f"{self.url}/r/{body['id']}"}
        return {"id": "lena-1", "token": "tok-1", "url": f"{self.url}/r/lena-1"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class PushRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / STATUS["run"]
        (self.run / "evidence").mkdir(parents=True)
        self.write_status(STATUS)
        self.files = {"brief.md": BRIEF, "evidence/job.md": EVIDENCE, "notes.md": NOTES,
                      "input.md": "Идея: свопы.\n", "author-answers.md": "18:24 — да\n", "card.md": CARD}
        for name, text in self.files.items():
            (self.run / name).write_text(text, encoding="utf-8")
        self.stand = Stand()
        # локальная заглушка — мимо системного прокси, если он есть
        env = mock.patch.dict(os.environ, {"no_proxy": "*", "NO_PROXY": "*"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.stand.close()
        self.tmp.cleanup()

    def write_status(self, status):
        (self.run / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    def with_statuses(self, *statuses):
        steps = [dict(s, status=st) for s, st in zip(STATUS["steps"], statuses)]
        self.write_status(dict(STATUS, steps=steps))
        return pr.payload(self.run)["meta"]

    def push(self, stand=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pr.main(["--run", str(self.run), "--stand", stand or self.stand.url])
        return code, out.getvalue(), err.getvalue()

    def key(self):
        return (self.run / ".stand.json").read_text(encoding="utf-8")

    # payload

    def test_payload_data(self):
        data = pr.payload(self.run)["data"]
        for name in ("input.md", "author-answers.md", "notes.md", "brief.md", "evidence/job.md", "card.md"):
            self.assertEqual(data["files"][name], self.files[name])
        self.assertNotIn("template", data)
        self.assertEqual(data["links"], {})
        self.assertEqual(data["title"], "Тестовый прогон")
        self.assertEqual(data["idea"], "Свопы «прямо в виджете».")
        self.assertEqual(len(data["timeline"]), 1)

    def test_payload_meta(self):
        meta = pr.payload(self.run)["meta"]
        self.assertEqual((meta["run"], meta["title"]), (STATUS["run"], "Тестовый прогон"))
        self.assertEqual((meta["author"], meta["engine"], meta["model"]), ("Лена", "Codex", "gpt-6-sol"))
        self.assertEqual((meta["state"], meta["step"]), ("running", "check"))
        self.assertEqual(meta["verdict"], "Не погружаться сейчас — живого сегмента нет.")

    def test_author_and_engine_in_data(self):
        data = pr.payload(self.run)["data"]
        self.assertEqual((data["author"], data["engine"]), ("Лена", "Codex"))
        (self.run / "notes.md").unlink()
        data = pr.payload(self.run)["data"]
        self.assertEqual((data["author"], data["engine"]), (None, None))

    def test_model_with_dots_and_engine_with_space(self):
        (self.run / "notes.md").write_text(
            "# Заметки\n\nВедёт: Андрей. Движок: Claude Code, главная модель: gpt-6.1-sol.\n", encoding="utf-8")
        meta = pr.payload(self.run)["meta"]
        self.assertEqual((meta["author"], meta["engine"], meta["model"]), ("Андрей", "Claude Code", "gpt-6.1-sol"))

    def test_payload_without_optional_files(self):
        for name in ("input.md", "author-answers.md", "notes.md", "card.md"):
            (self.run / name).unlink()
        data, meta = pr.payload(self.run)["data"], pr.payload(self.run)["meta"]
        for name in ("input.md", "author-answers.md", "notes.md", "card.md"):
            self.assertNotIn(name, data["files"])
        self.assertEqual((meta["author"], meta["engine"], meta["model"], meta["verdict"]), (None, None, None, None))

    def test_state_and_step(self):
        cases = [
            (("done", "done", "skipped", "done"), "done", "card"),
            (("done", "done", "waiting", "next"), "waiting", "check"),
            (("done", "running", "waiting", "next"), "waiting", "collect"),
            (("done", "done", "running", "next"), "running", "check"),
            (("done", "done", "next", "next"), "running", "collect"),
            (("next", "next", "next", "next"), "running", None),
        ]
        for statuses, state, step in cases:
            with self.subTest(statuses=statuses):
                meta = self.with_statuses(*statuses)
                self.assertEqual((meta["state"], meta["step"]), (state, step))

    def test_verdict_absent_section(self):
        (self.run / "card.md").write_text("# Карточка\n\n## Идея автора как есть\nСвопы.\n", encoding="utf-8")
        self.assertIsNone(pr.payload(self.run)["meta"]["verdict"])

    # отправка

    def test_first_push_creates_key(self):
        code, out, err = self.push()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.strip(), f"{self.stand.url}/r/lena-1")
        req, = self.stand.requests
        self.assertEqual(req["path"], "/api/push")
        self.assertIn("application/json", req["type"])
        self.assertEqual((req["body"]["id"], req["body"]["token"]), (None, None))
        self.assertEqual(req["body"]["meta"]["author"], "Лена")
        self.assertIn("Лена".encode(), req["raw"])  # без \\u-экранирования
        self.assertEqual(json.loads(self.key()), {"id": "lena-1", "token": "tok-1", "url": f"{self.stand.url}/r/lena-1"})

    def test_second_push_sends_key(self):
        self.push()
        code, out, err = self.push()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.strip(), f"{self.stand.url}/r/lena-1")
        second = self.stand.requests[1]["body"]
        self.assertEqual((second["id"], second["token"]), ("lena-1", "tok-1"))
        self.assertEqual(json.loads(self.key())["token"], "tok-1")

    def test_stand_error_keeps_key(self):
        (self.run / ".stand.json").write_text('{"id": "lena-1", "token": "tok-1", "url": "u"}\n', encoding="utf-8")
        for code_, reply in ((500, b"Internal Server Error"), (403, {"error": "ключ не подходит"})):
            with self.subTest(code=code_):
                self.stand.code, self.stand.reply = code_, (lambda body, r=reply: r)
                code, out, err = self.push()
                self.assertEqual((code, out), (1, ""))
                self.assertTrue(err.startswith("стенд:"), err)
                self.assertEqual(err.count("\n"), 1)
                self.assertIn(str(code_), err)
                self.assertEqual(self.key(), '{"id": "lena-1", "token": "tok-1", "url": "u"}\n')
        self.assertIn("ключ не подходит", err)
        self.assertIn("удали .stand.json", err)  # 403: как выйти — в той же строке
        self.assertIn("новую страницу", err)

    def test_stand_error_does_not_create_key(self):
        self.stand.code = 500
        code, _, err = self.push()
        self.assertEqual(code, 1)
        self.assertIn("стенд:", err)
        self.assertFalse((self.run / ".stand.json").exists())

    def test_broken_key_sends_nothing(self):
        (self.run / ".stand.json").write_text("{", encoding="utf-8")
        code, out, err = self.push()
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("стенд:") and ".stand.json" in err, err)
        self.assertEqual(self.stand.requests, [])
        self.assertEqual(self.key(), "{")

    def test_no_network(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        code, out, err = self.push(stand=f"http://127.0.0.1:{port}")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("стенд:"), err)
        self.assertEqual(err.count("\n"), 1)
        self.assertFalse((self.run / ".stand.json").exists())

    def assert_one_line(self, code, out, err):
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith("стенд:"), err)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertNotIn("Traceback", err)

    def test_malformed_status_is_one_line(self):
        self.write_status(dict(STATUS, steps=["brief", "collect"]))
        self.assert_one_line(*self.push())
        self.assertEqual(self.stand.requests, [])

    def test_unexpected_error_is_one_line(self):
        for exc in (http.client.IncompleteRead(b"par"), RuntimeError("что-то\nсломалось")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(pr.urllib.request, "urlopen", side_effect=exc):
                code, out, err = self.push()
                self.assert_one_line(code, out, err)
                self.assertIn(type(exc).__name__, err)
        self.assertFalse((self.run / ".stand.json").exists())

    def test_answer_without_url_is_error(self):
        self.stand.reply = lambda body: {"id": "lena-1"}
        code, _, err = self.push()
        self.assertEqual(code, 1)
        self.assertIn("стенд:", err)
        self.assertFalse((self.run / ".stand.json").exists())

    def test_big_body_drops_largest_evidence(self):
        big = {"evidence/big.md": "a" * 3_000_000, "evidence/mid.md": "b" * 1_500_000, "evidence/small.md": "c\n"}
        for name, text in big.items():
            (self.run / name).write_text(text, encoding="utf-8")
        steps = [dict(s) for s in STATUS["steps"]]
        steps[1]["files"] = ["evidence/job.md", *big]
        self.write_status(dict(STATUS, steps=steps))
        code, out, err = self.push()
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith("/r/lena-1"))
        req, = self.stand.requests
        sent = req["body"]["data"]["files"]
        self.assertNotIn("evidence/big.md", sent)
        self.assertIn("evidence/mid.md", sent)
        self.assertIn("evidence/small.md", sent)
        self.assertLessEqual(len(req["raw"]), 4_000_000)
        self.assertTrue(err.startswith("стенд: не влезли"), err)
        self.assertIn("evidence/big.md", err)
        self.assertNotIn("evidence/mid.md", err)

    def test_stand_url_order(self):
        with mock.patch.dict(os.environ, {"HARNESS_STAND_URL": "http://env.example/"}):
            self.assertEqual(pr.stand_url("http://arg.example/"), "http://arg.example")
            self.assertEqual(pr.stand_url(None), "http://env.example")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pr.stand_url(None), "https://product-harness-stand.vercel.app")
        self.assertEqual(pr.STAND, "https://product-harness-stand.vercel.app")


if __name__ == "__main__":
    unittest.main()

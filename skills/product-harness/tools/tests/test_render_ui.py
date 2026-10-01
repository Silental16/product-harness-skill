"""render_ui.py: страница прогона."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_ui as ui  # noqa: E402

DATA_RE = re.compile(r'<script type="application/json" id="run-data">(.*?)</script>', re.S)
STATUS = {
    "run": "2026-09-30-test-andrew",
    "title": "Тестовый прогон",
    "updated": "30.09.2026 20:05",
    "steps": [
        {"id": "brief", "n": 0, "title": "Бриф", "status": "done", "file": "brief.md", "summary": "Бриф принят."},
        {"id": "collect", "n": 1, "title": "Сбор", "status": "done", "files": ["evidence/job.md"]},
        {"id": "check", "n": 2, "title": "Сверка цитат", "status": "running", "file": "check.md"},
        {"id": "map", "n": 3, "title": "Карта сегментов", "status": "next"},
    ],
    "question": None,
    "answers": [],
}
BRIEF = "# Бриф: тест\n\n## Идея словами автора\nСвопы «прямо в виджете».\n\n## Кто платит\n- Платит: неизвестно\n"
EVIDENCE = "# Сбор: I want to тест\n\nСчёт: эпизодов о работе — 2; самый частый сайт — a.example, 1 из 2 цитат.\n"
NOTES = ("# Заметки прогона\n\n"
         "30.09 18:24 — шаг 0 — бриф собран, три вопроса — claude-opus-5-5 — 10 — неизвестно\n"
         "30.09 18:46 — шаг 1 — сбор закончен — claude-opus-5-5 — 21 — 323946\n"
         "строка без даты\n")


def data_of(html):
    m = DATA_RE.search(html)
    return json.loads(m.group(1)), m.group(1)


class RenderUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "evidence").mkdir()
        (self.run / "status.json").write_text(json.dumps(STATUS, ensure_ascii=False), encoding="utf-8")
        (self.run / "brief.md").write_text(BRIEF, encoding="utf-8")
        (self.run / "evidence" / "job.md").write_text(EVIDENCE, encoding="utf-8")
        (self.run / "notes.md").write_text(NOTES, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def render(self):
        ui.main(["--run", str(self.run)])
        return (self.run / "ui.html").read_text(encoding="utf-8")

    def test_data_brief_and_timeline(self):
        html = self.render()
        data, _ = data_of(html)
        self.assertEqual(data["title"], "Тестовый прогон")
        self.assertEqual(data["files"]["brief.md"], BRIEF)
        self.assertEqual(data["idea"], "Свопы «прямо в виджете».")
        self.assertIn("Счёт: эпизодов о работе — 2", data["counts"]["evidence"]["evidence/job.md"])
        first, second = data["timeline"]
        self.assertEqual((first["time"], first["step"], first["text"]), ("18:24", 0, "бриф собран, три вопроса"))
        self.assertEqual((second["model"], second["minutes"], second["tokens"]), ("claude-opus-5-5", "21", "323946"))
        self.assertEqual(len(data["timeline"]), 2)
        self.assertIn("<title>Тестовый прогон</title>", html)

    def test_script_close_is_escaped(self):
        (self.run / "brief.md").write_text("до </script><script>alert(1)</script> после", encoding="utf-8")
        data, raw = data_of(self.render())
        self.assertNotIn("</script", raw.lower())
        self.assertNotIn("<!--", raw)
        self.assertEqual(data["files"]["brief.md"], "до </script><script>alert(1)</script> после")

    def test_missing_file_is_skipped(self):
        (self.run / "evidence" / "job.md").unlink()
        data, _ = data_of(self.render())
        self.assertNotIn("evidence/job.md", data["files"])
        self.assertNotIn("check.md", data["files"])
        self.assertIn("brief.md", data["files"])

    def test_self_publish_shape(self):
        html = self.render()
        self.assertTrue(html.startswith(ui.HEAD))
        self.assertTrue(html.endswith(ui.TAIL))
        self.assertEqual(html.count("<title>"), 1)
        self.assertLess(html.index("<title>"), 8192)
        # страница пересобирает себя тем же каркасом, что и скрипт
        tpl = ui.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(ui.HEAD.rstrip("\n"), tpl)
        self.assertEqual((tpl.count("{{RUN_TITLE}}"), tpl.count("{{RUN_DATA}}")), (1, 1))
        data, _ = data_of(html)
        self.assertEqual(data["template"], tpl)

    def test_no_github_links_inside_git_repo(self):
        # прогоны живут на стенде, а не в git: ссылок на GitHub нет и в папке внутри репозитория
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.run)], check=True)
        self.assertEqual(data_of(self.render())[0]["links"], {})

    def test_default_steps_are_zero_to_five(self):
        (self.run / "status.json").write_text(json.dumps({"run": "r", "title": "Т", "question": None, "answers": []}),
                                              encoding="utf-8")
        steps = data_of(self.render())[0]["steps"]
        self.assertEqual([(s["n"], s["id"], s["status"]) for s in steps],
                         [(0, "brief", "next"), (1, "collect", "next"), (2, "check", "next"),
                          (3, "map", "next"), (4, "review", "next"), (5, "card", "next")])
        self.assertEqual(steps[5]["file"], "card.md")
        self.assertNotIn("file", steps[1])

    def test_question_items_reach_page(self):
        # вопросы шага 0 списком: страница рисует каждый строкой, метки дают заголовки групп
        items = ["[Расхождение] В описании «~35%», в расчёте 12,63%. Какое число верно? Например: «12,63%».",
                 "[Вопрос] Кто платит за обмен? Например: «покупатель, 1% сверху»."]
        status = {**STATUS, "question": {"id": "q-1", "step": "brief", "text": "Принимаете бриф?",
                                         "items": items, "asked": "20:05"}}
        (self.run / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        data, raw = data_of(self.render())
        self.assertEqual(data["question"]["items"], items)
        self.assertEqual(data["question"]["text"], "Принимаете бриф?")
        self.assertIn("Кто платит за обмен?", raw)
        tpl = ui.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("q.items", tpl)
        self.assertIn("Расхождения", tpl)

    def test_check_md_counts(self):
        (self.run / "check.md").write_text(
            "# Проверки прогона\n\n## Сверка цитат\n\nЦитат: 5. Нашлись: 5. Зачёркнуты: 0.\n- мелочь\n\n"
            "## Проверка карты, круг 1\n\nНарушений: 8, из них спорно: 5.\n- пункт\n\n"
            "## Проверка карты, круг 2\n\nНарушений: 0.\n", encoding="utf-8")
        counts = data_of(self.render())[0]["counts"]
        self.assertEqual(counts["quotes"], "Цитат: 5. Нашлись: 5. Зачёркнуты: 0.")
        self.assertEqual([(r["round"], r["violations"], r["disputed"]) for r in counts["reviews"]],
                         [(1, 8, 5), (2, 0, 0)])


if __name__ == "__main__":
    unittest.main()

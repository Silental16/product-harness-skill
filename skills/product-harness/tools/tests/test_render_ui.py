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
        {"id": "formula", "n": 1, "title": "Формула запроса", "status": "done", "files": ["formula.md", "input.md"],
         "section": "Формула", "summary": "Формула принята."},
        {"id": "market", "n": 2, "title": "Кто уже это делает и рынок", "status": "done",
         "files": ["evidence/job.md", "check.md", "card.md"],
         "section": ["Кто уже это делает", "Боль автора: что показал сбор"]},
        {"id": "segments", "n": 3, "title": "Сегменты", "status": "running", "file": "card.md", "section": "Сегменты"},
    ],
    "question": None,
    "answers": [],
}
FORMULA = ("# Формула запроса: тест\n\n## Запрос словами автора\nСвопы «прямо в виджете».\n\n"
           "## Формула\n- **Что хочу понять:** стоит ли строить свопы.\n")
EVIDENCE = ("# Сбор: кто уже это построил\n\nСчёт: компаний названо — 2; строк цитат — 3.\n\n"
            "## Игроки и аналоги\n- Сырой список сборщика.\n")
CARD = ("# Карта возможностей: тест\n\n## Ответ\nНе знаем: свопы — боль только со слов автора.\n\n"
        "## Кто уже это делает\n- Свопы есть у двух соседей.\n\n"
        "## Боль автора: что показал сбор\nНе подтвердилась.\n\n## Сегменты\nС1. Держатели токенов.\n")
NOTES = ("# Заметки прогона\n\n"
         "30.09 18:24 — шаг 1 — формула собрана, пять вопросов — claude-opus-5-5 — 10 — неизвестно\n"
         "30.09 18:46 — шаг 2 — сбор закончен — claude-opus-5-5 — 21 — 323946\n"
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
        (self.run / "formula.md").write_text(FORMULA, encoding="utf-8")
        (self.run / "evidence" / "job.md").write_text(EVIDENCE, encoding="utf-8")
        (self.run / "card.md").write_text(CARD, encoding="utf-8")
        (self.run / "notes.md").write_text(NOTES, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def render(self):
        ui.main(["--run", str(self.run)])
        return (self.run / "ui.html").read_text(encoding="utf-8")

    def test_data_formula_and_timeline(self):
        html = self.render()
        data, _ = data_of(html)
        self.assertEqual(data["title"], "Тестовый прогон")
        self.assertEqual(data["files"]["formula.md"], FORMULA)
        self.assertEqual(data["idea"], "Свопы «прямо в виджете».")
        self.assertIn("Счёт: компаний названо — 2", data["counts"]["evidence"]["evidence/job.md"])
        first, second = data["timeline"]
        self.assertEqual((first["time"], first["step"], first["text"]), ("18:24", 1, "формула собрана, пять вопросов"))
        self.assertEqual((second["model"], second["minutes"], second["tokens"]), ("claude-opus-5-5", "21", "323946"))
        self.assertEqual(len(data["timeline"]), 2)
        self.assertIn("<title>Тестовый прогон</title>", html)

    def test_script_close_is_escaped(self):
        (self.run / "formula.md").write_text("до </script><script>alert(1)</script> после", encoding="utf-8")
        data, raw = data_of(self.render())
        self.assertNotIn("</script", raw.lower())
        self.assertNotIn("<!--", raw)
        self.assertEqual(data["files"]["formula.md"], "до </script><script>alert(1)</script> после")

    def test_missing_file_is_skipped(self):
        (self.run / "evidence" / "job.md").unlink()
        data, _ = data_of(self.render())
        self.assertNotIn("evidence/job.md", data["files"])
        self.assertNotIn("check.md", data["files"])
        self.assertIn("formula.md", data["files"])

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

    def test_default_steps_are_one_to_seven(self):
        (self.run / "status.json").write_text(json.dumps({"run": "r", "title": "Т", "question": None, "answers": []}),
                                              encoding="utf-8")
        steps = data_of(self.render())[0]["steps"]
        self.assertEqual([(s["n"], s["id"], s["status"]) for s in steps],
                         [(1, "formula", "next"), (2, "market", "next"), (3, "segments", "next"),
                          (4, "solutions", "next"), (5, "money", "next"), (6, "test", "next"), (7, "card", "next")])
        self.assertEqual(steps[0]["title"], "Разбор запроса")
        self.assertEqual(steps[0]["files"], ["formula.md", "context.md", "input.md"])
        self.assertEqual(steps[0]["section"], "Коротко")
        self.assertEqual(steps[1]["title"], "Ресёрч под проблему")
        self.assertEqual(steps[1]["section"], ["Проблема в данных", "Что уже есть у нас", "Конкуренты"])
        self.assertEqual(steps[6]["files"], ["card.md", "details.md"])
        self.assertEqual(steps[6]["section"], ["Ответ на гипотезу", "Не знаем"])
        self.assertNotIn("result", steps[2])  # шаг next итога не показывает

    def test_section_fills_result(self):
        steps = data_of(self.render())[0]["steps"]
        self.assertEqual(steps[0]["result"], "- **Что хочу понять:** стоит ли строить свопы.")
        # два раздела — под своими заголовками, из card.md, а не из одноимённого места у сборщика
        self.assertEqual(steps[1]["result"], "### Кто уже это делает\n\n- Свопы есть у двух соседей.\n\n"
                                             "### Боль автора: что показал сбор\n\nНе подтвердилась.")
        self.assertNotIn("result", steps[2])  # шаг идёт: итога ещё нет

    def test_own_result_wins_and_missing_section_gives_none(self):
        status = json.loads(json.dumps(STATUS))
        status["steps"][0]["result"] = "Свой итог."
        status["steps"][1]["section"] = "Нет такого раздела"
        (self.run / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        steps = data_of(self.render())[0]["steps"]
        self.assertEqual(steps[0]["result"], "Свой итог.")
        self.assertNotIn("result", steps[1])

    def test_question_items_reach_page(self):
        # вопросы списком: страница рисует каждый строкой, метки дают заголовки групп
        items = ["[Расхождение] В описании «~35%», в расчёте 12,63%. Какое число верно? Например: «12,63%».",
                 "[Вопрос] Кто платит за обмен? Например: «покупатель, 1% сверху»."]
        status = {**STATUS, "question": {"id": "q-1", "step": "formula", "text": "Формула верна?",
                                         "items": items, "asked": "20:05"}}
        (self.run / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        data, raw = data_of(self.render())
        self.assertEqual(data["question"]["items"], items)
        self.assertEqual(data["question"]["text"], "Формула верна?")
        self.assertIn("Кто платит за обмен?", raw)
        tpl = ui.TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("q.items", tpl)
        self.assertIn("Расхождения", tpl)

    def test_check_md_counts(self):
        (self.run / "check.md").write_text(
            "# Проверки прогона\n\n## Сверка цитат\n\nЦитат: 5. Нашлись: 5. Зачёркнуты: 0.\n- мелочь\n",
            encoding="utf-8")
        counts = data_of(self.render())[0]["counts"]
        self.assertEqual(counts["quotes"], "Цитат: 5. Нашлись: 5. Зачёркнуты: 0.")


if __name__ == "__main__":
    unittest.main()

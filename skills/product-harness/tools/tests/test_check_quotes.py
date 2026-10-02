"""check_quotes.py: сверка цитат со снимками."""
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_quotes as cq  # noqa: E402

SNAP_TEXT = "Я плачу “двадцать долларов” в месяц за переводы — и это дорого. Другие ничего не платят."
EVIDENCE = """# Сбор: I want to переводить деньги

## Деньги
- Перевод — $20 в месяц. «Я плачу "двадцать долларов" в месяц за переводы» — [Форум](https://forum.example/t/1), 12.03.2025, src-001
- Выдумка. «бесплатно навсегда и без комиссии» — [Форум](https://forum.example/t/1), без даты, src-001
- Нет снимка. «цитата без снимка» — [Блог](https://blog.example/p), без даты, src-009
- Без номера. «цитата без номера снимка» — [Блог](https://blog.example/p), без даты
- Слова автора «в один клик» без ссылки — это не строка цитаты.
"""


class CheckQuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "sources").mkdir()
        (self.run / "evidence").mkdir()
        (self.run / "sources" / "src-001.txt").write_text(SNAP_TEXT, encoding="utf-8")
        (self.run / "sources" / "src-001.yaml").write_text("id: src-001\nsnapshot_kind: html\n", encoding="utf-8")
        self.ev = self.run / "evidence" / "job-1.md"
        self.ev.write_text(EVIDENCE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_statuses_and_typography(self):
        rows = cq.check_run(self.run)
        self.assertEqual([(q.quote, st) for q, st, _ in rows], [
            ('Я плачу "двадцать долларов" в месяц за переводы', "found"),
            ("бесплатно навсегда и без комиссии", "missing"),
            ("цитата без снимка", "no_snapshot"),
            ("цитата без номера снимка", "no_snapshot"),
        ])

    def test_bad_quotes_are_struck_once(self):
        cq.check_run(self.run)
        cq.check_run(self.run)
        text = self.ev.read_text(encoding="utf-8")
        self.assertIn("~~«бесплатно навсегда и без комиссии»~~", text)
        self.assertIn("~~«цитата без снимка»~~", text)
        self.assertIn("~~«цитата без номера снимка»~~", text)
        self.assertNotIn("~~~~", text)
        self.assertIn("— $20 в месяц. «Я плачу", text)  # найденная цитата не тронута
        self.assertIn("Слова автора «в один клик» без ссылки", text)

    def test_struck_quotes_are_parsed_as_struck(self):
        qs = cq.parse_quotes("~~«старое»~~ — [a](https://a.example), без даты, src-001")
        self.assertEqual([(q.quote, q.struck) for q in qs], [("старое", True)])

    def test_model_extract_is_weak(self):
        (self.run / "sources" / "src-001.yaml").write_text("id: src-001\nsnapshot_kind: model_extract\n",
                                                           encoding="utf-8")
        rows = cq.check_run(self.run)
        self.assertTrue(rows[0][2])

    def test_card_md_is_checked(self):
        card = self.run / "card.md"
        card.write_text(
            "- «другие ничего не платят» — [Форум](https://forum.example/t/1), без даты, src-001\n"
            "- «выдумка карточки» — [Форум](https://forum.example/t/1), без даты, src-001\n", encoding="utf-8")
        rows = [(q.file, st) for q, st, _ in cq.check_run(self.run) if q.file == "card.md"]
        self.assertEqual(rows, [("card.md", "found"), ("card.md", "missing")])
        text = card.read_text(encoding="utf-8")
        self.assertIn("~~«выдумка карточки»~~", text)
        self.assertNotIn("~~«другие ничего не платят»~~", text)
        cq.main(["--run", str(self.run)])
        self.assertIn("Цитат: 6. Нашлись: 2.", (self.run / "check.md").read_text(encoding="utf-8"))

    def test_details_md_is_checked(self):
        details = self.run / "details.md"
        details.write_text(
            "- «другие ничего не платят» — [Форум](https://forum.example/t/1), без даты, src-001\n"
            "- «выдумка подробностей» — [Форум](https://forum.example/t/1), без даты, src-001\n", encoding="utf-8")
        rows = [(q.file, st) for q, st, _ in cq.check_run(self.run) if q.file == "details.md"]
        self.assertEqual(rows, [("details.md", "found"), ("details.md", "missing")])
        self.assertIn("~~«выдумка подробностей»~~", details.read_text(encoding="utf-8"))

    def test_check_md_section_is_replaced_not_duplicated(self):
        (self.run / "check.md").write_text("# Проверки прогона\n\n## Проверка карты, круг 1\n\nНарушений: 0.\n",
                                           encoding="utf-8")
        cq.main(["--run", str(self.run)])
        cq.main(["--run", str(self.run)])
        md = (self.run / "check.md").read_text(encoding="utf-8")
        self.assertEqual(md.count("## Сверка цитат"), 1)
        self.assertIn("## Проверка карты, круг 1", md)
        self.assertIn("Цитат: 4. Нашлись: 1.", md)

    def _write_ev(self, name, text):
        f = self.run / "evidence" / name
        f.write_text(text, encoding="utf-8")
        return f

    def test_strike_only_on_the_bad_quote_line(self):
        f = self._write_ev("job-2.md",
                           "- Есть. «это дорого» — [Форум](https://forum.example/t/1), без даты, src-001\n"
                           "- Выдумка. «это дорого» — [Блог](https://blog.example/p), без даты, src-009\n")
        cq.check_run(self.run)
        first, second = f.read_text(encoding="utf-8").splitlines()
        self.assertTrue(first.startswith("- Есть. «это дорого» — "))
        self.assertTrue(second.startswith("- Выдумка. ~~«это дорого»~~ — "))

    def test_prose_with_same_text_is_not_struck(self):
        f = self._write_ev("job-2.md",
                           "Люди пишут, что это «дорого».\n"
                           "- Выдумка. «дорого» — [Блог](https://blog.example/p), без даты, src-009\n"
                           "Люди пишут «дорого» и «дорого» — [Блог](https://blog.example/p), без даты, src-009\n")
        cq.check_run(self.run)
        prose, quote_line, mixed = f.read_text(encoding="utf-8").splitlines()
        self.assertEqual(prose, "Люди пишут, что это «дорого».")
        self.assertIn("~~«дорого»~~ — ", quote_line)
        self.assertTrue(mixed.startswith("Люди пишут «дорого» и ~~«дорого»~~ — "))

    def test_dash_variants_are_parsed_and_checked(self):
        f = self._write_ev("job-2.md",
                           "- «выдумка раз» – [Форум](https://forum.example/t/1), без даты, src-001\n"
                           "- «выдумка два» —[Форум](https://forum.example/t/1), без даты, src-001\n"
                           "- «выдумка три» - [Форум](https://forum.example/t/1), без даты, src-001\n")
        rows = [(q.quote, st) for q, st, _ in cq.check_run(self.run) if q.file == "evidence/job-2.md"]
        self.assertEqual(rows, [("выдумка раз", "missing"), ("выдумка два", "missing"),
                                ("выдумка три", "missing")])
        text = f.read_text(encoding="utf-8")
        self.assertIn("- ~~«выдумка раз»~~ – [", text)
        self.assertIn("- ~~«выдумка два»~~ —[", text)
        self.assertIn("- ~~«выдумка три»~~ - [", text)

    def test_inner_guillemets_is_format_error_struck_whole(self):
        bad = "- «Он сказал «привет» и ушёл» — [Форум](https://forum.example/t/1), без даты, src-001"
        f = self._write_ev("job-2.md", bad + "\n")
        rows = [(st, q.line) for q, st, _ in cq.check_run(self.run) if q.file == "evidence/job-2.md"]
        self.assertEqual(rows, [("format", 1)])
        self.assertEqual(f.read_text(encoding="utf-8"), "- ~~" + bad[2:] + "~~\n")

    def test_format_error_is_reported_once_and_rerun_changes_nothing(self):
        bad = "* «Он сказал «привет» и ушёл» — [Форум](https://forum.example/t/1), без даты, src-001"
        f = self._write_ev("job-2.md", bad + "\n")
        cq.main(["--run", str(self.run)])
        md1 = (self.run / "check.md").read_text(encoding="utf-8")
        self.assertIn("Цитат: 5. Нашлись: 1. Неточно: 0. Зачёркнуты: 3. Ошибок формата: 1. Из выписок модели: 0.", md1)
        self.assertIn("- ошибка формата: evidence/job-2.md:1 src-001 «", md1)
        text1 = f.read_text(encoding="utf-8")
        cq.main(["--run", str(self.run)])
        self.assertEqual(f.read_text(encoding="utf-8"), text1)
        self.assertNotIn("~~~~", text1)
        md2 = (self.run / "check.md").read_text(encoding="utf-8")
        self.assertIn("Цитат: 5. Нашлись: 1. Неточно: 0. Зачёркнуты: 4. Ошибок формата: 0. Из выписок модели: 0.", md2)
        self.assertIn("- зачёркнута раньше: evidence/job-2.md:1 src-001 «", md2)
        self.assertNotIn("ошибка формата", md2)

    def _status_for_snapshot(self, snapshot, quote):
        """Статус цитаты `quote` против снимка src-002 с текстом `snapshot`."""
        (self.run / "sources" / "src-002.txt").write_text(snapshot, encoding="utf-8")
        self._write_ev("job-3.md", f"- «{quote}» — [Форум](https://forum.example/t/2), без даты, src-002\n")
        return [st for q, st, _ in cq.check_run(self.run) if q.file == "evidence/job-3.md"]

    def test_yo_in_snapshot_matches_ye_in_quote(self):
        self.assertEqual(self._status_for_snapshot("Всё это стоит дорого, а зарплата — четыре тысячи.",
                                                   "все это стоит дорого"), ["found"])

    def test_invisible_format_characters_in_snapshot_are_dropped(self):
        for ch in ("\u00ad", "\u200b", "\u200d", "\ufeff"):
            with self.subTest(char=f"U+{ord(ch):04X}"):
                self.assertEqual(self._status_for_snapshot(f"Переводы за пере{ch}воды стоят дорого.",
                                                           "переводы за переводы стоят дорого"), ["found"])

    def test_nbsp_in_snapshot_matches_plain_space_in_quote(self):
        self.assertEqual(self._status_for_snapshot("Переводы\u00a0стоят\u00a0дорого для всех.",
                                                   "переводы стоят дорого"), ["found"])


INPUT = "Хотим свопы прямо в виджете.\nМы теряем ~35% пользователей\nна экране адреса. Всё «очень» дорого.\n"
ANSWERS = "**Вопрос (01.10.2026 10:00, шаг 1):** кто платит?\n**Ответ (10:05):** «платит покупатель, 1% сверху»\n"
FORMULA = """# Формула запроса: свопы в виджете

## Запрос словами автора
Автор: «свопы прямо в виджете». Потери: «теряем ~35% пользователей на экране адреса».

## Формула
- Платит: «платит покупатель» — ответ автора.
- Сейчас решают: «биржа берёт 3% за обмен».
- Цена: «все "очень" дорого».

## Вопросы, которые задали бы человеку
1. Какая цель к дате? Например: «€50 тыс. в месяц к марту».
"""


class FormulaQuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        (self.run / "input.md").write_text(INPUT, encoding="utf-8")
        (self.run / "author-answers.md").write_text(ANSWERS, encoding="utf-8")
        self.formula = self.run / "formula.md"
        self.formula.write_text(FORMULA, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def formula_run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cq.main(["--run", str(self.run), "--formula"])
        return out.getvalue()

    def test_quotes_found_in_input_or_answers(self):
        rows = [(q.quote, st) for q, st in cq.check_formula(self.run)]
        self.assertEqual(rows, [
            ("свопы прямо в виджете", "found"),
            ("теряем ~35% пользователей на экране адреса", "found"),  # перенос строки во входе
            ("платит покупатель", "found"),  # из author-answers.md
            ("биржа берёт 3% за обмен", "missing"),
            ('все "очень" дорого', "found"),  # ё/е и кавычки — та же нормализация
        ])

    def test_missing_quote_is_struck_and_counted_on_own_line(self):
        out = self.formula_run()
        text = self.formula.read_text(encoding="utf-8")
        self.assertIn("- Сейчас решают: ~~«биржа берёт 3% за обмен»~~.", text)
        self.assertIn("Автор: «свопы прямо в виджете».", text)
        self.assertEqual(out.splitlines()[0], "Цитаты формулы: 5. Нашлись: 4. Неточно: 0. Зачёркнуты: 1.")
        self.assertIn("- нет во входе: formula.md:8 «биржа берёт 3% за обмен»", out)

    def test_example_answers_in_questions_are_not_checked(self):
        self.formula_run()
        self.assertIn("Например: «€50 тыс. в месяц к марту».", self.formula.read_text(encoding="utf-8"))

    def test_rerun_counts_struck_once(self):
        self.formula_run()
        out = self.formula_run()
        text = self.formula.read_text(encoding="utf-8")
        self.assertNotIn("~~~~", text)
        self.assertEqual(out.splitlines()[0], "Цитаты формулы: 5. Нашлись: 4. Неточно: 0. Зачёркнуты: 1.")
        self.assertIn("- зачёркнута раньше: formula.md:8 «биржа берёт 3% за обмен»", out)

    def test_formula_mode_leaves_check_md_and_evidence_alone(self):
        (self.run / "evidence").mkdir()
        ev = self.run / "evidence" / "job.md"
        ev.write_text("- «выдумка» — [Форум](https://forum.example/t/1), без даты, src-001\n", encoding="utf-8")
        self.formula_run()
        self.assertFalse((self.run / "check.md").exists())
        self.assertNotIn("~~", ev.read_text(encoding="utf-8"))

    def test_default_mode_leaves_formula_alone(self):
        cq.main(["--run", str(self.run)])
        self.assertEqual(self.formula.read_text(encoding="utf-8"), FORMULA)
        self.assertIn("Цитат: 0. Нашлись: 0.", (self.run / "check.md").read_text(encoding="utf-8"))

    def test_changed_meaning_is_inexact_not_found(self):
        # перевёрнутое отрицание и сдвинутая запятая похожи на вход больше чем на 0,8 — это «неточно»
        (self.run / "input.md").write_text("Покупатель не платит комиссию. Чаевые — 54 тыс. долларов в месяц.\n",
                                           encoding="utf-8")
        self.formula.write_text("## Формула\n- «покупатель платит комиссию»\n- «5,4 тыс. долларов в месяц»\n",
                              encoding="utf-8")
        out = self.formula_run()
        self.assertEqual(out.splitlines()[0], "Цитаты формулы: 2. Нашлись: 0. Неточно: 2. Зачёркнуты: 0.")
        self.assertIn("- неточно: formula.md:2 «покупатель платит комиссию»", out)
        self.assertIn("- неточно: formula.md:3 «5,4 тыс. долларов в месяц»", out)

    def test_only_human_answers_count_as_author_words(self):
        # пример ответа из вопроса сессии — не слова автора
        (self.run / "author-answers.md").write_text(
            "**Вопрос (01.10.2026 10:00, шаг 1):** Кто платит? Например: «покупатель, 1% сверху».\n"
            "**Ответ (10:05):** «не знаю, решает партнёр»\n", encoding="utf-8")
        self.formula.write_text("## Формула\n- Платит: «покупатель, 1% сверху».\n"
                              "- Решает о покупке: «решает партнёр».\n", encoding="utf-8")
        rows = [(q.quote, st) for q, st in cq.check_formula(self.run)]
        self.assertEqual(rows, [("покупатель, 1% сверху", "missing"), ("решает партнёр", "found")])

    def test_formula_mode_works_without_yaml(self):
        # формула идёт до установки пакетов: её сверке yaml не нужен
        code = ("import sys; sys.modules['yaml'] = None; sys.path.insert(0, sys.argv[1]); "
                "import check_quotes; check_quotes.main(['--run', sys.argv[2], '--formula'])")
        tools = str(Path(__file__).resolve().parents[1])
        r = subprocess.run([sys.executable, "-c", code, tools, str(self.run)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Цитаты формулы: 5.", r.stdout)


if __name__ == "__main__":
    unittest.main()

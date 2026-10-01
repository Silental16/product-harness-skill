"""snap.py: снимки страниц прогона."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SNAP = Path(__file__).resolve().parents[1] / "snap.py"
LONG = "Люди платят за перевод денег за границу около двадцати долларов в месяц. " * 8


def page(dir_, name, body):
    p = Path(dir_) / name
    p.write_text(f"<html><head><title>t</title></head><body><p>{body}</p></body></html>", encoding="utf-8")
    return p.as_uri()


def snap(run, cmd, *args):
    return subprocess.run([sys.executable, str(SNAP), cmd, "--run", str(run), *args],
                          capture_output=True, text=True)


class SnapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root / "run"
        self.run.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_fetch_saves_text_and_meta(self):
        url = page(self.root, "a.html", LONG)
        r = snap(self.run, "fetch", "--url", url, "--title", "Страница А", "--agent", "collect-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("src-001"), r.stdout)
        text = (self.run / "sources" / "src-001.txt").read_text(encoding="utf-8")
        self.assertIn("двадцати долларов", text)
        meta = yaml.safe_load((self.run / "sources" / "src-001.yaml").read_text(encoding="utf-8"))
        self.assertEqual((meta["id"], meta["url"], meta["snapshot_kind"], meta["agent"]),
                         ("src-001", url, "html", "collect-1"))

    def test_ids_skip_taken_numbers(self):
        (self.run / "sources").mkdir()
        (self.run / "sources" / "src-001.yaml").write_text("", encoding="utf-8")  # номер занял другой агент
        r = snap(self.run, "fetch", "--url", page(self.root, "a.html", LONG), "--title", "А")
        self.assertTrue(r.stdout.startswith("src-002"), r.stdout)

    def test_short_page_fails_and_frees_number(self):
        r = snap(self.run, "fetch", "--url", page(self.root, "s.html", "мало"), "--title", "S")
        self.assertEqual(r.returncode, 2)
        self.assertIn("fail", r.stdout)
        self.assertEqual(list((self.run / "sources").glob("src-*")), [])
        r = snap(self.run, "fetch", "--url", page(self.root, "a.html", LONG), "--title", "А")
        self.assertTrue(r.stdout.startswith("src-001"), r.stdout)

    def test_missing_file_fails(self):
        r = snap(self.run, "fetch", "--url", (self.root / "nope.html").as_uri(), "--title", "N")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(list((self.run / "sources").glob("src-*")), [])

    def test_find_exit_codes(self):
        snap(self.run, "fetch", "--url", page(self.root, "a.html", LONG), "--title", "А")
        self.assertEqual(snap(self.run, "find", "src-001", "около двадцати долларов в месяц").returncode, 0)
        self.assertEqual(snap(self.run, "find", "src-001", "бесплатно навсегда и без комиссии").returncode, 1)
        self.assertEqual(snap(self.run, "find", "src-099", "что угодно").returncode, 1)

    def test_save_marks_model_extract(self):
        f = self.root / "seen.txt"
        f.write_text("Видимый текст страницы, прочитанный агентом.", encoding="utf-8")
        r = snap(self.run, "save", "--url", "https://example.com/x", "--title", "X", "--from-file", str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        meta = yaml.safe_load((self.run / "sources" / "src-001.yaml").read_text(encoding="utf-8"))
        self.assertEqual(meta["snapshot_kind"], "model_extract")


if __name__ == "__main__":
    unittest.main()

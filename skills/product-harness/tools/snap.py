#!/usr/bin/env python3
"""Снимки страниц прогона.

  fetch — скачать страницу curl-ом и сохранить её текст
  save  — сохранить текст, который агент прочитал своим инструментом (выписка модели)
  find  — есть ли цитата в тексте снимка: код 0 — нашлась, 1 — нет

Папка прогона — ключ --run после подкоманды:
  snap.py fetch --run RUN --url URL --title "Заголовок" [--published ГГГГ-ММ-ДД] [--agent имя]
  snap.py save  --run RUN --url URL --title "Заголовок" --from-file файл [--published ...] [--agent ...]
  snap.py find  --run RUN src-NNN "цитата"
Номера снимков идут подряд: src-001, src-002, …"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import match_quote  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
STUBS = ("captcha", "just a moment", "cf-browser-verification", "access denied", "enable javascript")
BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
              "table", "section", "article", "header", "footer", "nav", "aside", "main", "blockquote",
              "pre", "dl", "dt", "dd", "figure", "figcaption", "form", "br", "hr", "title"]
MIN_CHARS, STUB_CHARS = 300, 2000


def clean(s):
    lines = (re.sub(r"\s+", " ", ln).strip() for ln in s.splitlines())
    return "\n".join(ln for ln in lines if ln)


def html_text(data):
    soup = BeautifulSoup(data, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    for t in soup.find_all(BLOCK_TAGS):  # перенос вокруг блоков, строчные теги склеиваются
        t.insert_before("\n")
        t.insert_after("\n")
    return clean(soup.get_text())


def fail_exit(reason, url="", paths=()):
    for p in paths:
        p.unlink(missing_ok=True)
    print(f"fail: {reason}  {url}".rstrip())
    sys.exit(2)


def claim_id(src):
    """Первый свободный номер. O_EXCL: два агента не займут один номер."""
    src.mkdir(parents=True, exist_ok=True)
    for n in range(1, 10000):
        meta = src / f"src-{n:03d}.yaml"
        try:
            os.close(os.open(meta, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            return f"src-{n:03d}", meta
        except FileExistsError:
            continue
    fail_exit("нет свободных номеров")


def write_meta(meta, a, sid, kind, chars):
    rec = {"id": sid, "url": a.url, "title": a.title, "accessed_at": date.today().isoformat(),
           "published_at": a.published, "snapshot_kind": kind, "chars": chars, "agent": a.agent}
    meta.write_text(yaml.safe_dump(rec, allow_unicode=True, sort_keys=False), encoding="utf-8")


def fetch(a, run):
    src = run / "sources"
    sid, meta = claim_id(src)
    part, txt = src / f"{sid}.part", src / f"{sid}.txt"
    r = subprocess.run(["curl", "-sL", "--max-time", "25", "--compressed", "-A", UA,
                        "-H", "Accept-Language: en-US,en;q=0.9", "-o", str(part),
                        "-w", "%{http_code}\t%{content_type}", a.url], capture_output=True, text=True)
    code, _, ctype = r.stdout.partition("\t")
    status = int(code) if code.isdigit() else 0
    body = part.read_bytes() if part.exists() else b""
    is_pdf = "pdf" in ctype.lower() or body[:5] == b"%PDF-"
    raw = src / f"{sid}.{'pdf' if is_pdf else 'html'}"
    if part.exists():
        part.rename(raw)
    trash = (meta, part, raw, txt)
    if r.returncode:
        fail_exit(f"curl exit {r.returncode}", a.url, trash)
    if status >= 400:
        fail_exit(f"http {status}", a.url, trash)
    if not is_pdf and ctype and not re.search(r"html|xml|text|json", ctype, re.I):
        fail_exit(f"content-type {ctype}", a.url, trash)
    if is_pdf:
        if not shutil.which("pdftotext"):
            fail_exit("нет pdftotext", a.url, trash)
        subprocess.run(["pdftotext", "-enc", "UTF-8", str(raw), str(txt)], capture_output=True)
        text = clean(txt.read_text(encoding="utf-8", errors="replace")) if txt.exists() else ""
    else:
        text = html_text(body)
        low = text.lower() + body[:5000].decode("utf-8", "replace").lower()
        stub = next((m for m in STUBS if m in low), None) if len(text) < STUB_CHARS else None
        if stub:
            fail_exit(f"заглушка: {stub}", a.url, trash)
    if len(text) < MIN_CHARS:
        fail_exit("короткий текст", a.url, trash)
    txt.write_text(text, encoding="utf-8")
    write_meta(meta, a, sid, "pdf" if is_pdf else "html", len(text))
    print(f"{sid}  {len(text)} chars  sources/{txt.name}")


def save(a, run):
    text = clean(Path(a.from_file).read_text(encoding="utf-8", errors="replace"))
    if not text:
        fail_exit(f"пустой файл {a.from_file}")
    sid, meta = claim_id(run / "sources")
    (run / "sources" / f"{sid}.txt").write_text(text, encoding="utf-8")
    write_meta(meta, a, sid, "model_extract", len(text))
    print(f"{sid}  {len(text)} chars  sources/{sid}.txt  (выписка модели)")


def find(a, run):
    p = run / "sources" / f"{a.id}.txt"
    if not p.exists():
        print("нет снимка")
        sys.exit(1)
    st, score, ctx = match_quote(a.quote, p.read_text(encoding="utf-8"))
    if st == "yes":
        print(f"found\n  …{ctx}…")
        return
    print(f"{'partial' if st == 'partial' else 'not found'} {score:.2f}\n  ближе всего: {ctx}")
    sys.exit(1)


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run", required=True, help="папка прогона")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "save"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--url", required=True)
        p.add_argument("--title", required=True)
        p.add_argument("--published", help="ГГГГ-ММ-ДД, если дата есть на странице")
        p.add_argument("--agent", help="кто снимал")
        if name == "save":
            p.add_argument("--from-file", required=True, help="файл с видимым текстом страницы")
    p = sub.add_parser("find", parents=[common])
    p.add_argument("id", help="src-NNN")
    p.add_argument("quote")
    a = ap.parse_args(argv)
    run = Path(a.run).resolve()
    if not run.is_dir():
        sys.exit(f"нет папки прогона {run}")
    {"fetch": fetch, "save": save, "find": find}[a.cmd](a, run)


if __name__ == "__main__":
    main()

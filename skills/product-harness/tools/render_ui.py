#!/usr/bin/env python3
"""Страница прогона: данные прогона в шаблон ui/page.html.

Читает RUN/status.json, который ведёт главная сессия, добавляет к нему тексты файлов
шагов, строки notes.md, счёт из evidence/*.md и check.md и сам шаблон. Кладёт всё одним
JSON в шаблон и пишет локальную страницу: её дают человеку, когда стенд не отвечает.
Те же данные без шаблона push_run.py шлёт на стенд.

  render_ui.py --run RUN [--out PATH]      по умолчанию PATH = RUN/ui.html"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "ui" / "page.html"
# Каркас самопубликации: так инструмент Artifact оборачивает страницу. Та же строка
# стоит в page.html — по ней инструмент узнаёт каркас и не вкладывает его второй раз.
HEAD = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport '
        'content="width=device-width,initial-scale=1,viewport-fit=cover"><style>:root{color-scheme:light;'
        'box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'html{scroll-padding-top:env(safe-area-inset-top,0px)}body{margin:0;padding:0;font:14px -apple-system,'
        'BlinkMacSystemFont,sans-serif;background:#faf9f5;color:#141413}img{max-width:100%}'
        '[hidden]:not([hidden=until-found i]){display:none!important}</style></head><body>\n')
TAIL = "\n</body></html>"
SLOT = re.compile(r"\{\{(RUN_TITLE|RUN_DATA)\}\}")
NOTE_RE = re.compile(r"^(\d\d\.\d\d) (\d\d:\d\d) — ([^—\n]+?) — (.+)$")
META_RE = re.compile(r"^(?:\d[\d\s]*|неизвестно)$")
ROUND_RE = re.compile(r"^## Проверка карты, круг (\d+)\s*$", re.M)
VIOL_RE = re.compile(r"^Нарушений: (\d+)(?:, из них спорно: (\d+))?.*$", re.M)
# Шаги прогона по SKILL.md: id, название, файл. status.json без "steps" получает их со статусом next.
STEPS = [("brief", "Бриф", "brief.md"), ("collect", "Сбор", None), ("check", "Сверка цитат", "check.md"),
         ("map", "Карта сегментов", "segments.md"), ("review", "Проверка карты", "check.md"),
         ("card", "Карточка решения", "card.md")]


def default_steps():
    return [{"id": i, "n": n, "title": t, "status": "next", **({"file": f} if f else {})}
            for n, (i, t, f) in enumerate(STEPS)]


def step_files(step):
    return list(step.get("files") or ([step["file"]] if step.get("file") else []))


def section(md, title):
    """Текст раздела «## title» до следующего «## »."""
    m = re.search(r"^## " + re.escape(title) + r"\s*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def timeline(notes):
    """Строки «ДД.ММ ЧЧ:ММ — шаг N — что — модель — минуты — токены»; хвост из трёх полей — если есть."""
    out = []
    for ln in notes.splitlines():
        m = NOTE_RE.match(ln.strip())
        if not m:
            continue
        date, time, label, rest = m.groups()
        step = re.match(r"шаг (\d+)", label)
        row = {"date": date, "time": time, "label": label, "step": int(step.group(1)) if step else None,
               "text": rest, "model": None, "minutes": None, "tokens": None}
        parts = rest.split(" — ")
        if len(parts) >= 4 and META_RE.match(parts[-1]) and META_RE.match(parts[-2]):
            row.update(text=" — ".join(parts[:-3]), model=parts[-3], minutes=parts[-2], tokens=parts[-1])
        out.append(row)
    return out


def counts(run):
    ev = {}
    for f in sorted((run / "evidence").glob("*.md")):
        line = next((ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("Счёт:")), None)
        if line:
            ev[f.relative_to(run).as_posix()] = line
    chk = run / "check.md"
    md = chk.read_text(encoding="utf-8") if chk.exists() else ""
    quotes = next((ln for ln in section(md, "Сверка цитат").splitlines() if ln.startswith("Цитат:")), None)
    reviews = []
    for m in ROUND_RE.finditer(md):
        v = VIOL_RE.search(section(md, f"Проверка карты, круг {m.group(1)}"))
        if v:
            reviews.append({"round": int(m.group(1)), "line": v.group(0), "violations": int(v.group(1)),
                            "disputed": int(v.group(2) or 0)})
    return {"evidence": ev, "quotes": quotes, "reviews": reviews}


def to_json(obj):
    """JSON, который нельзя закрыть изнутри тега <script>: каждый «<» — \\u003c."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def fill(template, data):
    vals = {"RUN_TITLE": html.escape(data.get("title") or data.get("run") or "Прогон"), "RUN_DATA": to_json(data)}
    return SLOT.sub(lambda m: vals[m.group(1)], template)


def build_data(run):
    """Данные страницы прогона без шаблона; их же push_run.py шлёт на стенд."""
    status = json.loads((run / "status.json").read_text(encoding="utf-8"))
    status["steps"] = status.get("steps") or default_steps()
    names = [f for s in status.get("steps", []) for f in step_files(s)]
    files = {f: (run / f).read_text(encoding="utf-8") for f in dict.fromkeys(names) if (run / f).is_file()}
    brief = files.get("brief.md") or ((run / "brief.md").read_text(encoding="utf-8")
                                      if (run / "brief.md").is_file() else "")
    notes = run / "notes.md"
    return {**status,
            "idea": status.get("idea") or section(brief, "Идея словами автора"),
            "files": files,
            "timeline": timeline(notes.read_text(encoding="utf-8")) if notes.exists() else [],
            "counts": counts(run),
            "links": {}}


def build(run):
    template = TEMPLATE.read_text(encoding="utf-8")
    return HEAD + fill(template, {**build_data(run), "template": template}) + TAIL


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="папка прогона")
    ap.add_argument("--out", help="куда писать страницу, по умолчанию RUN/ui.html")
    a = ap.parse_args(argv)
    run = Path(a.run).resolve()
    if not (run / "status.json").is_file():
        sys.exit(f"нет {run / 'status.json'}")
    out = Path(a.out) if a.out else run / "ui.html"
    out.write_text(build(run), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

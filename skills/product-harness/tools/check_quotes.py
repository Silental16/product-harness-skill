#!/usr/bin/env python3
"""Сверка цитат прогона со снимками страниц.

Находит в evidence/*.md и card.md строки цитат вида
  «цитата» — [название](URL), дата, src-NNN
и ищет каждую цитату в sources/src-NNN.txt. Цитату без снимка или без совпадения
зачёркивает в файле, только в её строке: ~~«цитата»~~. Между цитатой и ссылкой стоит
тире — длинное, среднее или дефис. Строку с src-NNN, где цитату разобрать не вышло
(например, «кавычки» внутри цитаты), считает ошибкой формата и зачёркивает целиком.
Итог пишет в check.md, раздел «Сверка цитат».

С --formula сверяет только formula.md: каждую «цитату» вне раздела вопросов (там «» —
пример ответа) ищет в input.md и в ответах автора из author-answers.md, без вопросов
сессии. Ненайденную зачёркивает, итог печатает отдельной строкой «Цитаты формулы: …»;
check.md и файлы других шагов не трогает. Этому режиму пакет yaml не нужен.

  check_quotes.py --run RUN [--formula]"""
import argparse
import re
import sys
from collections import Counter, namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import match_quote  # noqa: E402

DASH = r"\s*[—–-]\s*"
QUOTE_RE = re.compile(r"(?P<pre>~~)?«(?P<quote>[^»\n]+)»(?:~~)?" + DASH + r"\[[^\]\n]*\]\((?P<url>[^)\s]+)\)"
                      r"(?P<tail>[^«\n]*)")
SRC_RE = re.compile(r"\bsrc-\d{3,}\b")
LEAD_RE = re.compile(r"\s*(?:[-*+]\s+)?")
SECTION = "Сверка цитат"
LABEL = {"missing": "нет в снимке", "no_snapshot": "нет снимка", "inexact": "неточно",
         "struck": "зачёркнута раньше", "format": "ошибка формата"}
Quote = namedtuple("Quote", "file line quote url src struck")
FORMULA_QUOTE_RE = re.compile(r"(?P<pre>~~)?«(?P<quote>[^»\n]+)»")
FORMULA_SKIP = "## Вопросы"
ANSWER_RE = re.compile(r"^\*\*Ответ[^\n]*?:\*\*(.*?)(?=^\*\*Вопрос|\Z)", re.S | re.M)
FORMULA_LABEL = {"missing": "нет во входе", "inexact": "неточно", "struck": "зачёркнута раньше"}


def scan(text, file=""):
    """[(Quote, ошибка_формата)]. Строка с src-NNN без разобранной цитаты — ошибка формата:
    в Quote.quote лежит текст строки без маркера списка, struck — зачёркнута ли она целиком."""
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        found = False
        for m in QUOTE_RE.finditer(ln):
            found = True
            src = SRC_RE.search(m.group("tail"))
            out.append((Quote(file, i, m.group("quote"), m.group("url"),
                              src.group(0) if src else None, bool(m.group("pre"))), False))
        src = SRC_RE.search(ln)
        if src and not found:
            body = ln[LEAD_RE.match(ln).end():].strip()
            out.append((Quote(file, i, body, None, src.group(0),
                              len(body) > 4 and body.startswith("~~") and body.endswith("~~")), True))
    return out


def parse_quotes(text, file=""):
    """Все строки цитат файла, включая зачёркнутые (struck=True)."""
    return [q for q, bad_format in scan(text, file) if not bad_format]


def status_of(run, q):
    """(found | inexact | missing | no_snapshot | struck, снимок — выписка модели)."""
    if q.struck:
        return "struck", False
    if not q.src:
        return "no_snapshot", False
    txt = run / "sources" / f"{q.src}.txt"
    if not txt.exists():
        return "no_snapshot", False
    import yaml  # здесь, а не наверху: сверке формулы на шаге 1 пакет не нужен

    meta = run / "sources" / f"{q.src}.yaml"
    info = yaml.safe_load(meta.read_text(encoding="utf-8")) if meta.exists() else None
    weak = isinstance(info, dict) and info.get("snapshot_kind") == "model_extract"
    st, _, _ = match_quote(q.quote, txt.read_text(encoding="utf-8"))
    return {"yes": "found", "partial": "inexact"}.get(st, "missing"), weak


def strike(line, quote):
    """Зачеркнуть в строке одну «цитату» — ту, за которой идёт ссылка и которая ещё не зачёркнута."""
    pat = r"(?<!~~)«" + re.escape(quote) + r"»(?!~~)(?=" + DASH + r"\[)"
    return re.sub(pat, lambda m: "~~" + m.group(0) + "~~", line, count=1)


def strike_line(line):
    """Зачеркнуть строку целиком, кроме маркера списка, пробелов по краям и конца строки."""
    content = (line.splitlines() or [""])[0]
    lead = LEAD_RE.match(content).group(0)
    rest = content[len(lead):]
    body = rest.rstrip()
    return lead + "~~" + body + "~~" + rest[len(body):] + line[len(content):]


def check_run(run):
    files = sorted((run / "evidence").glob("*.md"))
    files += [run / "card.md"] if (run / "card.md").exists() else []
    rows = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        lines, changed = text.splitlines(keepends=True), False
        for q, bad_format in scan(text, f.relative_to(run).as_posix()):
            if bad_format:
                st, weak = ("struck" if q.struck else "format"), False
            else:
                st, weak = status_of(run, q)
            rows.append((q, st, weak))
            if st == "format":
                lines[q.line - 1], changed = strike_line(lines[q.line - 1]), True
            elif st in ("missing", "no_snapshot"):
                lines[q.line - 1], changed = strike(lines[q.line - 1], q.quote), True
        if changed:
            f.write_text("".join(lines), encoding="utf-8")
    return rows


def render(rows):
    n = Counter(st for _, st, _ in rows)
    struck = n["missing"] + n["no_snapshot"] + n["struck"]
    weak = sum(1 for _, _, w in rows if w)
    lines = [f"Цитат: {len(rows)}. Нашлись: {n['found']}. Неточно: {n['inexact']}. "
             f"Зачёркнуты: {struck}. Ошибок формата: {n['format']}. Из выписок модели: {weak}."]
    for q, st, _ in rows:
        if st in LABEL:
            lines.append(f"- {LABEL[st]}: {q.file}:{q.line} {q.src or 'без номера снимка'} «{q.quote[:80]}»")
    return "\n".join(lines)


def check_formula(run):
    """[(Quote, found | inexact | missing | struck)] для цитат formula.md; ненайденные зачёркивает в файле."""
    formula = run / "formula.md"
    texts = [(run / "input.md").read_text(encoding="utf-8")] if (run / "input.md").is_file() else []
    if (run / "author-answers.md").is_file():  # слова автора — только ответы, без вопросов сессии
        texts += ANSWER_RE.findall((run / "author-answers.md").read_text(encoding="utf-8"))
    lines, rows, skip = formula.read_text(encoding="utf-8").splitlines(keepends=True), [], False
    for i, ln in enumerate(lines, 1):
        if ln.startswith("## "):
            skip = ln.startswith(FORMULA_SKIP)
        if skip:
            continue
        for m in FORMULA_QUOTE_RE.finditer(ln):
            q = Quote("formula.md", i, m.group("quote"), None, None, bool(m.group("pre")))
            found = set() if q.struck else {match_quote(q.quote, t)[0] for t in texts}
            st = ("struck" if q.struck else "found" if "yes" in found
                  else "inexact" if "partial" in found else "missing")
            rows.append((q, st))
    missing = [q for q, st in rows if st == "missing"]
    for q in missing:
        pat = r"(?<!~~)«" + re.escape(q.quote) + r"»(?!~~)"
        lines[q.line - 1] = re.sub(pat, lambda m: "~~" + m.group(0) + "~~", lines[q.line - 1], count=1)
    if missing:
        formula.write_text("".join(lines), encoding="utf-8")
    return rows


def render_formula(rows):
    n = Counter(st for _, st in rows)
    lines = [f"Цитаты формулы: {len(rows)}. Нашлись: {n['found']}. Неточно: {n['inexact']}. "
             f"Зачёркнуты: {n['missing'] + n['struck']}."]
    for q, st in rows:
        if st in FORMULA_LABEL:
            lines.append(f"- {FORMULA_LABEL[st]}: {q.file}:{q.line} «{q.quote[:80]}»")
    return "\n".join(lines)


def upsert_section(md, title, body):
    """Заменить раздел «## title» или дописать его в конец; другие разделы не трогать."""
    block = f"## {title}\n\n{body.rstrip()}\n"
    pat = re.compile(r"^## " + re.escape(title) + r"\n.*?(?=^## |\Z)", re.S | re.M)
    if pat.search(md):
        return pat.sub(lambda m: block + "\n", md, count=1).rstrip() + "\n"
    return (md.rstrip() + "\n\n" if md.strip() else "") + block


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="папка прогона")
    ap.add_argument("--formula", action="store_true", help="только цитаты formula.md против входа и ответов автора")
    a = ap.parse_args(argv)
    run = Path(a.run).resolve()
    if not run.is_dir():
        sys.exit(f"нет папки прогона {run}")
    if a.formula:
        if not (run / "formula.md").is_file():
            sys.exit(f"нет {run / 'formula.md'}")
        print(render_formula(check_formula(run)))
        return
    body = render(check_run(run))
    chk = run / "check.md"
    md = chk.read_text(encoding="utf-8") if chk.exists() else "# Проверки прогона\n"
    chk.write_text(upsert_section(md, SECTION, body), encoding="utf-8")
    print(body.splitlines()[0])


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Отправка прогона на стенд: данные страницы прогона одним POST на /api/push.

Собирает то же, что render_ui.py, без шаблона, и добавляет input.md, author-answers.md
и notes.md. Первый раз стенд заводит прогон и отдаёт ключ записи; ключ ложится в
RUN/.stand.json, и следующие отправки с ним обновляют ту же страницу. Печатает ссылку
страницы. Любая ошибка — одна строка «стенд: …» в stderr и код 1, ключ остаётся прежним.

  push_run.py --run RUN [--stand URL]     адрес: --stand, иначе HARNESS_STAND_URL, иначе STAND"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_ui as ui  # noqa: E402

STAND = "https://product-harness-stand.vercel.app"
KEY = ".stand.json"
EXTRA = ("input.md", "author-answers.md", "notes.md")
LIMIT = 4_000_000  # байт тела; больше стенд не примет
TIMEOUT = 30
AUTHOR_RE = re.compile(r"^Ведёт: (.+?)\. Движок: (.+?), главная модель: (.+?)\.?\s*$", re.M)


class Failure(Exception):
    pass


def stand_url(arg=None):
    return (arg or os.environ.get("HARNESS_STAND_URL") or STAND).rstrip("/")


def state(steps):
    st = [s.get("status") for s in steps]
    if all(x in ("done", "skipped") for x in st):
        return "done"
    return "waiting" if "waiting" in st else "running"


def current(steps):
    """Первый шаг в waiting или running, иначе последний done."""
    live = [s.get("id") for s in steps if s.get("status") in ("waiting", "running")]
    done = [s.get("id") for s in steps if s.get("status") == "done"]
    return (live[:1] or done[-1:] or [None])[0]


def verdict(run):
    card = run / "card.md"
    md = card.read_text(encoding="utf-8") if card.is_file() else ""
    return next((ln.strip() for ln in ui.section(md, "Ответ").splitlines() if ln.strip()), None)


def payload(run):
    data = ui.build_data(run)
    files = data["files"]
    for name in EXTRA:
        if name not in files and (run / name).is_file():
            files[name] = (run / name).read_text(encoding="utf-8")
    m = AUTHOR_RE.search(files.get("notes.md", ""))
    author, engine, model = m.groups() if m else (None, None, None)
    data["author"], data["engine"] = author, engine  # шапка страницы: «Ведёт: …»
    meta = {"run": run.name, "title": data.get("title"), "author": author, "engine": engine, "model": model,
            "step": current(data["steps"]), "state": state(data["steps"]), "verdict": verdict(run)}
    return {"meta": meta, "data": data}


def encode(key, pay):
    body = {"id": key.get("id"), "token": key.get("token"), **pay}
    return json.dumps(body, ensure_ascii=False).encode()


def fit(key, pay):
    """Тело не больше LIMIT байт: из data.files выпадают самые большие evidence/*.md."""
    files = pay["data"]["files"]
    ev = sorted((f for f in files if f.startswith("evidence/") and f.endswith(".md")),
                key=lambda f: len(files[f].encode()))
    body, dropped = encode(key, pay), []
    while len(body) > LIMIT and ev:
        dropped.append(ev.pop())
        del files[dropped[-1]]
        body = encode(key, pay)
    return body, dropped


def post(url, body):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"content-type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read())["error"]
        except (ValueError, KeyError, TypeError):
            reason = e.reason
        if e.code == 403:  # ключ не от этой страницы: повтор не поможет, поможет новая страница
            reason = f"{reason} — удали {KEY} в папке прогона и отправь снова: стенд заведёт новую страницу"
        raise Failure(f"{e.code} {reason}")
    except OSError as e:  # нет сети, отказ, тайм-аут
        raise Failure(f"нет связи с {url}: {getattr(e, 'reason', e)}")
    try:
        resp = json.loads(raw)
    except ValueError:
        resp = None
    if not isinstance(resp, dict):
        raise Failure(f"ответ не JSON: {raw[:200]!r}")
    return resp


def push(run, stand):
    if not (run / "status.json").is_file():
        raise Failure(f"нет {run / 'status.json'}")
    key_file = run / KEY
    try:
        key = json.loads(key_file.read_text(encoding="utf-8")) if key_file.exists() else {}
    except (OSError, ValueError) as e:
        raise Failure(f"не прочитал {key_file}: {e}")
    try:
        pay = payload(run)
    except Exception as e:  # битый status.json и любые другие сбои чтения
        raise Failure(f"не прочитал прогон: {type(e).__name__}: {e}")
    body, dropped = fit(key, pay)
    if dropped:
        limit = f"{LIMIT:,}".replace(",", " ")
        print(f"стенд: не влезли {', '.join(dropped)} — тело больше {limit} байт", file=sys.stderr)
    resp = post(stand + "/api/push", body)
    new = {"id": resp.get("id"), "token": resp.get("token") or key.get("token"), "url": resp.get("url")}
    if not all(new.values()):
        raise Failure(f"в ответе нет id, url или ключа: {sorted(resp)}")
    if new != key:
        tmp = key_file.with_name(KEY + ".tmp")
        try:
            tmp.write_text(json.dumps(new, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, key_file)
        except OSError as e:
            raise Failure(f"страница {new['url']} есть, но ключ не записан в {key_file}: {e}")
    print(new["url"])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="папка прогона")
    ap.add_argument("--stand", help="адрес стенда")
    a = ap.parse_args(argv)
    try:
        return push(Path(a.run).resolve(), stand_url(a.stand))
    except Exception as e:  # любой сбой — одна строка без трассировки, прогон идёт дальше
        msg = str(e) if isinstance(e, Failure) else f"{type(e).__name__}: {e}"
        print("стенд: " + " ".join(msg.split()), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

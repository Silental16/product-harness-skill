"""Сравнение цитаты с текстом снимка. Перенесено из pipeline/run-kit/tools/common.py."""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from heapq import nlargest

PUNCT = " .,;:!?\"'()[]{}<>-*…«»“”"
QUOTES = str.maketrans({**{c: '"' for c in "“”„‟«»"},
                        **{c: "'" for c in "‘’‚‛′`"},
                        **{c: "-" for c in "‐‑‒–—―−"}})


def norm(s):
    s = unicodedata.normalize("NFKC", str(s)).lower().replace("ё", "е")
    s = "".join(c for c in s if unicodedata.category(c) != "Cf").translate(QUOTES)
    return re.sub(r"\s+", " ", s).strip(PUNCT)


def match_quote(quote, text):
    """(yes | partial | no, доля совпадения, контекст). partial — лучшее окно по словам с долей ≥ 0.8."""
    q, t = norm(quote), norm(text)
    if not q:
        return "no", 0.0, ""
    i = t.find(q)
    if i >= 0:
        return "yes", 1.0, t[max(0, i - 80): i + len(q) + 80]
    # окна длиной с цитату: сначала отбираем 30 окон с наибольшим числом общих слов,
    # потом на них считаем посимвольную долю совпадения
    qw, tw = q.split(" "), t.split(" ")
    n = len(qw)
    key = lambda w: w.strip(PUNCT)
    need, have, overlap, cands = Counter(map(key, qw)), Counter(), 0, []
    for j, w in enumerate(tw):
        k = key(w)
        if have[k] < need[k]:
            overlap += 1
        have[k] += 1
        if j >= n:
            k0 = key(tw[j - n])
            have[k0] -= 1
            if have[k0] < need[k0]:
                overlap -= 1
        if j >= n - 1:
            cands.append((overlap, j - n + 1))
    sm = SequenceMatcher(None, autojunk=False)
    sm.set_seq2(q)
    best, ctx = 0.0, ""
    for _, s in nlargest(30, cands or [(0, 0)]):
        for size in (max(1, n - 1), n, n + 1):
            win = " ".join(tw[s:s + size])
            sm.set_seq1(win)
            r = sm.ratio()
            if r > best:
                best, ctx = r, win
    best = int(best * 100) / 100  # вниз: неточное совпадение не показываем как 1.00
    return ("partial" if best >= 0.8 else "no"), best, ctx

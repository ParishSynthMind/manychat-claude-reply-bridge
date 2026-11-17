"""A small knowledge base: markdown files, one section per answerable fact.

Each ``## [id] Title`` heading starts an article; its id is what the AI cites.
Retrieval is plain keyword scoring with Arabic normalisation (no vector store
needed for a few hundred articles), so the same question in English or Arabic
finds the right article.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADING = re.compile(r"^##\s*\[([A-Za-z0-9_-]+)\]\s*(.+)$")
ARABIC_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
TOKEN = re.compile(r"[a-z0-9]+|[ء-ي]+")
STOP = {
    "the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "we", "to", "of", "and", "or", "in", "on",
    "for", "it", "my", "me", "what", "how", "can", "with", "at", "be", "have", "has", "there", "this", "that",
    "هل", "في", "من", "على", "الى", "إلى", "عن", "ما", "ماذا", "كيف", "انا", "أنا", "لكم", "لديكم", "هو", "هي",
}


@dataclass
class Article:
    id: str
    title: str
    body: str
    source: str

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


def normalise(text: str) -> str:
    text = ARABIC_DIACRITICS.sub("", text.lower())
    text = re.sub("[إأآا]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه")
    return text


def tokens(text: str) -> list[str]:
    out = []
    for tok in TOKEN.findall(normalise(text)):
        if tok.startswith("ال") and len(tok) > 4:  # Arabic definite article
            tok = tok[2:]
        if tok in STOP or len(tok) < 2:
            continue
        if tok.endswith("s") and len(tok) > 3 and tok.isascii():
            tok = tok[:-1]
        out.append(tok)
    return out


def parse_markdown(text: str, source: str) -> list[Article]:
    articles: list[Article] = []
    current = None
    lines: list[str] = []
    for line in text.splitlines():
        m = HEADING.match(line.strip())
        if m:
            if current:
                articles.append(Article(current[0], current[1], "\n".join(lines).strip(), source))
            current, lines = (m.group(1), m.group(2).strip()), []
        elif current:
            lines.append(line)
    if current:
        articles.append(Article(current[0], current[1], "\n".join(lines).strip(), source))
    return articles


class KnowledgeBase:
    def __init__(self, articles: list[Article]):
        ids = [a.id for a in articles]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate knowledge base ids: {sorted(dupes)}")
        self.articles = {a.id: a for a in articles}
        self._tokens = {a.id: set(tokens(a.text)) for a in articles}

    @classmethod
    def load(cls, directory: str | Path) -> "KnowledgeBase":
        arts: list[Article] = []
        for path in sorted(Path(directory).glob("*.md")):
            arts.extend(parse_markdown(path.read_text(encoding="utf-8"), path.name))
        if not arts:
            raise ValueError(f"no knowledge base articles found in {directory}")
        return cls(arts)

    def search(self, query: str, k: int = 4) -> list[Article]:
        q = set(tokens(query))
        if not q:
            return []
        scored = []
        for aid, toks in self._tokens.items():
            hits = len(q & toks)
            partial = sum(1 for t in q for u in toks if len(t) > 3 and t != u and (t in u or u in t))
            score = hits + 0.5 * partial
            if score > 0:
                scored.append((score, aid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.articles[aid] for _, aid in scored[:k]]

    def has(self, article_id: str) -> bool:
        return article_id in self.articles

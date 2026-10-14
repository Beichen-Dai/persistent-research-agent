"""Knowledge cards: an LLM digest of each document, built once and reused.

A card holds the document's methods, parameters, findings and mechanisms,
each with the page (or section) it comes from. Cards are rebuilt only for new
or changed documents (incremental learning).

Cards are used three ways:

* a one-line-per-document *catalog* is sent with every request;
* the cards most relevant to the latest user message are attached
  automatically (keyword matching, no extra API call);
* the model can fetch full cards with the ``get_knowledge_cards`` tool.

Cards are AI digests, not the documents themselves: a value from a card must
be checked on the cited page before it is reported as source evidence.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from research_agent.sources.base import DataSource
from research_agent.store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    content_hash TEXT,
    card_json TEXT NOT NULL,
    model TEXT,
    created_at TEXT
);
"""

SOURCE_CHARS = 50_000
WORKERS = 4
CATALOG_LINE_CHARS = 160

STOPWORDS = set(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "been",
        "have",
        "has",
        "into",
        "onto",
        "than",
        "then",
        "also",
        "such",
        "these",
        "those",
        "which",
        "while",
        "where",
        "when",
        "what",
        "their",
        "there",
        "using",
        "used",
        "use",
        "based",
        "between",
        "within",
        "under",
        "over",
        "more",
        "most",
        "less",
        "very",
        "can",
        "may",
        "our",
        "its",
        "not",
        "but",
        "all",
        "any",
        "each",
        "other",
        "both",
        "only",
        "same",
        "about",
        "after",
        "before",
        "study",
        "results",
        "result",
        "method",
        "methods",
        "paper",
        "show",
        "shows",
        "shown",
        "figure",
        "table",
    ]
)

CARD_PROMPT = """You are building a knowledge card for a scientific document.
Read the text below. Page or section markers look like [p.N].

Return ONLY a JSON object with these keys:

"title": the document's real title,
"authors_year": first author et al., year (if found),
"one_line": one sentence: what was studied and the main result,
"topic": short phrase,
"systems": materials / systems studied (list of strings),
"methods": computational or experimental methods, software, models (list of strings),
"key_parameters": list of {{"name", "value", "page"}} for parameters stated in the text,
"key_findings": list of {{"finding", "page"}} for the main reported results (include numbers),
"mechanisms": list of {{"claim", "page"}} for mechanisms or explanations proposed by the authors,
"open_questions": limitations or open questions the authors mention (list of strings),
"keywords": 10-20 English keywords, lowercase{extra_keywords}

Rules: only state what the text says. Every parameter, finding and mechanism must
carry the page it comes from. Do not invent numbers. If something is not in the
text, leave the list empty.

TEXT:
"""


def terms(text: str) -> set[str]:
    """English words plus CJK character bigrams (so CJK questions match CJK keywords)."""
    text = text.lower()
    out = {w for w in re.findall(r"[a-z][a-z0-9\-/]{2,}", text) if w not in STOPWORDS}
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            out.add(run)
        out |= {run[i : i + 2] for i in range(len(run) - 1)}
    return out


def card_terms(card: dict[str, Any]) -> set[str]:
    fields = [card.get("title", ""), card.get("one_line", ""), card.get("topic", "")]
    for key in ("systems", "methods", "keywords"):
        fields.append(" ".join(map(str, card.get(key) or [])))
    for key, value in card.items():
        if key.startswith("keywords_") and isinstance(value, list):
            fields.append(" ".join(map(str, value)))
    return terms(" ".join(map(str, fields)))


def card_brief(card: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "key",
        "title",
        "authors_year",
        "one_line",
        "methods",
        "key_parameters",
        "key_findings",
        "mechanisms",
        "related",
    )
    return {k: card[k] for k in keep if k in card}


class CardStore:
    """SQLite store of knowledge cards for all sources."""

    def __init__(self, path: Path, keyword_languages: list[str] | None = None) -> None:
        self.path = Path(path)
        self.keyword_languages = list(keyword_languages or [])

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # -- building -------------------------------------------------------------

    def _prompt(self) -> str:
        extra = "".join(
            f',\n"keywords_{lang.lower()}": 10-20 keywords translated into {lang}'
            for lang in self.keyword_languages
        )
        return CARD_PROMPT.format(extra_keywords=extra)

    def _generate(self, client: Any, model: str, title: str, text: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You return strict JSON."},
            {"role": "user", "content": self._prompt() + text},
        ]
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"}
            )
        except Exception:  # endpoint without JSON mode
            response = client.chat.completions.create(model=model, messages=messages)
        content = response.choices[0].message.content or ""
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M)
        card = json.loads(content)
        card.setdefault("title", title)
        return card

    def build(
        self,
        client: Any,
        model: str,
        sources: dict[str, DataSource],
        force: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Build cards for documents without an up-to-date card."""
        conn = self._db()
        try:
            existing = {
                r["key"]: r["content_hash"]
                for r in conn.execute("SELECT key, content_hash FROM cards")
            }
            wanted: dict[str, tuple[DataSource, Any]] = {}
            for source in sources.values():
                for cand in source.card_candidates():
                    wanted[f"{source.name}:{cand.doc_id}"] = (source, cand)

            # Cards of documents that no longer exist.
            stale = [
                key for key in existing if key not in wanted and key.split(":", 1)[0] in sources
            ]
            conn.executemany("DELETE FROM cards WHERE key = ?", [(k,) for k in stale])
            conn.commit()

            todo = [
                (key, src, cand)
                for key, (src, cand) in wanted.items()
                if force or existing.get(key) != cand.content_hash
            ]
            if limit:
                todo = todo[:limit]
            print(f"[cards] {len(todo)} document(s) to process.")

            texts = {key: src.card_text(cand.doc_id, SOURCE_CHARS) for key, src, cand in todo}
            built, failed = 0, []
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {
                    pool.submit(self._generate, client, model, cand.title, texts[key]): (
                        key,
                        src,
                        cand,
                    )
                    for key, src, cand in todo
                    if texts[key].strip()
                }
                for future in as_completed(futures):
                    key, src, cand = futures[future]
                    try:
                        card = future.result()
                    except Exception as exc:
                        failed.append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            key,
                            src.name,
                            cand.doc_id,
                            cand.content_hash,
                            json.dumps(card, ensure_ascii=False),
                            model,
                            now_iso(),
                        ),
                    )
                    conn.commit()
                    built += 1
                    print(f"[cards] {built}/{len(futures)} {key} {str(card.get('title', ''))[:70]}")
        finally:
            conn.close()

        self.link_related()
        return {
            "built": built,
            "failed": failed,
            "removed": len(stale),
            "skipped_no_text": [key for key, _, _ in todo if not texts[key].strip()],
        }

    # -- access ---------------------------------------------------------------

    def load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        conn = self._db()
        try:
            rows = conn.execute(
                "SELECT key, source, doc_id, card_json FROM cards ORDER BY key"
            ).fetchall()
        finally:
            conn.close()
        cards = {}
        for r in rows:
            try:
                card = json.loads(r["card_json"])
            except json.JSONDecodeError:
                continue
            card.update(key=r["key"], source=r["source"], doc_id=r["doc_id"])
            cards[r["key"]] = card
        return cards

    def get(self, keys: list[str]) -> list[dict[str, Any]]:
        cards = self.load()
        return [cards.get(str(k), {"key": k, "error": "No card with this key."}) for k in keys]

    def match(
        self, query: str, k: int = 3, min_score: float = 2.0, cards: dict | None = None
    ) -> list[dict[str, Any]]:
        """Cards most relevant to ``query`` (IDF-weighted term overlap)."""
        cards = cards if cards is not None else self.load()
        if not cards:
            return []
        index = {key: card_terms(card) for key, card in cards.items()}
        df = Counter(t for ts in index.values() for t in ts)
        n = len(index)
        q = terms(query)
        scored = []
        for key, ts in index.items():
            score = sum(math.log(1 + n / df[t]) for t in q & ts)
            if score >= min_score:
                scored.append((score, key))
        scored.sort(reverse=True)
        return [cards[key] for _, key in scored[:k]]

    def link_related(self, top: int = 3, min_jaccard: float = 0.08) -> None:
        """Store in each card the documents that share many concepts with it."""
        cards = self.load()
        index = {key: card_terms(card) for key, card in cards.items()}
        conn = self._db()
        try:
            for key, ts in index.items():
                sims = sorted(
                    (
                        (len(ts & other) / len(ts | other), okey)
                        for okey, other in index.items()
                        if okey != key and ts and other
                    ),
                    reverse=True,
                )
                card = cards[key]
                card["related"] = [
                    {"key": okey, "title": cards[okey].get("title", "")}
                    for j, okey in sims[:top]
                    if j >= min_jaccard
                ]
                stored = {k: v for k, v in card.items() if k not in ("key", "source", "doc_id")}
                conn.execute(
                    "UPDATE cards SET card_json = ? WHERE key = ?",
                    (json.dumps(stored, ensure_ascii=False), key),
                )
            conn.commit()
        finally:
            conn.close()

    def status(self, sources: dict[str, DataSource]) -> dict[str, Any]:
        cards = self.load()
        candidates = {f"{s.name}:{c.doc_id}" for s in sources.values() for c in s.card_candidates()}
        return {
            "cards": len(cards),
            "documents_with_card_support": len(candidates),
            "documents_without_card": len(candidates - set(cards)),
            "database": str(self.path),
        }

    # -- context blocks -------------------------------------------------------

    def catalog(self) -> str:
        lines = []
        for key, card in sorted(self.load().items()):
            one = str(card.get("one_line", "")).strip()
            if len(one) > CATALOG_LINE_CHARS:
                one = one[:CATALOG_LINE_CHARS].rsplit(" ", 1)[0] + " ..."
            lines.append(
                f"{key} | {card.get('title', '')} | {card.get('authors_year', '')} | {one}"
            )
        return "\n".join(lines)

    def auto_context(self, query: str, k: int) -> str | None:
        cards = self.match(query, k=k)
        if not cards:
            return None
        return (
            "RELEVANT KNOWLEDGE CARDS (auto-retrieved for the latest user message; [CARD] = "
            "AI digest, verify the cited page before reporting a value as source evidence):\n"
            + json.dumps([card_brief(c) for c in cards], ensure_ascii=False, indent=1)
        )

import json

from conftest import FakeClient, reply
from research_agent.knowledge.cards import CardStore, terms
from research_agent.sources.text_folder import TextFolderSource


def card_reply(title, keywords):
    return lambda request: reply(
        json.dumps({"title": title, "one_line": f"About {title}", "keywords": keywords})
    )


def test_build_match_and_incremental(tmp_path, notes_dir):
    source = TextFolderSource("notes", tmp_path / "idx", path=str(notes_dir), cards=True)
    source.index()
    store = CardStore(tmp_path / "cards.db", keyword_languages=["Chinese"])

    client = FakeClient(
        [
            card_reply("Carbon", ["carbon", "graphitization"]),
            card_reply("Copper", ["copper", "quench"]),
        ]
    )
    summary = store.build(client, "m", {"notes": source})
    assert summary["built"] == 2 and not summary["failed"]
    assert "keywords_chinese" in client.requests[0]["messages"][1]["content"]

    # Unchanged documents are not rebuilt.
    assert store.build(FakeClient([]), "m", {"notes": source})["built"] == 0

    assert store.match("graphitization of carbon")[0]["title"] == "Carbon"
    assert "notes:" in store.catalog()
    assert "RELEVANT KNOWLEDGE CARDS" in store.auto_context("graphitization carbon", 2)
    key = store.match("copper quench")[0]["key"]
    assert store.get([key])[0]["title"] == "Copper"
    assert store.get(["missing"])[0]["error"]
    assert store.status({"notes": source})["documents_without_card"] == 0


def test_failed_cards_are_reported(tmp_path, notes_dir):
    source = TextFolderSource("notes", tmp_path / "idx", path=str(notes_dir), cards=True)
    source.index()
    store = CardStore(tmp_path / "cards.db")
    summary = store.build(
        FakeClient([reply("not json"), reply("not json")]), "m", {"notes": source}
    )
    assert summary["built"] == 0 and len(summary["failed"]) == 2


def test_terms_handle_cjk():
    # "\u77f3\u58a8\u5316" is the Chinese word for graphitization (three characters).
    assert {"\u77f3\u58a8", "\u58a8\u5316"} <= terms("\u77f3\u58a8\u5316")
    assert "the" not in terms("the graphite")

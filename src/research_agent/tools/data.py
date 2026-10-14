"""Generic tools that reach every configured data source, plus knowledge cards."""

from __future__ import annotations

from typing import Any

from research_agent import workflow
from research_agent.sources.base import DataSource, SourceError
from research_agent.tools.registry import Tool, ToolContext, params

MAX_RESULTS = 50


def _source(ctx: ToolContext, name: str) -> DataSource:
    if name not in ctx.sources:
        raise SourceError(
            f"Unknown source '{name}'. Available: {', '.join(ctx.sources) or 'none'}."
        )
    source = ctx.sources[name]
    if workflow.source_disabled(ctx.store.project_state, source.name, source.evidence_label):
        raise SourceError(f"Source '{name}' is disabled in the current workflow phase.")
    return source


def list_sources(ctx: ToolContext, args: dict[str, Any]) -> Any:
    out = []
    for source in ctx.sources.values():
        info = source.describe()
        if workflow.source_disabled(ctx.store.project_state, source.name, source.evidence_label):
            info["disabled_in_current_phase"] = True
        out.append(info)
    return out


def search_sources(ctx: ToolContext, args: dict[str, Any]) -> Any:
    query = args["query"]
    limit = max(1, min(int(args.get("limit", 10)), MAX_RESULTS))
    filters = args.get("filters") or {}
    names = [args["source"]] if args.get("source") else list(ctx.sources)

    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for name in names:
        try:
            source = _source(ctx, name)
            hits = source.search(query, limit=limit, filters=filters)
        except SourceError as exc:
            if args.get("source"):
                raise
            errors[name] = str(exc)
            continue
        except Exception as exc:  # a broken backend must not stop the agent
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        for rank, hit in enumerate(hits):
            hit.source = name
            item = hit.to_dict()
            item.setdefault("label", hit.metadata.get("label", source.evidence_label))
            item["_rank"] = rank
            results.append(item)

    # Interleave sources by rank so one source cannot crowd out the others.
    results.sort(key=lambda r: r.pop("_rank"))
    response: dict[str, Any] = {
        "query": query,
        "count": len(results[:limit]),
        "results": results[:limit],
    }
    if errors:
        response["errors"] = errors
    return response


def read_document(ctx: ToolContext, args: dict[str, Any]) -> Any:
    source = _source(ctx, args["source"])
    doc = source.read(str(args["doc_id"]), max_chars=int(args.get("max_chars", 30_000)))
    return {"source": source.name, "label": source.evidence_label, **doc.to_dict()}


def list_documents(ctx: ToolContext, args: dict[str, Any]) -> Any:
    source = _source(ctx, args["source"])
    docs = source.list_documents(
        limit=int(args.get("limit", 50)), offset=int(args.get("offset", 0))
    )
    return {"source": source.name, "documents": [d.to_dict() for d in docs]}


def get_knowledge_cards(ctx: ToolContext, args: dict[str, Any]) -> Any:
    return ctx.cards.get(args["keys"]) if ctx.cards else []


def search_knowledge_cards(ctx: ToolContext, args: dict[str, Any]) -> Any:
    from research_agent.knowledge.cards import card_brief

    if not ctx.cards:
        return []
    return [
        card_brief(c)
        for c in ctx.cards.match(args["query"], k=int(args.get("limit", 5)), min_score=0.5)
    ]


SOURCE_TOOLS = [
    Tool(
        "list_sources",
        "List the configured data sources with their descriptions, evidence labels, "
        "capabilities and supported search filters.",
        params({}),
        list_sources,
    ),
    Tool(
        "search_sources",
        "Full-text search for evidence in one data source or in all of them. Returns text "
        "excerpts with document id, title, location (e.g. page) and evidence label.",
        params(
            {
                "query": {"type": "string", "description": "Keywords or a research question."},
                "source": {"type": "string", "description": "Source name; omit to search all."},
                "limit": {"type": "integer", "default": 10},
                "filters": {
                    "type": "object",
                    "description": "Source-specific filters (see list_sources).",
                },
            },
            ["query"],
        ),
        search_sources,
    ),
    Tool(
        "read_document",
        "Read the content of one document from a data source (doc_id from search_sources or "
        "list_documents).",
        params(
            {
                "source": {"type": "string"},
                "doc_id": {"type": "string"},
                "max_chars": {"type": "integer", "default": 30000},
            },
            ["source", "doc_id"],
        ),
        read_document,
    ),
    Tool(
        "list_documents",
        "List documents in a data source.",
        params(
            {
                "source": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            ["source"],
        ),
        list_documents,
    ),
]

CARD_TOOLS = [
    Tool(
        "get_knowledge_cards",
        "Get full knowledge cards (methods, parameters, findings, mechanisms with pages, "
        "related documents) by key, as listed in the document catalog (e.g. 'papers:12').",
        params({"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]),
        get_knowledge_cards,
    ),
    Tool(
        "search_knowledge_cards",
        "Find the knowledge cards most relevant to a question.",
        params(
            {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, ["query"]
        ),
        search_knowledge_cards,
    ),
]

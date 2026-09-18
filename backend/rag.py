from __future__ import annotations

from ingest import get_collection, ingest, parse_chunks


def ensure_index() -> None:
    collection = get_collection()
    if collection.count() == 0:
        ingest()


def search(query: str, limit: int = 4) -> list[dict]:
    ensure_index()
    collection = get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_texts=[query],
        n_results=min(limit, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    hits: list[dict] = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for index, doc in enumerate(docs):
        meta = metas[index] if index < len(metas) else {}
        title, _, body = (doc or "").partition("\n")
        distance = distances[index] if index < len(distances) else 1.0
        hits.append(
            {
                "id": ids[index] if index < len(ids) else "",
                "title": meta.get("title") or title.strip(),
                "body": body.strip() or doc,
                "file": meta.get("file", ""),
                "score": round(1.0 / (1.0 + float(distance)), 4),
            }
        )
    return hits


def chunk_count() -> int:
    try:
        return get_collection().count()
    except Exception:
        return len(parse_chunks())

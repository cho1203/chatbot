from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MANUAL_DIR = ROOT / "data" / "manuals"
CHROMA_PATH = ROOT / os.getenv("CHROMA_PATH", "data/chroma")
COLLECTION = os.getenv("CHROMA_COLLECTION", "fanuc_manuals")


def parse_chunks() -> list[dict]:
    chunks: list[dict] = []
    if not MANUAL_DIR.exists():
        return chunks
    for path in sorted(MANUAL_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        parts = re.split(r"(?m)^# ", text)
        for index, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            title, _, body = part.partition("\n")
            title = title.strip() or path.stem
            body = body.strip()
            chunks.append(
                {
                    "id": f"{path.name}:{index}:{title}"[:200],
                    "title": title,
                    "body": body,
                    "file": path.name,
                    "text": f"{title}\n{body}",
                }
            )
    return chunks


def get_collection():
    import chromadb
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    download_dir = ROOT / ".cache" / "chroma" / "onnx_models" / ONNXMiniLM_L6_V2.MODEL_NAME
    download_dir.mkdir(parents=True, exist_ok=True)
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = download_dir
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_or_create_collection(name=COLLECTION)


def ingest() -> int:
    chunks = parse_chunks()
    collection = get_collection()
    if not chunks:
        return 0
    existing = collection.get()
    ids = existing.get("ids") or []
    if ids:
        collection.delete(ids=ids)
    collection.add(
        ids=[item["id"] for item in chunks],
        documents=[item["text"] for item in chunks],
        metadatas=[
            {"title": item["title"], "file": item["file"], "machine": "common"}
            for item in chunks
        ],
    )
    return len(chunks)


if __name__ == "__main__":
    count = ingest()
    print(f"Chroma에 {count}개 항목을 넣었습니다. 경로: {CHROMA_PATH}")

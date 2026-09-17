from __future__ import annotations

import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

FORBIDDEN_PORTS = {8005, 8010, 8011, 8020, 8080, 5173, 3306, 3308, 11434}

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8787"))
FRONT_PORT = int(os.getenv("FRONT_PORT", str(APP_PORT)))
API_PORT = int(os.getenv("API_PORT", str(APP_PORT)))

FRONTEND = ROOT / "frontend"
DATA_DIR = ROOT / "data"
MANUAL_DIR = DATA_DIR / "manuals"
DB_PATH = DATA_DIR / "chatbot.db"


def check_forbidden_ports() -> None:
    for name, port in (
        ("APP_PORT", APP_PORT),
        ("FRONT_PORT", FRONT_PORT),
        ("API_PORT", API_PORT),
    ):
        if port in FORBIDDEN_PORTS:
            print(
                f"[오류] {name}={port} 는 사용 금지 포트입니다. "
                "금지: 8005, 8010, 8011, 8020, 8080, 5173, 3306, 3308, 11434",
                file=sys.stderr,
            )
            sys.exit(1)
    if len({APP_PORT, FRONT_PORT, API_PORT}) != 1:
        print("[오류] 이 프로젝트는 포트 8787 하나만 사용합니다.", file=sys.stderr)
        sys.exit(1)
    if APP_PORT != 8787:
        print("[오류] APP_PORT 는 8787 이어야 합니다.", file=sys.stderr)
        sys.exit(1)


check_forbidden_ports()

app = FastAPI(title="FANUC 매뉴얼 챗봇")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


class Source(BaseModel):
    title: str
    file: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    session_id: str


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_message(session_id: str, role: str, content: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def load_chunks() -> list[dict]:
    chunks: list[dict] = []
    if not MANUAL_DIR.exists():
        return chunks
    for path in sorted(MANUAL_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        parts = re.split(r"(?m)^# ", text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            title, _, body = part.partition("\n")
            body = body.strip()
            chunks.append(
                {
                    "title": title.strip() or path.stem,
                    "body": body,
                    "file": path.name,
                    "haystack": f"{title} {body}".lower(),
                }
            )
    return chunks


CHUNKS = load_chunks()
TOKEN_RE = re.compile(r"[a-zA-Z]+[0-9.]*|[0-9]+|[가-힣]{2,}")


def tokens(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_RE.findall(text)]


def search_manuals(query: str, limit: int = 3) -> list[dict]:
    query_tokens = tokens(query)
    if not query_tokens:
        return []
    codes = [token for token in query_tokens if re.fullmatch(r"[a-z]+\d+|\d+", token)]
    scored: list[dict] = []
    for chunk in CHUNKS:
        hay = chunk["haystack"]
        title_l = chunk["title"].lower()
        if codes and not any(code in hay for code in codes):
            continue
        score = 0.0
        for token in query_tokens:
            if token in hay:
                score += 2.0 if re.fullmatch(r"[a-z]+\d+|\d+", token) else 1.0
        for code in codes:
            if code in title_l.split() or title_l.startswith(code) or f" {code}" in f" {title_l}":
                score += 6.0
        if query.strip().lower() in title_l:
            score += 5.0
        if score > 0:
            item = dict(chunk)
            item["score"] = score
            scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def build_answer(query: str, hits: list[dict]) -> str:
    if not hits:
        return (
            "매뉴얼에서 관련 항목을 찾지 못했습니다.\n"
            "알람 번호, G/M 코드, 또는 '공구 길이 보정', '비상정지'처럼 기능 이름으로 다시 질문해 주세요."
        )
    lines = [f"질문 '{query.strip()}'에 해당하는 매뉴얼 내용입니다.\n"]
    for hit in hits:
        excerpt = hit["body"].strip()
        if len(excerpt) > 700:
            excerpt = excerpt[:700].rstrip() + "…"
        lines.append(f"[{hit['title']}]\n{excerpt}")
    return "\n\n".join(lines)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "port": APP_PORT,
        "host": APP_HOST,
        "db": str(DB_PATH),
        "manual_chunks": len(CHUNKS),
        "forbidden_ports": sorted(FORBIDDEN_PORTS),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="질문을 입력하세요.")
    session_id = req.session_id or str(uuid.uuid4())
    hits = search_manuals(message)
    answer = build_answer(message, hits)
    save_message(session_id, "user", message)
    save_message(session_id, "assistant", answer)
    sources = [
        Source(title=hit["title"], file=hit["file"], score=hit["score"])
        for hit in hits
    ]
    return ChatResponse(answer=answer, sources=sources, session_id=session_id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="js")


if __name__ == "__main__":
    import uvicorn

    print(f"FANUC 매뉴얼 챗봇: http://{APP_HOST}:{APP_PORT}")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)

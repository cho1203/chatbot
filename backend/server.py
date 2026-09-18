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

from llm import generate_card_data, generate_chat_answer, model_ready
from rag import chunk_count, search as chroma_search

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

MACHINES = {
    "mill_0i": "0i 밀링",
    "lathe_0i": "0i 선반",
    "mill_31i": "31i",
}


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
    machine: str | None = "mill_0i"
    mode: str | None = "workshop"
    source: str | None = "chat"


class Source(BaseModel):
    title: str
    file: str
    score: float


class AnswerCard(BaseModel):
    kind: str = "generic"
    title: str = ""
    lead: str = ""
    cause: str = ""
    action: str = ""
    caution: str = ""
    note: str = ""
    badges: list[str] = []


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    session_id: str
    card: AnswerCard | None = None
    machine: str = "mill_0i"


ALARM_CARDS = {
    "000": AnswerCard(
        kind="alarm",
        title="리셋 또는 비상정지 해제 대기",
        lead="전원 직후나 비상정지 직후면 이 화면이 납니다. 리셋부터 서두르지 마세요.",
        cause="비상정지 회로가 열려 있거나, 전원 투입 직후 리셋 대기 상태입니다.",
        action="비상버튼을 돌려 해제하고, 도어·외부 EMG를 확인한 뒤 리셋하세요. 필요하면 원점 복귀합니다.",
        caution="축이 어디에 있는지도 모른 채 사이클 스타트 하지 마세요.",
        badges=["000"],
    ),
    "410": AnswerCard(
        kind="alarm",
        title="위치편차 과대 (정지 중)",
        lead="410이면 축이 물린 건지부터 보세요. 리셋은 그다음입니다.",
        cause="정지 중 위치 편차가 허용값을 넘었습니다. 축이 외력에 밀렸거나, 구속·서보 이상인 경우가 많습니다.",
        action="해당 축이 치구·워크에 박혀 있는지 보고, 수동으로 빼낼 수 있으면 뺀 뒤 리셋하세요.",
        caution="구속이 남은 채로 리셋하고 바로 이송하면 다시 알람이 납니다.",
        badges=["410"],
    ),
    "411": AnswerCard(
        kind="alarm",
        title="위치편차 과대 (이동 중)",
        lead="이동 중 추종이 못 따라간 겁니다. 속도부터 낮추세요.",
        cause="이송이 너무 빠르거나, 절삭 부하가 크거나, 서보/기계 구속이 있습니다.",
        action="오버라이드를 낮추고 부하·간섭을 확인하세요. 같은 구간에서 반복되면 이송·절입을 줄입니다.",
        caution="알람만 지우고 같은 조건으로 재개하지 마세요.",
        badges=["411"],
    ),
    "414": AnswerCard(
        kind="alarm",
        title="서보 이상 검출",
        lead="CNC 프로그램 문제가 아니라 서보 쪽을 의심하세요.",
        cause="서보 앰프·모터·피드백 이상인 경우가 많습니다.",
        action="축 번호와 앰프 LED, 케이블·커넥터를 확인하세요.",
        caution="리셋으로만 반복 해제되면 운전하지 말고 보전을 부르세요.",
        badges=["414"],
    ),
    "510": AnswerCard(
        kind="alarm",
        title="오버트래블 +",
        lead="축이 + 리미트를 넘었습니다. 반대 방향으로 빼세요.",
        cause="해당 축이 + 방향 리미트(하드 또는 소프트)를 넘었습니다.",
        action="수동으로 - 방향으로 축을 빼낸 뒤 리셋합니다. 소프트 리미트 파라미터도 확인하세요.",
        caution="리미트 상태에서 같은 방향으로 더 보내지 마세요.",
        badges=["510"],
    ),
    "511": AnswerCard(
        kind="alarm",
        title="오버트래블 -",
        lead="축이 - 리미트를 넘었습니다. + 쪽으로 빼세요.",
        cause="해당 축이 - 방향 리미트를 넘었습니다.",
        action="수동으로 + 방향으로 복귀시킨 뒤 리셋합니다.",
        caution="원점·워크 원점이 어긋났는지도 같이 보세요.",
        badges=["511"],
    ),
    "ps0009": AnswerCard(
        kind="alarm",
        title="잘못된 어드레스",
        lead="그 블록에 CNC가 모르는 글자가 있습니다.",
        cause="프로그램 어드레스, 소수점, 허용되지 않는 지령이 있습니다.",
        action="알람 난 블록 O/N 번호를 열고 해당 줄을 고치세요.",
        caution="한 줄만 지우고 넘기면 다음 블록에서 또 납니다.",
        badges=["PS0009"],
    ),
    "ps0011": AnswerCard(
        kind="alarm",
        title="절삭이송 속도 지정 없음",
        lead="G01인데 F가 없는 겁니다. F부터 넣으세요.",
        cause="절삭 지령에 F가 없거나 이전 F가 유효하지 않습니다.",
        action="해당 블록 또는 앞에 F를 지령하세요.",
        caution="F 없이 급속으로 밀어 넣지 마세요.",
        badges=["PS0011"],
    ),
}

CODE_CARDS = {
    "g43": AnswerCard(
        kind="code",
        title="공구 길이 보정",
        lead="H 번호가 틀리면 바로 Z 충돌입니다.",
        cause="공구 길이 오프셋 H를 + 방향으로 적용합니다.",
        action="공구에 맞는 H를 넣고 G43 Hxx Z.. 로 안전 높이에서 적용하세요.",
        caution="공구를 바꿨는데 H를 안 바꾸면 안 됩니다.",
        badges=["G43"],
    ),
    "g41": AnswerCard(
        kind="code",
        title="공구경 보정 좌측",
        lead="진행 방향 왼쪽을 보정합니다. D 오프셋이 필요합니다.",
        cause="공구 반지름만큼 경로를 왼쪽으로 밉니다.",
        action="직선 블록에서 G41 Dxx 로 켜고, 끝날 때 G40으로 끄세요.",
        caution="원호에서 켜고 끄면 과대 보정 알람이 납니다.",
        badges=["G41"],
    ),
    "g54": AnswerCard(
        kind="code",
        title="워크 좌표계 G54",
        lead="기본 워크 원점입니다. G55와 값이 다릅니다.",
        cause="측정한 워크 원점을 G54에 넣습니다.",
        action="프로그램 선두에서 G54를 명시하고, 측정값과 화면 값이 같은지 보세요.",
        caution="G54/G55를 섞어 쓰면 원점이 바뀝니다.",
        badges=["G54"],
    ),
    "g55": AnswerCard(
        kind="code",
        title="워크 좌표계 G55",
        lead="두 번째 원점입니다. G54와 별개입니다.",
        cause="한 테이블에 다른 원점의 공작물을 둘 때 씁니다.",
        action="G55 값을 따로 측정해 넣고, 프로그램에서 G55를 지령하세요.",
        caution="G54만 고치고 G55로 돌리면 위치가 틀립니다.",
        badges=["G55"],
    ),
    "g01": AnswerCard(
        kind="code",
        title="절삭이송 G01",
        lead="F가 있어야 움직입니다.",
        cause="지정한 F로 직선 절삭합니다.",
        action="G01 X.. Y.. F.. 형태로 지령하세요.",
        caution="F 없이 돌리면 PS0011이 납니다.",
        badges=["G01"],
    ),
}

HOWTO_CARDS = {
    "비상정지": AnswerCard(
        kind="howto",
        title="비상정지 후 복구",
        lead="위험부터 보고, 버튼은 그다음입니다.",
        cause="비상정지 회로가 열려 축·주축이 멈춘 상태입니다.",
        action="회전·간섭을 확인하고 버튼을 돌려 해제한 뒤 리셋, 필요하면 원점 복귀하세요.",
        caution="원점·오프셋을 확인하기 전에 프로그램을 재개하지 마세요.",
        badges=["EMG"],
    ),
    "원점": AnswerCard(
        kind="howto",
        title="원점 복귀",
        lead="위치를 모르는 상태면 원점부터입니다.",
        cause="전원 투입 또는 비상정지 후 기계 좌표가 미확정일 수 있습니다.",
        action="수동 원점 복귀 모드에서 각 축을 원점 방향으로 보내세요.",
        caution="간섭 구간에서 원점 방향으로 급속 보내지 마세요.",
        badges=["G28"],
    ),
    "공구 길이": AnswerCard(
        kind="howto",
        title="공구 길이 보정",
        lead="측정값과 H 번호가 한 세트입니다.",
        cause="공구마다 길이가 달라 Z 위치가 바뀝니다.",
        action="공구를 측정해 H에 넣고, 프로그램의 H와 실제 공구를 맞추세요.",
        caution="이전 공구 H를 그대로 쓰면 Z 충돌입니다.",
        badges=["G43", "H"],
    ),
}

MACHINE_NOTES = {
    ("g43", "mill_0i"): "0i 밀링: 안전 Z에서 G43 Hxx 적용하세요.",
    ("g43", "lathe_0i"): "0i 선반: 공구 형상 오프셋(T)이 먼저입니다. G43을 쓰는 기종인지 확인하세요.",
    ("g43", "mill_31i"): "31i: 공구 관리 화면 오프셋과 프로그램 H가 같은지 같이 보세요.",
    ("g41", "mill_0i"): "0i 밀링: 외형 절삭에서 G41+D가 일반적입니다.",
    ("g41", "lathe_0i"): "0i 선반: 공구경 보정 대신 인선 R 보정을 쓰는 기종이 많습니다.",
    ("g41", "mill_31i"): "31i: D 오프셋과 공구 번호 연결을 공구 관리에서 확인하세요.",
    ("g01", "mill_0i"): "0i 밀링: 보통 G94(mm/min) F입니다.",
    ("g01", "lathe_0i"): "0i 선반: G95(회전당 이송)인 경우가 많습니다. F 단위를 확인하세요.",
    ("g01", "mill_31i"): "31i: 모달 F가 남아 있는지 싱글블록으로 한 줄 확인하세요.",
    ("g54", "mill_0i"): "0i 밀링: X/Y 에지, Z 면 터치가 기본입니다.",
    ("g54", "lathe_0i"): "0i 선반: X는 직경 값인지 반경 값인지 화면 단위를 보세요.",
    ("g54", "mill_31i"): "31i: 워크 시프트·외부 원점이 더해져 있는지 확인하세요.",
    ("410", "mill_0i"): "0i 밀링: 테이블·바이스에 Z가 박힌 경우가 많습니다.",
    ("410", "lathe_0i"): "0i 선반: 척·심압계에 X/Z가 물렸는지 먼저 보세요.",
    ("410", "mill_31i"): "31i: 서보 진단 화면의 축 번호를 같이 보세요.",
    ("411", "mill_0i"): "0i 밀링: 절입·이송이 과하면 411이 납니다. 오버라이드부터 내리세요.",
    ("411", "lathe_0i"): "0i 선반: 절입 과다·칩 말림으로 부하가 급증했는지 보세요.",
    ("411", "mill_31i"): "31i: 부하 미터와 서보 추종 오차를 함께 보세요.",
    ("비상정지", "mill_0i"): "0i 밀링: 도어 인터록이 같이 열려 있는 경우가 많습니다.",
    ("비상정지", "lathe_0i"): "0i 선반: 척 미체결·도어 신호를 같이 보세요.",
    ("비상정지", "mill_31i"): "31i: PMC 진단에서 EMG 접점을 확인하세요.",
}


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
WEAK_TOKENS = {"주의", "방법", "하는", "어떻게", "무엇", "인가요", "해주세요", "관련", "내용"}
OVERTRAVEL_TERMS = ("오버트래블", "오버트러블", "overtravel")
MISSING_ANSWER = "매뉴얼에 없음\n그 번호·기능 이름으로는 못 찾았습니다."


def tokens(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_RE.findall(text)]


def query_has_overtravel(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in OVERTRAVEL_TERMS)


def hit_text(hit: dict) -> str:
    return f"{hit.get('title', '')} {hit.get('body', '')} {hit.get('haystack', '')}".lower()


def hit_mentions_overtravel(hit: dict) -> bool:
    hay = hit_text(hit)
    return any(term in hay for term in OVERTRAVEL_TERMS) or "510" in hay or "511" in hay


def strong_query_terms(query: str) -> list[str]:
    terms = [token for token in tokens(query) if token not in WEAK_TOKENS]
    if query_has_overtravel(query):
        for extra in ("오버트래블", "510", "511"):
            if extra not in terms:
                terms.append(extra)
    return terms


def hit_matches_query(query: str, hit: dict) -> bool:
    terms = strong_query_terms(query)
    if not terms:
        return False
    if query_has_overtravel(query) and not hit_mentions_overtravel(hit):
        return False
    hay = hit_text(hit)
    codes = [term for term in terms if re.fullmatch(r"[a-z]+\d+|\d+", term)]
    if codes and not any(code in hay for code in codes):
        return False
    return any(term in hay for term in terms)


def search_keyword(query: str, limit: int = 3) -> list[dict]:
    query_tokens = tokens(query)
    if not query_tokens and not query_has_overtravel(query):
        return []
    codes = [token for token in query_tokens if re.fullmatch(r"[a-z]+\d+|\d+", token)]
    scored: list[dict] = []
    overtravel = query_has_overtravel(query)
    for chunk in CHUNKS:
        hay = chunk["haystack"]
        title_l = chunk["title"].lower()
        if codes and not any(code in hay for code in codes):
            continue
        score = 0.0
        for token in query_tokens:
            if token in WEAK_TOKENS:
                continue
            if token in hay:
                score += 2.0 if re.fullmatch(r"[a-z]+\d+|\d+", token) else 1.0
        for code in codes:
            if code in title_l.split() or title_l.startswith(code) or f" {code}" in f" {title_l}":
                score += 6.0
        if query.strip().lower() in title_l:
            score += 5.0
        if overtravel and hit_mentions_overtravel(chunk):
            score += 12.0
            if "510" in title_l or "511" in title_l or "오버트래블" in title_l:
                score += 8.0
        if score > 0:
            item = dict(chunk)
            item["score"] = score
            scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def search_manuals(query: str, limit: int = 4) -> list[dict]:
    keyword_hits = search_keyword(query, limit=limit)
    chroma_hits: list[dict] = []
    try:
        chroma_hits = chroma_search(query, limit=limit)
    except Exception:
        chroma_hits = []

    merged: list[dict] = []
    seen: set[str] = set()
    for hit in keyword_hits + chroma_hits:
        title = hit.get("title", "")
        if title in seen:
            continue
        if not hit_matches_query(query, hit):
            continue
        seen.add(title)
        merged.append(hit)
    return merged[:limit]


def query_codes(query: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    lowered = query.lower()
    for token in tokens(query):
        if re.fullmatch(r"[a-z]+\d+|\d+", token) and token not in seen:
            found.append(token)
            seen.add(token)
    for match in re.finditer(r"\bg([0-9]{2,3})\b", lowered):
        code = f"g{match.group(1)}"
        if code not in seen:
            found.append(code)
            seen.add(code)
    return found


PAIR_CODES = (
    ("g40", "g41"),
    ("g40", "g42"),
    ("g41", "g42"),
    ("g43", "g49"),
    ("g54", "g55"),
    ("510", "511"),
    ("410", "411"),
)
COMPARE_WORDS = ("차이", "비교", "그리고", "이랑", "또는", "차이점")


def rank_core_hits(query: str, hits: list[dict]) -> list[dict]:
    codes = query_codes(query)

    def rank(hit: dict) -> tuple[float, float]:
        hay = hit_text(hit)
        title = hit.get("title", "").lower()
        bonus = 0.0
        for code in codes:
            if code in title:
                bonus += 10.0
            elif code in hay:
                bonus += 3.0
        return (bonus, float(hit.get("score") or 0.0))

    return sorted(hits, key=rank, reverse=True)


def should_keep_second(query: str, first: dict, second: dict) -> bool:
    codes = query_codes(query)
    second_hay = hit_text(second)
    if len(codes) >= 2:
        return any(code in second_hay for code in codes)
    lowered = query.lower()
    if any(word in lowered for word in COMPARE_WORDS):
        return True
    if query_has_overtravel(query):
        return hit_mentions_overtravel(second)
    for left, right in PAIR_CODES:
        both_in_query = (left in lowered or left in codes) and (
            right in lowered or right in codes
        )
        if both_in_query and (left in second_hay or right in second_hay):
            return True
    score1 = float(first.get("score") or 0.0)
    score2 = float(second.get("score") or 0.0)
    if score1 > 0 and score2 >= score1 * 0.9:
        return any(code in second_hay for code in codes) if codes else False
    return False


def select_core_hits(query: str, hits: list[dict]) -> list[dict]:
    if not hits:
        return []
    ranked = rank_core_hits(query, hits)
    chosen = [ranked[0]]
    if len(ranked) > 1 and should_keep_second(query, ranked[0], ranked[1]):
        chosen.append(ranked[1])
    return chosen[:2]


def shorten_excerpt(query: str, body: str, limit: int = 250) -> str:
    text = " ".join((body or "").split())
    if not text:
        return ""
    terms = strong_query_terms(query)
    parts = [part.strip() for part in re.split(r"(?<=[\.!?다요])\s+|\n+", text) if part.strip()]
    picked = [part for part in parts if any(term in part.lower() for term in terms)]
    if not picked:
        picked = parts[:2] or [text]
    out = " ".join(picked)
    if len(out) > limit:
        out = out[:limit].rstrip() + "…"
    return out


def build_chat_answer(query: str, hits: list[dict]) -> str:
    if not hits:
        return MISSING_ANSWER
    lines = []
    for hit in hits:
        excerpt = shorten_excerpt(query, hit.get("body", ""))
        lines.append(f"[{hit['title']}]\n{excerpt}")
    return "\n\n".join(lines)


def build_classic_answer(query: str, hits: list[dict]) -> str:
    return build_chat_answer(query, select_core_hits(query, hits))


def lookup_key(query: str) -> str:
    joined = query.lower()
    if query_has_overtravel(query):
        if any(word in joined for word in ("-", "마이너스", "음방향", "−")):
            return "511"
        return "510"
    match = re.search(r"\bg([0-9]{2,3})\b", joined)
    if match:
        return f"g{match.group(1)}"
    match = re.search(r"ps\s*0*(\d+)", joined)
    if match:
        return f"ps{int(match.group(1)):04d}"
    match = re.search(r"\b(410|411|414|510|511|000|100|101)\b", joined)
    if match:
        return match.group(1)
    if "비상" in joined:
        return "비상정지"
    if "원점" in joined:
        return "원점"
    if "공구" in joined and "길이" in joined:
        return "공구 길이"
    return ""


def hit_matches_key(hit: dict, key: str) -> bool:
    if not key:
        return False
    hay = f"{hit.get('title', '')} {hit.get('body', '')}".lower()
    if key in ("510", "511"):
        return key in hay or query_has_overtravel(hay)
    if re.fullmatch(r"\d+", key):
        return key in hay
    return key.lower() in hay


def machine_note(key: str, machine: str) -> str:
    extra = MACHINE_NOTES.get((key, machine), "")
    if extra:
        return extra
    return f"{MACHINES.get(machine, MACHINES['mill_0i'])} 기준으로 보세요."


def build_card(query: str, hits: list[dict], machine: str) -> AnswerCard:
    llm_data = generate_card_data(query, hits, machine) if hits else None
    if llm_data:
        return AnswerCard(**llm_data)
    key = lookup_key(query)
    card = None
    if hits:
        hit = hits[0]
        excerpt = hit["body"].strip()
        if len(excerpt) > 400:
            excerpt = excerpt[:400].rstrip() + "…"
        base = ALARM_CARDS.get(key) or CODE_CARDS.get(key) or HOWTO_CARDS.get(key)
        card = AnswerCard(
            kind=base.kind if base else "generic",
            title=hit["title"],
            lead=base.lead if base else "매뉴얼 기준으로 답합니다.",
            cause=base.cause if base else excerpt[:180],
            action=excerpt,
            caution=base.caution if base else "해당 기종 화면·파라미터와 한 번 대조하세요.",
            badges=list(base.badges) if base else [],
        )
    elif key in ALARM_CARDS:
        card = ALARM_CARDS[key].model_copy()
    elif key in CODE_CARDS:
        card = CODE_CARDS[key].model_copy()
    elif key in HOWTO_CARDS:
        card = HOWTO_CARDS[key].model_copy()
    else:
        return AnswerCard(
            kind="generic",
            title="매뉴얼에 없음",
            lead="그 번호·기능 이름으로는 못 찾았습니다.",
        )
    card.note = machine_note(key, machine)
    return card


def card_to_text(card: AnswerCard) -> str:
    parts = [card.lead, card.title, card.cause, card.action, card.caution, card.note]
    return "\n".join(part for part in parts if part)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "port": APP_PORT,
        "host": APP_HOST,
        "db": str(DB_PATH),
        "manual_chunks": chunk_count() or len(CHUNKS),
        "llm_ready": model_ready(),
        "chroma": "file",
        "forbidden_ports": sorted(FORBIDDEN_PORTS),
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="질문을 입력하세요.")
    session_id = req.session_id or str(uuid.uuid4())
    machine = req.machine if req.machine in MACHINES else "mill_0i"
    mode = "classic" if req.mode == "classic" else "workshop"
    hits = search_manuals(message)
    card = build_card(message, hits, machine)
    source = "chip" if req.source == "chip" else "chat"
    if source == "chat" or mode == "classic":
        core = select_core_hits(message, hits)
        if not core:
            answer = MISSING_ANSWER
            out_card = None
            used = []
        else:
            answer = generate_chat_answer(message, core, machine) or build_chat_answer(
                message, core
            )
            out_card = None
            used = core
    elif card.title == "매뉴얼에 없음":
        answer = MISSING_ANSWER
        out_card = card if mode == "workshop" else None
        used = []
    else:
        answer = card_to_text(card)
        out_card = card
        used = hits
    save_message(session_id, "user", message)
    save_message(session_id, "assistant", answer)
    sources = [
        Source(title=hit["title"], file=hit["file"], score=hit["score"])
        for hit in used
    ]
    return ChatResponse(
        answer=answer,
        sources=sources,
        session_id=session_id,
        card=out_card,
        machine=machine,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="js")
app.mount("/classic", StaticFiles(directory=FRONTEND / "classic", html=True), name="classic")


if __name__ == "__main__":
    import uvicorn

    print(f"FANUC 매뉴얼 챗봇: http://{APP_HOST}:{APP_PORT}")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)

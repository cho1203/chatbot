from __future__ import annotations

MACHINES = {
    "mill_0i": "0i 밀링",
    "lathe_0i": "0i 선반",
    "mill_31i": "31i",
}


def _manual_block(hits: list[dict], limit: int = 2, body_len: int = 500) -> str:
    evidence = []
    for hit in hits[:limit]:
        evidence.append(f"[{hit.get('title', '')}]\n{hit.get('body', '')[:body_len]}")
    return "\n\n".join(evidence) if evidence else "(관련 매뉴얼 없음)"


def build_prompt(query: str, hits: list[dict], machine: str) -> str:
    label = MACHINES.get(machine, "0i 밀링")
    manual = _manual_block(hits, limit=4, body_len=800)
    return f"""당신은 FANUC 현장 조수입니다. 아래 매뉴얼 조각에만 근거해 답하세요.
없는 내용은 만들지 마세요. JSON만 출력하세요.

기종: {label}
질문: {query}

매뉴얼:
{manual}

JSON 키: kind, title, lead, cause, action, caution, note, badges
- kind는 alarm, code, howto, generic 중 하나
- lead는 한 줄 현장 말투
- cause는 원인
- action은 지금 할 일
- caution은 주의
- note는 이 기종에서 볼 것
- badges는 G43, 410 같은 짧은 코드 배열
"""


def build_chat_prompt(query: str, hits: list[dict], machine: str) -> str:
    label = MACHINES.get(machine, "0i 밀링")
    manual = _manual_block(hits, limit=2, body_len=500)
    return f"""당신은 FANUC 매뉴얼 안내입니다. 아래 매뉴얼 1~2개에만 근거해 짧게 답하세요.
없는 내용은 만들지 마세요. JSON이나 원인/조치 칸을 쓰지 마세요. 설명 문장만 쓰세요.

기종: {label}
질문: {query}

매뉴얼:
{manual}

답변:
"""

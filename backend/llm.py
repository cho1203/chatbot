from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from prompts import MACHINES, build_chat_prompt, build_prompt

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL_PATH = os.getenv("LLM_MODEL_PATH", "").strip()

_LLM = None
_LLM_FAIL = False


def model_ready() -> bool:
    path = Path(MODEL_PATH) if MODEL_PATH else None
    return bool(path and path.is_file())


def _load_llm():
    global _LLM, _LLM_FAIL
    if _LLM is not None or _LLM_FAIL or not model_ready():
        return _LLM
    try:
        from llama_cpp import Llama

        _LLM = Llama(
            model_path=str(Path(MODEL_PATH)),
            n_ctx=2048,
            n_threads=4,
            verbose=False,
        )
        return _LLM
    except Exception:
        _LLM_FAIL = True
        return None


def _parse_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def generate_card_data(query: str, hits: list[dict], machine: str) -> dict | None:
    llm = _load_llm()
    if llm is None:
        return None
    prompt = build_prompt(query, hits, machine)
    output = llm(
        prompt,
        max_tokens=500,
        temperature=0.1,
        stop=["\n\n질문:", "```"],
    )
    text = output["choices"][0]["text"]
    data = _parse_json(text)
    if not data:
        return None
    badges = data.get("badges") or []
    if isinstance(badges, str):
        badges = [badges]
    return {
        "kind": str(data.get("kind") or "generic"),
        "title": str(data.get("title") or ""),
        "lead": str(data.get("lead") or ""),
        "cause": str(data.get("cause") or ""),
        "action": str(data.get("action") or ""),
        "caution": str(data.get("caution") or ""),
        "note": str(data.get("note") or f"{MACHINES.get(machine, '')} 기준으로 보세요."),
        "badges": [str(item) for item in badges],
    }


def generate_chat_answer(query: str, hits: list[dict], machine: str) -> str | None:
    llm = _load_llm()
    if llm is None or not hits:
        return None
    prompt = build_chat_prompt(query, hits[:2], machine)
    try:
        output = llm(
            prompt,
            max_tokens=350,
            temperature=0.1,
            stop=["\n\n질문:", "```", "매뉴얼:"],
        )
    except Exception:
        return None
    text = (output["choices"][0]["text"] or "").strip()
    if len(text) < 10:
        return None
    if text.startswith("{") and "cause" in text:
        return None
    return text[:800]

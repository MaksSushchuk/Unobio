"""Check that the configured model is reachable and returns valid JSON.

    python -m analytics.llm                 # uses LLM_* from env / server/.env
    python -m analytics.llm "Say hi"        # free-form prompt
"""
from __future__ import annotations

import asyncio
import sys

from .base import LLMRequest, Message
from .config import LLMSettings
from .json_utils import extract_json
from . import make_llm

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "model_name": {"type": "string"}, "two_plus_two": {"type": "integer"}},
    "required": ["ok", "two_plus_two"],
}


async def main() -> None:
    s = LLMSettings.from_env()
    print(f"provider={s.provider} model={s.model} base_url={s.base_url or '-'} num_ctx={s.num_ctx} json_mode={s.json_mode}")
    llm = make_llm(s)
    prompt = " ".join(sys.argv[1:])
    try:
        if prompt:
            r = await llm.complete(LLMRequest(messages=[Message(role="user", content=prompt)], tag="ping"))
            print(r.text)
        else:
            r = await llm.complete(LLMRequest(
                messages=[Message(role="system", content="Answer with JSON only."),
                          Message(role="user", content="Return {\"ok\": true, \"model_name\": <your name>, \"two_plus_two\": <number>}")],
                json_schema=PING_SCHEMA, tag="ping"))
            data = extract_json(r.text)
            print(f"JSON ok: {data}")
            if data.get("two_plus_two") != 4:
                print("WARNING: wrong arithmetic — model quality may be too low for analysis")
        print(f"tokens in/out: {r.input_tokens}/{r.output_tokens}, latency {r.latency_s}s, cost ${r.cost_usd}, cached={r.cached}")
    finally:
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())

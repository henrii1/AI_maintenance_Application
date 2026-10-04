"""Answer-quality eval against the real Claude API (~$0.30 per run with Haiku 4.5).

Usage: uv run python scripts/eval_answers.py [--model claude-sonnet-5-5]
Checks per golden question: answer cites at least one page; the expected figure (if any) is among the
chosen figure_ids; every number-with-unit in the answer appears in the retrieved excerpt text.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.embeddings import get_embedder  # noqa: E402
from backend.llm.chat import ChatContext, stream_answer  # noqa: E402
from backend.llm.client import make_client  # noqa: E402
from backend.llm.common import estimate_cost  # noqa: E402
from backend.retrieval.search import Retriever  # noqa: E402

SET_ID = "ariel-kbe"
NUM_UNIT = re.compile(r"(\d+(?:[.,/]\d+)?)\s?(?:ft[·\-\s]?lbs?|lb[·\-\s]?ft|N·m|Nm|in\.|inch(?:es)?|mm|psi[ag]?|°F|°C|%)",
                      re.I)


async def run(model: str) -> int:
    s = get_settings()
    retriever = Retriever.open(s.content_dir, get_embedder(s.embed_cache_dir))
    client = make_client(s)
    set_info = retriever.get_set(SET_ID)
    cases = yaml.safe_load((Path(__file__).resolve().parents[1] / "tests/golden/retrieval.yaml").read_text())
    total, failures = 0.0, 0
    for case in cases:
        q = case["query"]
        chunks = retriever.search_chunks(SET_ID, q)
        cands = retriever.figure_candidates(SET_ID, q, chunks)
        ctx = ChatContext(equipment=set_info["equipment"], chunks=chunks, candidates=cands, history=[], question=q,
                          resolve=lambda n: retriever.resolve_figure(SET_ID, n))
        text, assets = "", {"figure_ids": [], "citations": []}
        async for kind, payload in stream_answer(client, model, ctx):
            if kind == "assets":
                assets = payload
            elif kind == "final":
                text = payload["text"]
                total += estimate_cost(model, payload["usage"])
        evidence = " ".join(c.text for c in chunks)
        unsupported = sorted({m.group(0) for m in NUM_UNIT.finditer(text) if m.group(1) not in evidence})
        cited = bool(assets["citations"])
        fig_ok = case["figure"] in assets["figure_ids"] if "figure" in case else True
        ok = cited and fig_ok and not unsupported
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {q}\n      cited={cited} figure_ok={fig_ok} figures={assets['figure_ids']}"
              + (f"\n      unsupported specs: {unsupported}" if unsupported else ""))
    print(f"\n{len(cases) - failures}/{len(cases)} passed · model={model} · cost=${total:.3f}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=get_settings().chat_model)
    raise SystemExit(asyncio.run(run(ap.parse_args().model)))

"""FastAPI inference server for fine-tuned THX-01 Dofus decision model.

Production-grade serving targeting ~10ms per request (native THX-01 latency)
with GPU micro-batching.

Usage:
    python pipeline/serve_thx01.py --model doofz/THX-01 --port 8095
    # or fine-tuned:
    python pipeline/serve_thx01.py --model models/finetuned/thx01-dofus --port 8095

Endpoints:
  POST /v1/decide      Single decision (state + questions)
  POST /v1/decide/batch Batched decisions
  GET  /health         Health check
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="THX-01 Dofus Decision Server", version="1.0.0")

_agent: Any = None
_model_name: str = ""


class DecideRequest(BaseModel):
    state: str
    questions: dict[str, Any]


class DecideResponse(BaseModel):
    answers: dict[str, Any]
    latency_ms: float


class DecideBatchItem(BaseModel):
    state: str
    questions: dict[str, Any]


class DecideBatchRequest(BaseModel):
    items: list[DecideBatchItem]


@app.on_event("startup")
def _load_model() -> None:
    global _agent, _model_name
    import thx01

    _agent = thx01.load(_model_name)
    print(f"Loaded THX-01 model: {_model_name}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": _model_name}


@app.post("/v1/decide", response_model=DecideResponse)
def decide(req: DecideRequest) -> DecideResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    start = time.perf_counter()
    result = _agent.decide(req.state, req.questions)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return DecideResponse(
        answers=result.get("answers", {}),
        latency_ms=round(elapsed_ms, 3),
    )


@app.post("/v1/decide/batch")
def decide_batch(req: DecideBatchRequest) -> dict[str, Any]:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    start = time.perf_counter()
    results = []
    for item in req.items:
        res = _agent.decide(item.state, item.questions)
        results.append(res.get("answers", {}))
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "answers": results,
        "latency_ms": round(elapsed_ms, 3),
        "per_decision_ms": round(elapsed_ms / max(len(req.items), 1), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve THX-01 Dofus decision model")
    parser.add_argument("--model", required=True, help="Model path or HF hub ID (e.g. doofz/THX-01)")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    global _model_name
    _model_name = args.model

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
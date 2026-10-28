from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable

@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0

@dataclass
class LLMStats:
    calls: int = 0
    failures: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_s: float = 0.0
    in_flight: int = 0
    last_error: str = ""
    per_model: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        avg = self.total_latency_s / self.calls if self.calls else 0.0
        return {
            "calls": self.calls, "failures": self.failures, "retries": self.retries,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "avg_latency_s": round(avg, 2), "in_flight": self.in_flight, "last_error": self.last_error,
            "per_model": dict(self.per_model),
        }

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)

class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 180.0,
        max_concurrency: int = 4,
        max_retries: int = 2,
        keep_alive: str = "30m",
        num_ctx: int = 8192,
        num_predict: int = 700,
        use_json_schema: bool = True,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.schema_supported = use_json_schema
        self._sem = asyncio.Semaphore(max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            limits=httpx.Limits(max_connections=max(8, max_concurrency * 2)),
            transport=transport,
        )
        self.stats = LLMStats()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def health(self) -> Dict[str, Any]:
        try:
            v = await self._client.get("/api/version", timeout=5.0)
            version = v.json().get("version", "?") if v.status_code == 200 else "?"
            models = await self.list_models()
            return {"ok": True, "version": version, "models": models, "base_url": self.base_url}
        except Exception as e:
            return {"ok": False, "error": f"cannot reach Ollama at {self.base_url}: {e.__class__.__name__}: {e}",
                    "models": [], "base_url": self.base_url}

    async def list_models(self) -> List[str]:
        r = await self._client.get("/api/tags", timeout=10.0)
        r.raise_for_status()
        return sorted(m.get("name", "") for m in r.json().get("models", []))

    async def ensure_models(self, models: List[str], auto_pull: bool = False) -> List[str]:
        
        have = set(await self.list_models())

        def present(m: str) -> bool:
            return m in have or (":" not in m and f"{m}:latest" in have)

        missing = [m for m in dict.fromkeys(models) if not present(m)]
        if missing and auto_pull:
            for m in missing:
                await self.pull(m)
            have = set(await self.list_models())
            missing = [m for m in missing if not present(m)]
        return missing

    async def pull(self, model: str) -> None:
        async with self._client.stream("POST", "/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
            if r.status_code != 200:
                raise LLMError(f"pull {model} failed: HTTP {r.status_code}", retryable=False)
            async for line in r.aiter_lines():
                if line.strip():
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise LLMError(f"pull {model} failed: {msg['error']}", retryable=False)

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.8,
        seed: Optional[int] = None,
        num_predict: Optional[int] = None,
    ) -> LLMResponse:
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": self.num_ctx,
            "num_predict": num_predict or self.num_predict,
        }
        if seed is not None:
            options["seed"] = seed
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            payload: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": options,
                "keep_alive": self.keep_alive,
            }
            if schema is not None:
                payload["format"] = schema if self.schema_supported else "json"
            try:
                async with self._sem:
                    self.stats.in_flight += 1
                    t0 = time.perf_counter()
                    try:
                        r = await self._client.post("/api/chat", json=payload)
                    finally:
                        self.stats.in_flight -= 1
                    dt = time.perf_counter() - t0
                if r.status_code == 400 and schema is not None and self.schema_supported and "format" in r.text.lower():

                    self.schema_supported = False
                    continue
                if r.status_code == 404:
                    raise LLMError(f"model {model!r} not found on the Ollama server — run `ollama pull {model}`", retryable=False)
                if r.status_code >= 500:
                    raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code != 200:
                    raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:200]}", retryable=False)
                data = r.json()
                if data.get("error"):
                    raise LLMError(str(data["error"]), retryable=False)
                content = (data.get("message") or {}).get("content", "") or data.get("response", "")
                content = _THINK.sub("", content).strip()
                self.stats.calls += 1
                self.stats.prompt_tokens += int(data.get("prompt_eval_count") or 0)
                self.stats.completion_tokens += int(data.get("eval_count") or 0)
                self.stats.total_latency_s += dt
                self.stats.per_model[model] = self.stats.per_model.get(model, 0) + 1
                return LLMResponse(content, model, int(data.get("prompt_eval_count") or 0),
                                   int(data.get("eval_count") or 0), dt)
            except LLMError as e:
                last_exc = e
                if not e.retryable:
                    break
            except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError) as e:
                last_exc = LLMError(f"{e.__class__.__name__}: {e}")
            if attempt < self.max_retries:
                self.stats.retries += 1
                await asyncio.sleep(min(20.0, (1.5 ** attempt)) + random.random() * 0.5)
        self.stats.failures += 1
        self.stats.last_error = str(last_exc)
        raise last_exc if isinstance(last_exc, LLMError) else LLMError(str(last_exc))

"""Provider-independent model inference with durable, explicit usage semantics.

Configuration is opt-in through CONFLUX_MODEL_PROVIDER. Supported providers:
- ``openai_compatible``: Chat Completions-compatible hosted or local endpoint.
- ``ollama``: local Ollama ``/api/chat`` endpoint.
- ``simulated``: deterministic development/test echo only (not real inference).

Model admission is process-local; deployment-wide concurrency needs an external
shared scheduler. Calls are supervised by Engine workers, which can terminate
blocked provider requests, but billing after a lost response remains uncertain.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


class InferenceProvider(Protocol):
    def complete(self, prompt: str, *, max_output_tokens: int,
                 deadline_at: float | None = None) -> dict: ...


def _secret_setting(name: str) -> str:
    direct = os.environ.get(name, "")
    path = os.environ.get(name + "_FILE", "")
    if direct and path:
        raise RuntimeError(f"configure only one of {name} and {name}_FILE")
    if direct:
        return direct.strip()
    if path:
        try:
            with open(path, encoding="utf-8") as stream:
                return stream.read().strip()
        except OSError as exc:
            raise RuntimeError(f"cannot read configured {name}_FILE") from exc
    return ""


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _require_safe_transport(base_url: str, has_key: bool, production: bool) -> None:
    """Refuse configurations that would send credentials or prompts in cleartext.

    HTTPS is always accepted. Plain HTTP is accepted for loopback endpoints and,
    outside production, for other hosts that carry no credential (for example a
    trusted Docker-internal model server). A key is never sent over plain HTTP
    to a non-loopback host.
    """
    parts = urlsplit(base_url)
    if parts.scheme == "https":
        return
    if parts.scheme != "http":
        raise RuntimeError("model base URL must use http or https")
    if parts.hostname in _LOOPBACK_HOSTS:
        return
    if has_key:
        raise RuntimeError("model API key requires an https endpoint unless loopback")
    if production:
        raise RuntimeError("production model endpoints must use https unless loopback")


def _float_setting(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a nonnegative number") from exc
    if value < 0:
        raise ValueError(f"{name} must be a nonnegative number")
    return value


def _http_json(url: str, body: dict, headers: dict[str, str],
               deadline_at: float | None) -> dict:
    timeout = 60.0 if deadline_at is None else max(0.1, deadline_at - time.time())
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Do not return response headers/body: providers can echo credentials or
        # prompt data in diagnostics. The worker records this as a failed call;
        # interrupted/timeout billing remains explicitly uncertain in recovery.
        raise RuntimeError(f"inference provider returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"inference provider request failed: {type(exc).__name__}") from None
    if not isinstance(payload, dict):
        raise RuntimeError("inference provider returned a non-object response")
    return payload


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key: str = ""
    billable: bool = True
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    def complete(self, prompt: str, *, max_output_tokens: int,
                 deadline_at: float | None = None) -> dict:
        url = self.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload = _http_json(url, {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max(1, max_output_tokens),
        }, headers, deadline_at)
        try:
            completion = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            input_tokens = max(0, int(usage.get("prompt_tokens", 0)))
            output_tokens = max(0, int(usage.get("completion_tokens", 0)))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("inference provider returned an invalid completion") from exc
        provider_cost = usage.get("cost_usd", usage.get("cost"))
        cost = (max(0.0, float(provider_cost))
                if isinstance(provider_cost, (int, float))
                and not isinstance(provider_cost, bool) else None)
        cost_source = "provider_reported" if cost is not None else None
        if (cost is None and self.input_cost_per_million is not None
                and self.output_cost_per_million is not None
                and input_tokens + output_tokens):
            cost = (input_tokens * self.input_cost_per_million
                    + output_tokens * self.output_cost_per_million) / 1_000_000
            cost_source = "configured_rate_card"
        result_usage = {
            "provider": "openai_compatible", "provider_calls": 1,
            "usage_source": "provider" if "usage" in payload else "unavailable",
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "model_tokens": input_tokens + output_tokens,
            "token_consumption_uncertain": "usage" not in payload,
            "cost_consumption_uncertain": bool(self.billable and cost is None),
        }
        if cost is not None:
            result_usage["cost_usd"] = cost
            result_usage["cost_source"] = cost_source
        return {"completion": completion, "usage": result_usage}


@dataclass
class OllamaProvider:
    base_url: str
    model: str
    billable: bool = False

    def complete(self, prompt: str, *, max_output_tokens: int,
                 deadline_at: float | None = None) -> dict:
        url = self.base_url.rstrip("/")
        if not url.endswith("/api/chat"):
            url += "/api/chat"
        payload = _http_json(url, {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": max(1, max_output_tokens)},
        }, {}, deadline_at)
        try:
            completion = payload["message"]["content"]
            counted = "prompt_eval_count" in payload and "eval_count" in payload
            input_tokens = max(0, int(payload.get("prompt_eval_count", 0)))
            output_tokens = max(0, int(payload.get("eval_count", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Ollama returned an invalid completion") from exc
        return {"completion": completion, "usage": {
            "provider": "ollama", "provider_calls": 1,
            "usage_source": "provider" if counted else "unavailable",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model_tokens": input_tokens + output_tokens,
            "token_consumption_uncertain": not counted,
        }}


class SimulatedProvider:
    """Deterministic test/development responder; never represents real usage."""
    billable = False

    def complete(self, prompt: str, *, max_output_tokens: int,
                 deadline_at: float | None = None) -> dict:
        completion = f"[model.complete] {prompt[:200]}"
        estimated = len(prompt.split()) + len(completion.split())
        return {"completion": completion, "usage": {
            "provider": "simulated", "provider_calls": 0,
            "usage_source": "estimated", "input_tokens": 0,
            "output_tokens": 0, "model_tokens": estimated,
            "token_consumption_uncertain": False,
        }}


def provider_from_environment() -> InferenceProvider:
    kind = os.environ.get("CONFLUX_MODEL_PROVIDER", "simulated").strip().lower()
    if kind == "simulated":
        return SimulatedProvider()
    if kind == "openai_compatible":
        base_url = os.environ.get("CONFLUX_MODEL_BASE_URL", "").strip()
        model = os.environ.get("CONFLUX_MODEL_NAME", "").strip()
        if not base_url or not model:
            raise RuntimeError("openai_compatible requires CONFLUX_MODEL_BASE_URL and CONFLUX_MODEL_NAME")
        api_key = _secret_setting("CONFLUX_MODEL_API_KEY")
        _require_safe_transport(
            base_url, bool(api_key),
            os.environ.get("UNIFIED_ENV", "").strip().lower() in {"prod", "production"})
        return OpenAICompatibleProvider(
            base_url, model, api_key,
            os.environ.get("CONFLUX_MODEL_BILLABLE", "true").strip().lower()
            not in {"0", "false", "no"},
            _float_setting("CONFLUX_MODEL_INPUT_COST_PER_MILLION"),
            _float_setting("CONFLUX_MODEL_OUTPUT_COST_PER_MILLION"))
    if kind == "ollama":
        return OllamaProvider(
            # Compose passes unset variables as "", so fall back explicitly.
            os.environ.get("CONFLUX_MODEL_BASE_URL", "").strip() or "http://127.0.0.1:11434",
            os.environ.get("CONFLUX_MODEL_NAME", "").strip() or "llama3.2")
    raise RuntimeError("CONFLUX_MODEL_PROVIDER must be simulated, openai_compatible, or ollama")


@dataclass
class ModelRouter:
    max_concurrent: int = 1
    provider: InferenceProvider | None = None

    def __post_init__(self):
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be at least one")
        self.provider = self.provider or provider_from_environment()
        # Production startup must not depend on inference being configured; the
        # simulated provider is refused per model call in complete() instead.
        self._simulated_refused = (
            os.environ.get("UNIFIED_ENV", "").strip().lower() in {"prod", "production"}
            and isinstance(self.provider, SimulatedProvider))
        self._sem = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._tokens_used = 0
        self._task_tokens: dict[str, int] = {}

    def restore_task_usage(self, task_id: str, tokens_used: int) -> None:
        """Reconcile in-memory accounting from durable committed receipts."""
        with self._lock:
            prior = self._task_tokens.get(task_id, 0)
            reconciled = max(prior, max(0, int(tokens_used)))
            self._task_tokens[task_id] = reconciled
            self._tokens_used += reconciled - prior

    def forget_task(self, task_id: str) -> None:
        with self._lock:
            self._task_tokens.pop(task_id, None)

    def complete(self, prompt: str, budget_tokens: int = 4096,
                 task_id: str | None = None, deadline_at: float | None = None,
                 cancel_check=None, supervised: bool = False) -> dict:
        """Call the configured provider, reporting actual or estimated usage.

        Provider-reported token enforcement is post-response because the
        provider's tokenizer is authoritative; output is bounded with the
        provider's max-token parameter, but input/context limits remain
        provider-specific and need model metadata qualification.
        """
        if self._simulated_refused:
            return {"ok": False, "error": "production requires a real CONFLUX_MODEL_PROVIDER",
                    "usage": {"queued_seconds": 0, "provider_calls": 0}}
        queued_at = time.monotonic()
        acquired = False
        wait_deadline = min(deadline_at or (time.time() + 30), time.time() + 30)
        while not supervised and not acquired:
            acquired = self._sem.acquire(
                timeout=min(0.05, max(0.0, wait_deadline - time.time())))
            if acquired:
                break
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "error": "model call cancelled",
                        "usage": {"queued_seconds": time.monotonic() - queued_at}}
            if time.time() >= wait_deadline:
                return {"ok": False, "budget": "time_seconds",
                        "error": "model queue/deadline budget exceeded",
                        "usage": {"queued_seconds": time.monotonic() - queued_at}}
        try:
            if cancel_check and cancel_check():
                return {"ok": False, "cancelled": True, "error": "model call cancelled",
                        "usage": {"queued_seconds": 0}}
            if deadline_at is not None and time.time() >= deadline_at:
                return {"ok": False, "budget": "time_seconds",
                        "error": "task time budget exceeded", "usage": {"queued_seconds": 0}}
            budget_scope = task_id or "_unscoped"
            with self._lock:
                task_used = 0 if supervised else self._task_tokens.get(budget_scope, 0)
            remaining = (max(0, int(budget_tokens)) if supervised else
                         max(0, int(budget_tokens) - task_used))
            if remaining <= 0:
                return {"ok": False, "budget": "model_tokens",
                        "error": "model token budget exhausted",
                        "usage": {"tokens_used": task_used,
                                  "budget_tokens": max(0, int(budget_tokens))}}
            # Reserve input space before dispatch: the provider's input tokens
            # count against the same budget as output. UTF-8 byte length is used
            # as a conservative estimate of the prompt body only; chat-template
            # tokens are not estimated, so the post-response check remains the
            # backstop for them.
            reserved_input = len(prompt.encode("utf-8"))
            if reserved_input >= remaining:
                return {"ok": False, "budget": "model_tokens",
                        "error": "model prompt does not fit the remaining token budget",
                        "usage": {"tokens_used": task_used, "provider_calls": 0,
                                  "dispatched": False,
                                  "input_reservation_tokens": reserved_input,
                                  "budget_tokens": max(0, int(budget_tokens))}}
            output_cap = remaining - reserved_input
            queued_seconds = max(0.0, time.monotonic() - queued_at)
            output = self.provider.complete(prompt, max_output_tokens=output_cap,
                                            deadline_at=deadline_at)
            usage = dict(output.get("usage", {}))
            used = max(0, int(usage.get("model_tokens", 0) or 0))
            with self._lock:
                if supervised:
                    # Engine passes the remaining task allowance and restores
                    # cumulative counts from durable receipts in the parent.
                    task_used = used
                    self._tokens_used += used
                else:
                    task_used = self._task_tokens.get(budget_scope, 0) + used
                    self._task_tokens[budget_scope] = task_used
                    self._tokens_used += used
                total_used = self._tokens_used
            usage.update({"elapsed_ms": int(max(0.0, time.monotonic() - queued_at) * 1000),
                          "queued_seconds": queued_seconds,
                          "tokens_used": task_used,
                          "tokens_used_total": total_used})
            if used > remaining:
                return {"ok": False, "budget": "model_tokens",
                        "error": "provider-reported usage exceeded remaining token budget",
                        "usage": usage}
            return {"ok": True, "completion": output["completion"], "usage": usage}
        except Exception as exc:
            # Provider exceptions can follow request acceptance; they do not
            # prove zero billable consumption. The supervised UNKNOWN attempt
            # and durable uncertain_provider_calls metric expose that ambiguity.
            return {"ok": False, "error": f"inference provider failed: {type(exc).__name__}",
                    "usage": {"provider": type(self.provider).__name__,
                              "provider_calls": 1,
                              "usage_source": "unavailable",
                              "consumption_status": "uncertain",
                              "token_consumption_uncertain": True,
                              "cost_consumption_uncertain": bool(
                                  getattr(self.provider, "billable", False)),
                              "uncertain_provider_calls": int(
                                  bool(getattr(self.provider, "billable", False)))}}
        finally:
            if acquired:
                self._sem.release()

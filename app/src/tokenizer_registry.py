"""Tokenizer registry multi-runtime.

Soporta:
- llama.cpp / vLLM: endpoint /tokenize (exacto)
- OpenAI-compatible: /tokenize o /v1/tokenize si existe; si no, calibracion via usage
- Ollama: /api/tokenize si existe; si no, calibracion via prompt_eval_count
- LM Studio / Strata: OpenAI-compatible; igual que arriba
- tokenizer.json local via `tokenizers` (opcional)
- heuristico conservador como fallback

El control plane no carga pesos de modelo. Solo habla HTTP con el runtime o lee
un tokenizer.json montado.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request
from threading import RLock
from typing import Any, Dict, Optional, Tuple

import sqlalchemy as sa

from src.token_budget import estimate_tokens


class TokenizerUnavailable(Exception):
    pass


def _json_request(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _base_variants(base_url: str) -> list:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return []
    variants = [base]
    if base.endswith("/v1"):
        root = base[:-3].rstrip("/")
        if root and root not in variants:
            variants.append(root)
    elif not base.endswith("/v1"):
        variants.append(base + "/v1")
    return variants


class HeuristicTokenizer:
    name = "heuristic"
    status = "HEURISTIC"

    def __init__(self, chars_per_token: float = 3.6):
        self.chars_per_token = float(chars_per_token or 3.6)

    def count(self, text: str) -> int:
        return estimate_tokens(text, self.chars_per_token)

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "source": self.name, "status": self.status}


class HttpTokenizer:
    """Tokenizer HTTP para llama.cpp, OpenAI-compatible y Ollama."""

    def __init__(
        self,
        base_url: str,
        kind: str = "auto",
        model: Optional[str] = None,
        timeout_ms: int = 2000,
        chars_per_token: float = 3.6,
        calibration: Optional[Dict[str, Any]] = None,
    ):
        self.base_url = base_url
        self.kind = (kind or "auto").lower()
        self.model = model
        self.timeout = max(0.2, float(timeout_ms or 2000) / 1000.0)
        self.calibration = calibration or {}
        self.chars_per_token = float(
            self.calibration.get("chars_per_token") or chars_per_token or 3.6
        )
        self.name = f"http:{self.kind}"
        self.status = "EXACT"
        self._tokenize_url: Optional[str] = None

    def _candidate_urls(self) -> list:
        urls = []
        variants = _base_variants(self.base_url)
        if self.kind in ("llamacpp", "llama.cpp"):
            urls.extend(v + "/tokenize" for v in variants[:1])
        elif self.kind == "ollama":
            urls.extend(v + "/api/tokenize" for v in variants[:1])
            urls.extend(v + "/tokenize" for v in variants[:1])
        else:
            for v in variants:
                urls.extend([v + "/tokenize", v + "/v1/tokenize"])
        return list(dict.fromkeys(urls))

    @staticmethod
    def _extract_tokens(data: Dict[str, Any]) -> int:
        for key in ("tokens", "token_ids", "ids"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        for key in ("n_tokens", "token_count", "count"):
            value = data.get(key)
            if isinstance(value, (int, float)) and int(value) > 0:
                return int(value)
        usage = data.get("usage") or {}
        value = usage.get("prompt_tokens") or usage.get("input_tokens")
        if isinstance(value, (int, float)) and int(value) > 0:
            return int(value)
        return 0

    def _try_tokenize(self, url: str, text: str) -> int:
        payloads = ({"content": text}, {"input": text}, {"prompt": text})
        last_error = None
        for payload in payloads:
            try:
                data = _json_request(url, payload, self.timeout)
                count = self._extract_tokens(data)
                if count > 0:
                    self._tokenize_url = url
                    return count
            except Exception as exc:
                last_error = exc
        raise TokenizerUnavailable(f"no tokenize endpoint at {url}: {last_error}")

    def count(self, text: str) -> int:
        if self._tokenize_url:
            try:
                return self._try_tokenize(self._tokenize_url, text)
            except Exception:
                self._tokenize_url = None
        for url in self._candidate_urls():
            try:
                return self._try_tokenize(url, text)
            except Exception:
                continue
        raise TokenizerUnavailable(f"tokenizer unavailable at {self.base_url}")

    def _chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def _generate_url(self) -> str:
        return self.base_url.rstrip("/") + "/api/generate"

    def calibrate(self, sample: Optional[str] = None) -> Dict[str, Any]:
        sample = sample or (
            "Harness2 tokenizer calibration sample. "
            "Esta muestra mide la relacion entre caracteres y tokens del modelo local."
        )
        if self.kind == "ollama":
            try:
                data = _json_request(self._generate_url(), {
                    "model": self.model or "local-model",
                    "prompt": sample,
                    "stream": False,
                    "options": {"num_predict": 1, "temperature": 0},
                }, self.timeout)
                tokens = int(data.get("prompt_eval_count") or 0)
                if tokens > 0:
                    ratio = len(sample) / tokens
                    return {
                        "status": "CALIBRATED",
                        "method": "ollama_prompt_eval_count",
                        "sample_chars": len(sample),
                        "sample_tokens": tokens,
                        "chars_per_token": round(ratio, 4),
                    }
            except Exception as exc:
                return {"status": "FALLBACK", "method": "ollama", "error": str(exc)}
        try:
            data = _json_request(self._chat_url(), {
                "model": self.model or "local-model",
                "messages": [{"role": "user", "content": sample}],
                "max_tokens": 1,
                "temperature": 0,
                "stream": False,
            }, max(self.timeout, 10.0))
            tokens = int((data.get("usage") or {}).get("prompt_tokens") or 0)
            if tokens > 0:
                ratio = len(sample) / tokens
                return {
                    "status": "CALIBRATED",
                    "method": "openai_usage",
                    "sample_chars": len(sample),
                    "sample_tokens": tokens,
                    "chars_per_token": round(ratio, 4),
                }
        except Exception as exc:
            return {"status": "FALLBACK", "method": "openai_usage", "error": str(exc)}
        return {"status": "FALLBACK", "method": "http", "error": "no tokenizer endpoint"}

    def health(self) -> Dict[str, Any]:
        started = time.time()
        try:
            tokens = self.count("Harness2 tokenizer health check")
            return {
                "ok": True,
                "source": self.name,
                "status": self.status,
                "tokens": tokens,
                "latency_ms": round((time.time() - started) * 1000, 1),
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": self.name,
                "status": "UNAVAILABLE",
                "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000, 1),
            }


class TokenizerFileAdapter:
    name = "tokenizers_file"
    status = "EXACT"

    def __init__(self, path: str):
        self.path = path
        try:
            from tokenizers import Tokenizer  # type: ignore
        except Exception as exc:
            raise TokenizerUnavailable(f"tokenizers library not installed: {exc}")
        self._tokenizer = Tokenizer.from_file(path)

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text).ids)

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "source": self.name, "status": self.status}


class CalibratedHeuristicTokenizer(HeuristicTokenizer):
    status = "CALIBRATED"

    def __init__(self, chars_per_token: float, calibration: Optional[Dict[str, Any]] = None):
        super().__init__(chars_per_token)
        self.calibration = calibration or {}
        self.name = "calibrated_heuristic"


class TokenizerRegistry:
    def __init__(self):
        self._lock = RLock()
        self._adapters: Dict[str, Tuple[float, Any]] = {}
        self._counts: Dict[str, Tuple[float, int, str]] = {}

    def adapter_for_profile(self, profile: Dict[str, Any]):
        name = str(profile.get("name") or "unknown")
        now = time.time()
        with self._lock:
            cached = self._adapters.get(name)
            if cached and now - cached[0] < 60:
                return cached[1]
        kind = str(profile.get("tokenizer_kind") or "auto").lower()
        url = profile.get("tokenizer_url")
        path = profile.get("tokenizer_path")
        chars_per_token = float(profile.get("chars_per_token") or 3.6)
        calibration = profile.get("tokenizer_calibration") or {}
        if isinstance(calibration, str):
            try:
                calibration = json.loads(calibration)
            except Exception:
                calibration = {}
        adapter = None
        if kind in ("llamacpp", "llama.cpp", "openai_compatible", "ollama") and url:
            adapter = HttpTokenizer(
                url, kind=kind, model=profile.get("tokenizer_model"),
                timeout_ms=int(profile.get("tokenizer_timeout_ms") or 2000),
                chars_per_token=chars_per_token,
                calibration=calibration,
            )
        elif kind == "tokenizers_file" and path:
            adapter = TokenizerFileAdapter(path)
        elif kind == "auto":
            if url:
                adapter = HttpTokenizer(
                    url, kind="auto", model=profile.get("tokenizer_model"),
                    timeout_ms=int(profile.get("tokenizer_timeout_ms") or 2000),
                    chars_per_token=chars_per_token,
                    calibration=calibration,
                )
            elif path:
                adapter = TokenizerFileAdapter(path)
        if adapter is None:
            if calibration.get("chars_per_token"):
                adapter = CalibratedHeuristicTokenizer(
                    float(calibration["chars_per_token"]), calibration
                )
            else:
                adapter = HeuristicTokenizer(chars_per_token)
        with self._lock:
            self._adapters[name] = (now, adapter)
        return adapter

    def count_for_profile(self, profile: Dict[str, Any], text: str) -> int:
        key = hashlib.sha256(
            (str(profile.get("name")) + "\0" + str(text or "")).encode("utf-8")
        ).hexdigest()
        now = time.time()
        with self._lock:
            cached = self._counts.get(key)
            if cached and now - cached[0] < 30:
                return cached[1]
        adapter = self.adapter_for_profile(profile)
        try:
            count = int(adapter.count(text))
            source = getattr(adapter, "name", "unknown")
            status = getattr(adapter, "status", "UNKNOWN")
        except Exception:
            count = estimate_tokens(text, float(profile.get("chars_per_token") or 3.6))
            source = "heuristic_fallback"
            status = "FALLBACK"
        with self._lock:
            if len(self._counts) > 2048:
                self._counts.clear()
            self._counts[key] = (now, count, source)
        return count

    def count_with_meta(self, profile: Dict[str, Any], text: str) -> Dict[str, Any]:
        adapter = self.adapter_for_profile(profile)
        started = time.time()
        try:
            count = int(adapter.count(text))
            source = getattr(adapter, "name", "unknown")
            status = getattr(adapter, "status", "UNKNOWN")
            return {
                "tokens": count,
                "source": source,
                "status": status,
                "latency_ms": round((time.time() - started) * 1000, 1),
            }
        except Exception as exc:
            count = estimate_tokens(text, float(profile.get("chars_per_token") or 3.6))
            return {
                "tokens": count,
                "source": "heuristic_fallback",
                "status": "FALLBACK",
                "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000, 1),
            }

    def health(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        adapter = self.adapter_for_profile(profile)
        result = adapter.health()
        result["profile"] = profile.get("name")
        return result

    def clear(self, profile_name: Optional[str] = None) -> None:
        with self._lock:
            if profile_name:
                self._adapters.pop(profile_name, None)
            else:
                self._adapters.clear()
            self._counts.clear()

    def calibrate(self, engine: sa.Engine, profile_name: str, sample: Optional[str] = None) -> Dict[str, Any]:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("""
                    SELECT name, display_name, provider, model_id, context_window_tokens,
                           max_input_tokens, max_output_tokens, reserved_tokens,
                           chars_per_token, supports_tools, is_default,
                           tokenizer_kind, tokenizer_url, tokenizer_model, tokenizer_path,
                           tokenizer_timeout_ms, tokenizer_status, tokenizer_calibration
                    FROM harness_model_profiles
                    WHERE name = :name
                """),
                {"name": profile_name},
            ).mappings().first()
        if not row:
            raise LookupError("model profile not found")
        profile = dict(row)
        profile["chars_per_token"] = float(profile.get("chars_per_token") or 3.6)
        adapter = self.adapter_for_profile(profile)
        if isinstance(adapter, CalibratedHeuristicTokenizer):
            sample_text = sample or "Harness2 tokenizer calibration sample"
            tokens = adapter.count(sample_text)
            result = {
                "status": "CALIBRATED",
                "method": "calibrated_heuristic",
                "sample_chars": len(sample_text),
                "sample_tokens": tokens,
                "chars_per_token": adapter.chars_per_token,
            }
        elif isinstance(adapter, HeuristicTokenizer):
            result = {
                "status": "HEURISTIC",
                "method": "heuristic",
                "sample_chars": len(sample or ""),
                "sample_tokens": adapter.count(sample or "Harness2"),
                "chars_per_token": adapter.chars_per_token,
            }
        elif isinstance(adapter, HttpTokenizer):
            sample_text = sample or "Harness2 tokenizer calibration sample"
            try:
                tokens = adapter.count(sample_text)
                result = {
                    "status": "EXACT",
                    "method": adapter.name + ":tokenize",
                    "sample_chars": len(sample_text),
                    "sample_tokens": tokens,
                    "chars_per_token": round(len(sample_text) / max(1, tokens), 4),
                }
            except Exception:
                result = adapter.calibrate(sample_text)
        elif hasattr(adapter, "calibrate"):
            result = adapter.calibrate(sample)
        else:
            sample_text = sample or "Harness2 tokenizer calibration sample"
            tokens = adapter.count(sample_text)
            result = {
                "status": "EXACT",
                "method": adapter.name,
                "sample_chars": len(sample_text),
                "sample_tokens": tokens,
                "chars_per_token": round(len(sample_text) / max(1, tokens), 4),
            }
        calibration = {
            "status": result.get("status"),
            "method": result.get("method"),
            "sample_chars": result.get("sample_chars"),
            "sample_tokens": result.get("sample_tokens"),
            "chars_per_token": result.get("chars_per_token"),
            "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with engine.begin() as conn:
            conn.execute(
                sa.text("""
                    UPDATE harness_model_profiles
                    SET tokenizer_status = :status,
                        tokenizer_calibration = CAST(:calibration AS JSONB),
                        chars_per_token = COALESCE(:chars_per_token, chars_per_token)
                    WHERE name = :name
                """),
                {
                    "status": result.get("status") or "HEURISTIC",
                    "calibration": json.dumps(calibration),
                    "chars_per_token": result.get("chars_per_token"),
                    "name": profile_name,
                },
            )
            conn.execute(
                sa.text("""
                    INSERT INTO harness_tokenizer_samples
                        (profile_name, model_id, sample_chars, sample_tokens,
                         chars_per_token, method, details)
                    VALUES
                        (:profile_name, :model_id, :sample_chars, :sample_tokens,
                         :chars_per_token, :method, CAST(:details AS JSONB))
                """),
                {
                    "profile_name": profile_name,
                    "model_id": profile.get("tokenizer_model") or profile.get("model_id"),
                    "sample_chars": int(result.get("sample_chars") or 0),
                    "sample_tokens": int(result.get("sample_tokens") or 0),
                    "chars_per_token": result.get("chars_per_token"),
                    "method": result.get("method") or "unknown",
                    "details": json.dumps(result),
                },
            )
        with self._lock:
            self._adapters.pop(profile_name, None)
            self._counts.clear()
        result["profile"] = profile_name
        return result


REGISTRY = TokenizerRegistry()


def count_for_profile(profile: Dict[str, Any], text: str) -> int:
    return REGISTRY.count_for_profile(profile, text)

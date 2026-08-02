import json
import os
import re
from pathlib import Path

import httpx

from . import config, crypto
from .models import User

PROVIDERS = {
    "opencode": {"base": "https://opencode.ai/zen/go/v1", "model": "deepseek-v4-flash"},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic": {"base": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
    "ollama": {"base": "http://localhost:11434/v1", "model": "llama3.2"},
}


class LlmError(Exception):
    pass


def _find_opencode_key() -> str | None:
    candidates = [
        os.getenv("OPENCODE_DATA_DIR"),
        os.path.expanduser("~/.local/share/opencode"),
        os.path.expanduser("~/.config/opencode"),
        os.path.expanduser("~/.opencode"),
    ]
    for base in candidates:
        if not base:
            continue
        path = Path(base) / "auth.json"
        try:
            data = json.loads(path.read_text())
            for entry in data.values():
                if isinstance(entry, dict) and entry.get("type") == "api" and entry.get("key"):
                    return entry["key"]
        except Exception:
            continue
    return None


def get_config(user: User | None = None) -> dict:
    provider = (user.llm_provider if user and user.llm_provider else config.get("LLM_PROVIDER") or "opencode").lower()
    if provider not in PROVIDERS:
        raise LlmError(f"Unbekannter LLM_PROVIDER: {provider}")
    defaults = PROVIDERS[provider]
    base = (user.llm_base_url if user and user.llm_base_url else config.get("LLM_BASE_URL")) or defaults["base"]
    model = (user.llm_model if user and user.llm_model else config.get("LLM_MODEL")) or defaults["model"]
    key = (crypto.decrypt_secret(user.llm_api_key) if user and user.llm_api_key else config.get("LLM_API_KEY"))
    if not key and provider == "opencode" and not (user and user.llm_api_key):
        key = _find_opencode_key()
    if not key and provider != "ollama":
        raise LlmError(
            f"Kein API-Key fuer Provider '{provider}'. Key in den Einstellungen eintragen."
        )
    return {"provider": provider, "base_url": base, "api_key": key, "model": model}


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LlmError(f"Kein JSON in LLM-Antwort gefunden: {text[:200]}")
    return json.loads(text[start : end + 1])


def _chat_openai_compatible(cfg: dict, system: str, user: str, json_mode: bool) -> str:
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "max_tokens": int(config.get("LLM_MAX_TOKENS") or 8000),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json=body,
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise LlmError(f"LLM-Anfrage fehlgeschlagen: {exc}") from exc
    if r.status_code != 200:
        detail = r.text[:300]
        if json_mode and r.status_code == 400 and "response_format" in detail:
            return _chat_openai_compatible(cfg, system, user, json_mode=False)
        raise LlmError(f"LLM-API Fehler {r.status_code}: {detail}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError(f"Unerwartete LLM-Antwort: {data}") from exc


def _chat_anthropic(cfg: dict, system: str, user: str, json_mode: bool) -> str:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": cfg["model"],
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": int(config.get("LLM_MAX_TOKENS") or 8000),
    }
    if json_mode:
        body["temperature"] = 0.7
    try:
        r = httpx.post(
            f"{cfg['base_url'].rstrip('/')}/v1/messages",
            headers=headers,
            json=body,
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise LlmError(f"LLM-Anfrage fehlgeschlagen: {exc}") from exc
    if r.status_code != 200:
        raise LlmError(f"LLM-API Fehler {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        parts = [p.get("text", "") for p in data["content"] if p.get("type") == "text"]
        return "".join(parts)
    except (KeyError, TypeError) as exc:
        raise LlmError(f"Unerwartete LLM-Antwort: {data}") from exc


def chat_json(system: str, user_prompt: str, user: User | None = None) -> dict:
    cfg = get_config(user)
    if cfg["provider"] == "anthropic":
        text = _chat_anthropic(cfg, system, user_prompt, json_mode=True)
    else:
        text = _chat_openai_compatible(cfg, system, user_prompt, json_mode=True)
    return _extract_json(text)


def status(user: User | None = None) -> dict:
    try:
        cfg = get_config(user)
        return {
            "provider": cfg["provider"],
            "model": cfg["model"],
            "configured": True,
            "error": None,
        }
    except LlmError as exc:
        return {
            "provider": user.llm_provider or config.get("LLM_PROVIDER") or "opencode",
            "model": user.llm_model or config.get("LLM_MODEL") or "",
            "configured": False,
            "error": str(exc),
        }

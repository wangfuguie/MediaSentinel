"""Shared LLM client supporting Gemini and OpenAI-compatible APIs."""

from __future__ import annotations

import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()


def _clean_response(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\n\s*\n", "\n", text).strip()


def _gemini_request(prompt: str, temperature: float, max_tokens: int, timeout: int) -> str:
    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY is required when LLM_PROVIDER=gemini.")
    base_url = LLM_BASE_URL or "https://generativelanguage.googleapis.com/v1beta"
    endpoint = (
        f"{base_url.rstrip('/')}/models/{LLM_MODEL}:generateContent"
        f"?key={LLM_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    response = requests.post(endpoint, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def _openai_compatible_request(
    prompt: str, temperature: float, max_tokens: int, timeout: int
) -> str:
    base_url = LLM_BASE_URL or "http://127.0.0.1:1234/v1"
    endpoint = (
        base_url
        if base_url.rstrip("/").endswith("/chat/completions")
        else f"{base_url.rstrip('/')}/chat/completions"
    )
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "你是一個擅長新聞文字清理的語言模型。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def call_llm(
    prompt: str,
    *,
    temperature: float = 0,
    max_tokens: int = 1500,
    timeout: int = 90,
    max_retries: int = 3,
) -> str:
    """Call the configured LLM provider and return cleaned response text."""
    providers = {
        "gemini": _gemini_request,
        "openai": _openai_compatible_request,
        "openai_compatible": _openai_compatible_request,
        "local": _openai_compatible_request,
    }
    request_function = providers.get(LLM_PROVIDER)
    if not request_function:
        raise ValueError(
            "Unsupported LLM_PROVIDER. Use gemini or openai_compatible."
        )

    for attempt in range(1, max_retries + 1):
        try:
            result = request_function(prompt, temperature, max_tokens, timeout)
            return _clean_response(result)
        except Exception as error:
            print(f"❌ LLM API 呼叫失敗（{attempt}/{max_retries}）：{error}")
            if attempt == max_retries:
                raise
            time.sleep(attempt * 3)

    raise RuntimeError("LLM request failed unexpectedly.")

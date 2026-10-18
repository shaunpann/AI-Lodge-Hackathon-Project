"""Thin OpenAI-compatible client. Works with Anthropic, Groq, OpenAI, OpenRouter, Gemini, etc.

Default settings: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
Per-role overrides (so the agent and the intent judge can use different models):
  AGENT_API_KEY / AGENT_BASE_URL / AGENT_MODEL   -> the agent being protected
  JUDGE_API_KEY / JUDGE_BASE_URL / JUDGE_MODEL   -> the firewall's intent judge
Any override not set falls back to the LLM_* value."""
import os


def _get(role, name, default=None):
    if role:
        v = os.environ.get(f"{role.upper()}_{name}")
        if v:
            return v
    return os.environ.get(f"LLM_{name}", default)


def available(role=None):
    return bool(_get(role, "API_KEY"))


def client(role=None):
    from openai import OpenAI
    return OpenAI(api_key=_get(role, "API_KEY"), base_url=_get(role, "BASE_URL"))


def model(role=None):
    return _get(role, "MODEL", "llama-3.3-70b-versatile")

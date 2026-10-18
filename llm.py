"""Thin OpenAI-compatible client. Works with Groq, OpenAI, OpenRouter, etc.
Set LLM_API_KEY, LLM_BASE_URL (e.g. https://api.groq.com/openai/v1), LLM_MODEL (e.g. llama-3.3-70b-versatile)."""
import os

def available():
    return bool(os.environ.get("LLM_API_KEY"))

def client():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ.get("LLM_BASE_URL"))

def model():
    return os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

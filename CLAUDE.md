# Agent Payment Firewall: context for coding agents

Python project. An AI agent buys content via x402-style HTTP 402 flows; a firewall gateway decides whether
money moves. Evaluated against a synthetic attack suite. Read README.md for the full story; this file is the map.

## Files
- `gateway.py`: the firewall. `fetch_page` (never pays), `paid_fetch` (402 → policy → judge → step-up → signer). Logs decisions.
- `policy.py`: deterministic rules + `MERCHANT_REGISTRY`. No AI.
- `judge.py`: LLM intent checker. Sees only mandate + 402 fields. Fails closed.
- `signer.py`: only holder of the key (HMAC mock), own hard caps.
- `agent.py`: `LLMAgent` (tool loop, `guardrails=True` = prompt-only mode, retries transient errors), `ScriptedAgent` (offline).
- `llm.py`: OpenAI-compatible client. `LLM_*` env vars; `AGENT_*` / `JUDGE_*` override per role.
- `merchant.py`: FastAPI test server on port 8402: legit shops, attack routes, `/custom/*` (user-built attacks), `/ledger` (ground truth).
- `eval.py`: runs cases.json in modes off/prompt/on, grades from the ledger, writes results.json + logs.jsonl.
- `app.py`: Streamlit dashboard + live runner. `demo.py`: one case in the terminal.
- `make_cases.py`, `make_heldout.py`, `make_adaptive.py` → `cases.json` (the test suite).

## Rules
- Never edit `cases.json` or the `make_*.py` generators to make results look better; new cases are appended and committed before their first run.
- Never tune `judge.py` on held-out (`H-*`) cases.
- Never commit API keys; keys come from env vars or Streamlit Secrets.
- Don't run `eval.py` while the dashboard's live runner is in use (both use port 8402).
- Model API notes: Claude rejects `temperature`; its OpenAI-compatible endpoint ignores `response_format`, so judge output is parsed with a regex.

## Commands
- `python3 eval.py [case-id-prefixes]`; `MODES=off,on python3 eval.py` for a subset of modes.
- `streamlit run app.py`
- `python3 demo.py A10-retry-1`

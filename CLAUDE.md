# Agent Payment Firewall: context for coding agents

Python project. An AI agent buys content via x402-style HTTP 402 flows; a firewall gateway decides whether
money moves. Evaluated against a synthetic attack suite. Read README.md for the full story; this file is the map.

## Files
- `gateway.py`: the firewall. `fetch_page` (never pays), `paid_fetch` (402 → policy → judge → step-up → signer). Logs decisions.
- `policy.py`: deterministic rules + `MERCHANT_REGISTRY`. No AI.
- `judge.py`: LLM intent checker. Sees only mandate + 402 fields. Asks K times; score = mean vote; thresholds from `calibration.json`. Fails closed.
- `calibrate.py` + `make_calibration.py` → `calib_cases.json`: labelled set that fits the judge thresholds (fit/test split).
- `signer.py`: `Signer` (in-process HMAC mock, baselines + public demo) and `RemoteSigner` (client for the isolated signer).
- `signer_service.py`: isolated signer process (port 8403). Re-checks user-signed mandate, own registry (`signer_registry.json`), own ledger, human-signed approvals.
- `keys.py` (Ed25519 keys in `.keys/`), `approver.py` (user mandate signing + human approval), `attack_signer.py` (compromised-gateway red team).
- `agent.py`: `LLMAgent` (tool loop, `guardrails=True` = prompt-only mode, retries transient errors), `ScriptedAgent` (offline).
- `llm.py`: OpenAI-compatible client. `LLM_*` env vars; `AGENT_*` / `JUDGE_*` override per role.
- `merchant.py`: FastAPI test server on port 8402: legit shops, attack routes, `/custom/*` (user-built attacks), `/ledger` (ground truth).
- `eval.py`: runs cases.json in modes off/prompt/on, grades from the ledger, writes results.json + logs.jsonl.
- `app.py`: Streamlit dashboard + live runner (How it works tab renders `docs/code_map.mmd`; the README embeds the same Mermaid, kept in sync by a test). `demo.py`: one case in the terminal.
- `make_cases.py`, `make_heldout.py`, `make_adaptive.py` → `cases.json` (the test suite).

- `redteam.py`: attacker LLM vs the AI judge (role `attacker`: `ATTACKER_*` env vars), black-box feedback, writes redteam_results.json.

## Rules
- Never edit `cases.json` or the `make_*.py` generators to make results look better; new cases are appended and committed before their first run.
- Never tune `judge.py` on held-out (`H-*`) cases, and never tune its prompt on `calib_cases.json` (that only fits thresholds).
- Never commit `.keys/`; the signer service must never import `policy.py` (its checks are independent on purpose).
- Never commit API keys; keys come from env vars or Streamlit Secrets.
- Don't run `eval.py` while the dashboard's live runner is in use (both use port 8402).
- Model API notes: Claude rejects `temperature`; its OpenAI-compatible endpoint ignores `response_format`, so judge output is parsed with a regex.

## Commands
- `python3 -m pytest` (offline, no keys; must stay green; add a test with every new rule)
- `python3 eval.py [case-id-prefixes]`; `MODES=off,on python3 eval.py` for a subset of modes.
- `streamlit run app.py`
- `python3 demo.py A10-retry-1`

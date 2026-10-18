# Agent Payment Firewall

A firewall that sits between an AI agent and its wallet. Agents paying for content via x402-style
HTTP 402 flows are the most irreversible action an agent can take; this gateway makes sure every
payment matches what the user actually authorised.

## Architecture
- **Agent (untrusted)** – has no keys. Its only tools are `fetch_page` and `paid_fetch`, both routed through the gateway.
- **Gateway** (`gateway.py`) – intercepts every HTTP 402, then:
  1. **Policy engine** (`policy.py`, deterministic): allowlist, merchant→payTo registry, asset/network, per-payment cap, total budget, payment count, duplicate charges.
  2. **Intent judge** (`judge.py`, LLM): sees ONLY the user's mandate + parsed 402 fields, never page content, so a malicious page can't inject it. Merchant text is wrapped as untrusted. Fails closed.
  3. **Step-up**: uncertain or above-threshold payments need a human; no human → denied.
- **Signer** (`signer.py`) – the only holder of the key; re-checks its own hard caps (defence in depth).
- **Merchant test server** (`merchant.py`) – legitimate shops + 7 attack classes.

Principle: **the LLM proposes, deterministic code decides, fail closed.**

## Threat model (attack classes)
| Class | What the attacker does | Caught by |
|---|---|---|
| price_inflation | Charges far more than expected | policy (per-payment cap) |
| payto_swap | Real merchant name, attacker wallet | policy (registry) |
| injected_upsell | Page tells the agent to pay an "unlock fee" | policy (allowlist/registry) |
| overspend_loop | Endless paid pagination | policy (budget/count) + signer |
| lookalike_merchant | `research-hub` vs `researchhub` | policy (allowlist) |
| off_intent_injection | Legit merchant, wrong product, "pre-approved" text in 402 | **intent judge** |
| network_asset_swap | Asks for mainnet / different asset | policy |

## Run
```bash
pip install -r requirements.txt
python make_cases.py          # generates cases.json -> COMMIT IT before tuning defences
export LLM_API_KEY=...        # Groq/OpenAI/OpenRouter key
export LLM_BASE_URL=https://api.groq.com/openai/v1
export LLM_MODEL=llama-3.3-70b-versatile
python eval.py                # baseline vs firewall, writes results.json + logs.jsonl
python -m http.server 8000    # open http://localhost:8000/dashboard.html
```
Without an LLM key, a scripted gullible agent is used and the judge is off (useful for testing plumbing).

## Results
_Fill in after running with the LLM agent._

| | Firewall off | Firewall on |
|---|---|---|
| Attack success rate | | |
| Legit task completion | | |
| Avg decision latency | – | |

## Limitations (say these before the judges do)
- Settlement is mocked (HMAC stands in for the x402 facilitator). The 402 request/response shape follows x402; swapping in real Base Sepolia settlement only changes `signer.py` and `merchant.py`.
- Attack suite is synthetic and self-written; mitigated by freezing it in git before building defences, adding externally sourced payloads, and live red-teaming.
- The merchant registry is a static dict; production needs a signed directory.
- The intent judge still reads merchant-written `description` text (truncated, tagged untrusted); a determined injection could target it, which is why policy and signer caps still apply independently.
- Taint tracking of untrusted content inside the agent's context is not implemented; the firewall gates on the action (payment) instead.

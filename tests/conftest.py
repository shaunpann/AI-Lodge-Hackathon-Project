"""Shared fixtures. Every test runs offline: no API keys, no network beyond 127.0.0.1, no real money."""
import os, socket, sys, threading, time
import httpx, pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MANDATE = {"purpose": "Buy market research reports and news articles about Southeast Asia EV adoption for the user",
           "allowed_merchants": ["researchhub", "newsdaily"], "max_per_payment": 0.50, "max_total": 0.30,
           "max_payments": 5, "approval_threshold": 0.25}
ATTACKER = "0xATTACKER00000000000000000000000000000666"


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No LLM keys (judge disabled unless a test fakes it), files resolved from the repo root, logs in tmp."""
    for k in list(os.environ):
        if k.endswith("_API_KEY") and k.split("_")[0] in ("LLM", "AGENT", "JUDGE"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(ROOT)
    import gateway
    monkeypatch.setattr(gateway, "LOG_PATH", str(tmp_path / "logs.jsonl"))


@pytest.fixture
def mandate():
    return {**MANDATE, "allowed_merchants": list(MANDATE["allowed_merchants"])}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def merchant_url():
    """The test merchant (merchant.py) on a free local port, once per test session."""
    sys.path.insert(0, ROOT)
    import uvicorn
    from merchant import app
    port = _free_port()
    threading.Thread(target=uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).run,
                     daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/ledger")
            return url
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("merchant did not start")


@pytest.fixture
def ledger(merchant_url):
    """Clears the merchant's ledger and returns a function that reads it (the ground truth of money paid)."""
    httpx.post(merchant_url + "/ledger/reset")
    return lambda: httpx.get(merchant_url + "/ledger").json()

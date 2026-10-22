"""Design invariants that are easy to break by accident in a later change."""
import re
import agent


def test_agent_only_has_gateway_tools():
    """The agent holds no key: its only tools are the gateway's read and pay calls."""
    assert {t["function"]["name"] for t in agent.TOOLS} == {"fetch_page", "paid_fetch"}


def test_agent_and_gateway_never_load_private_keys():
    for f in ("agent.py", "gateway.py", "policy.py", "judge.py"):
        src = open(f).read()
        assert "load_private" not in src and "ONCHAIN_PRIVATE_KEY" not in src, f


def test_no_secrets_committed():
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    assert not any(p.startswith(".keys/") or p.endswith((".key", ".env")) or "secrets.toml" in p for p in tracked)
    api_key = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}")
    hex64 = re.compile(r"0x[0-9a-fA-F]{64}\b")  # a private key looks like this; so does a transaction hash
    for p in tracked:
        if p.endswith((".py", ".md", ".json", ".toml", ".txt", ".yml")):
            text = open(p, errors="ignore").read()
            assert not api_key.search(text), f"possible API key in {p}"
            if p not in ("onchain_proof.json", "README.md"):  # these two hold public transaction hashes
                assert not hex64.search(text), f"possible private key in {p}"


def test_readme_code_map_matches_source():
    """The README embeds docs/code_map.mmd (GitHub only renders inline Mermaid); keep the two in sync."""
    src = open("docs/code_map.mmd").read().strip()
    assert src in open("README.md").read()

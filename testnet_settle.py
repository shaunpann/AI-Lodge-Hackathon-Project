"""Demo settlement on Base Sepolia TESTNET: turns a signed, approved payment into a real on-chain transfer.

It plays the facilitator's role in miniature. It sends money only if:
  1. the payment carries a valid Ed25519 signature from the isolated signer (checked with the signer's PUBLIC key),
  2. the payee is the registered merchant wallet (MERCHANT_ADDRESS), the same address the firewall and signer pin,
  3. the chain really is Base Sepolia (84532), so it can never touch mainnet.
Anything else raises and sends nothing.

Setup (testnet wallets only, never a real one; the same .wallets.env as onchain_proof.py):
  pip install web3
  set -a; source .wallets.env; set +a          # ONCHAIN_PRIVATE_KEY (payer) and MERCHANT_ADDRESS
  python3 phone_approver.py demo --onchain     # tap Approve on your phone -> a real Base Sepolia transaction

SETTLE_ASSET=eth (default) sends a tiny 0.00001 ETH stand-in for the USDC amount; SETTLE_ASSET=usdc sends the
actual USDC amount from testnet USDC (needs faucet USDC in the payer wallet)."""
import base64, json, os
import keys

RPC = os.environ.get("BASE_SEPOLIA_RPC", "https://sepolia.base.org")
CHAIN_ID = 84532
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Circle testnet USDC on Base Sepolia (as in onchain_proof.py)
EXPLORER = "https://sepolia.basescan.org/tx/0x"
ERC20 = [{"name": "transfer", "type": "function", "stateMutability": "nonpayable",
          "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
          "outputs": [{"name": "", "type": "bool"}]}]


def merchant_address():
    a = os.environ.get("MERCHANT_ADDRESS")
    if not a:
        raise SystemExit("Set MERCHANT_ADDRESS and ONCHAIN_PRIVATE_KEY (source .wallets.env), testnet wallets only.")
    return a


def verified_payload(header, registered_payto):
    """Return the payment payload only if the isolated signer signed it and it pays the registered merchant."""
    p = json.loads(base64.b64decode(header))
    if p.get("alg") != "ed25519" or not keys.verify(keys.load_public("signer"), p["payload"], p["signature"]):
        raise PermissionError("settlement refused: payment is not signed by the isolated signer")
    pl = p["payload"]
    if str(pl.get("payTo", "")).lower() != str(registered_payto).lower():
        raise PermissionError("settlement refused: payee is not the registered merchant wallet")
    if pl.get("network") != "base-sepolia":
        raise PermissionError("settlement refused: not a Base Sepolia payment")
    return pl


def send(payload, asset=None):
    """Send the real testnet transfer. Returns the explorer link."""
    from web3 import Web3
    asset = (asset or os.environ.get("SETTLE_ASSET", "eth")).lower()
    w3 = Web3(Web3.HTTPProvider(RPC))
    if w3.eth.chain_id != CHAIN_ID:
        raise PermissionError(f"settlement refused: connected to chain {w3.eth.chain_id}, not Base Sepolia")
    acct = w3.eth.account.from_key(os.environ["ONCHAIN_PRIVATE_KEY"])
    to = Web3.to_checksum_address(payload["payTo"])
    base = {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": CHAIN_ID}
    if asset == "usdc":
        tx = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=ERC20).functions.transfer(
            to, int(round(float(payload["amount"]) * 1e6))).build_transaction(base)
    else:
        tx = {**base, "to": to, "value": w3.to_wei(0.00001, "ether")}
        tx["gas"] = w3.eth.estimate_gas(tx)
        tx["maxFeePerGas"] = w3.eth.gas_price * 2
        tx["maxPriorityFeePerGas"] = w3.to_wei(0.001, "gwei")
    signed = acct.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    h = w3.eth.send_raw_transaction(raw).hex().removeprefix("0x")
    rcpt = w3.eth.wait_for_transaction_receipt("0x" + h, timeout=120)
    if rcpt.status != 1:
        raise RuntimeError(f"transaction failed: {EXPLORER}{h}")
    return EXPLORER + h


class CapturingSigner:
    """Wraps a signer and remembers the last signed payment header, so the demo can settle exactly that one."""

    def __init__(self, inner):
        self.inner, self.last_header = inner, None

    @property
    def session(self):
        return getattr(self.inner, "session", None)

    def sign(self, accepted, approval=None):
        self.last_header = self.inner.sign(accepted, approval=approval)
        return self.last_header

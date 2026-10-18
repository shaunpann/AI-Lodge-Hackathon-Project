"""Real on-chain proof: the firewall decides, and only APPROVED payments become a real Base Sepolia transaction.

It runs two payment requests through the same policy engine used everywhere else:
  1. a legitimate one (payTo = your registered merchant wallet)  -> should be ALLOWED -> real tx on Base Sepolia
  2. a payout-address swap attack (payTo = attacker address)      -> should be BLOCKED -> nothing sent on-chain

TESTNET ONLY. Use a brand-new wallet that holds nothing but faucet funds. Never a mainnet key.

Setup:
  pip install web3
  export ONCHAIN_PRIVATE_KEY="0x..."        # fresh TESTNET wallet (payer); never commit this
  export MERCHANT_ADDRESS="0x..."           # a second wallet you control (the 'merchant')
  python3 onchain_proof.py --dry-run        # checks everything without sending
  python3 onchain_proof.py                  # sends a tiny native-ETH testnet payment
  python3 onchain_proof.py --asset usdc     # sends 0.01 testnet USDC instead (needs faucet USDC)
"""
import argparse, json, os, sys, time
from web3 import Web3
import policy

RPC = os.environ.get("BASE_SEPOLIA_RPC", "https://sepolia.base.org")
CHAIN_ID = 84532  # Base Sepolia
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Circle USDC on Base Sepolia; check against Circle's docs
EXPLORER = "https://sepolia.basescan.org/tx/0x"
ERC20_ABI = [{"name": "transfer", "type": "function", "stateMutability": "nonpayable",
              "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
              "outputs": [{"name": "", "type": "bool"}]},
             {"name": "balanceOf", "type": "function", "stateMutability": "view",
              "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]}]
ATTACKER = "0x000000000000000000000000000000000000dEaD"  # burn address stands in for the attacker; never actually paid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", choices=["eth", "usdc"], default="eth")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    key, merchant = os.environ.get("ONCHAIN_PRIVATE_KEY"), os.environ.get("MERCHANT_ADDRESS")
    if not key or not merchant:
        sys.exit("Set ONCHAIN_PRIVATE_KEY (fresh testnet wallet) and MERCHANT_ADDRESS (second wallet you control).")
    w3 = Web3(Web3.HTTPProvider(RPC))
    if not w3.is_connected():
        sys.exit(f"Cannot reach {RPC}. Check your internet connection or set BASE_SEPOLIA_RPC.")
    if w3.eth.chain_id != CHAIN_ID:
        sys.exit(f"Connected to chain {w3.eth.chain_id}, expected Base Sepolia ({CHAIN_ID}). Refusing to send.")
    acct = w3.eth.account.from_key(key)
    merchant = Web3.to_checksum_address(merchant)

    eth_bal = w3.from_wei(w3.eth.get_balance(acct.address), "ether")
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC), abi=ERC20_ABI)
    usdc_bal = usdc.functions.balanceOf(acct.address).call() / 1e6
    print(f"Payer {acct.address} on Base Sepolia · {eth_bal:.5f} ETH · {usdc_bal:.2f} USDC")

    # The firewall trusts ITS registry, not the merchant: register the merchant wallet for 'researchhub'
    policy.MERCHANT_REGISTRY["researchhub"] = merchant
    amount = 0.01  # policy units (USDC), within the mandate's caps
    mandate = {"purpose": "Buy market research reports about Southeast Asia EV adoption",
               "allowed_merchants": ["researchhub"], "max_per_payment": 0.50, "max_total": 0.30,
               "max_payments": 5, "approval_threshold": 0.25}
    state = {"spent": 0.0, "payments": 0, "paid_resources": set()}
    requests = [
        ("Legitimate purchase", {"merchantId": "researchhub", "payTo": merchant, "maxAmountRequired": f"{amount:.2f}",
                                 "asset": "USDC", "network": "base-sepolia", "resource": "/researchhub/report/r1"}),
        ("Attack: payout address swapped", {"merchantId": "researchhub", "payTo": ATTACKER,
                                            "maxAmountRequired": f"{amount:.2f}", "asset": "USDC",
                                            "network": "base-sepolia", "resource": "/researchhub/report/r2"}),
    ]

    proof = []
    for label, req in requests:
        decision, reasons = policy.evaluate(req, mandate, state)
        print(f"\n== {label}\n   402 asks for {req['maxAmountRequired']} USDC to {req['payTo']}")
        print(f"   Firewall: {decision.upper()} ({'; '.join(reasons)})")
        entry = {"case": label, "payTo": req["payTo"], "decision": decision, "reasons": reasons, "tx": None}
        if decision == "allow":
            state["spent"] += amount; state["payments"] += 1; state["paid_resources"].add(req["resource"])
            if a.dry_run:
                print("   [dry run] would send now")
            else:
                to = Web3.to_checksum_address(req["payTo"])
                assert to == policy.MERCHANT_REGISTRY["researchhub"], "safety: only ever pay the registered merchant"
                base = {"from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address), "chainId": CHAIN_ID}
                if a.asset == "usdc":
                    tx = usdc.functions.transfer(to, int(amount * 1e6)).build_transaction(base)
                else:  # tiny native-ETH payment stands in for the USDC amount
                    tx = {**base, "to": to, "value": w3.to_wei(0.00001, "ether")}
                    tx["gas"] = w3.eth.estimate_gas(tx)
                    tx["maxFeePerGas"] = w3.eth.gas_price * 2
                    tx["maxPriorityFeePerGas"] = w3.to_wei(0.001, "gwei")
                signed = acct.sign_transaction(tx)
                raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
                h = w3.eth.send_raw_transaction(raw).hex().removeprefix("0x")
                print(f"   Sent. Waiting for confirmation…")
                rcpt = w3.eth.wait_for_transaction_receipt("0x" + h, timeout=120)
                ok = rcpt.status == 1
                print(f"   {'CONFIRMED' if ok else 'FAILED'} in block {rcpt.blockNumber}: {EXPLORER}{h}")
                entry["tx"] = EXPLORER + h
                entry["status"] = "confirmed" if ok else "failed"
        else:
            print("   Nothing sent on-chain.")
        proof.append(entry)

    if not a.dry_run:
        json.dump({"network": "base-sepolia", "asset": a.asset, "payer": acct.address, "merchant": merchant,
                   "time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": proof}, open("onchain_proof.json", "w"), indent=2)
        print("\nWrote onchain_proof.json (contains addresses and tx links only, no keys).")


if __name__ == "__main__":
    main()

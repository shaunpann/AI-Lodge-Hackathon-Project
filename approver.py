"""The two HUMAN roles, kept outside the gateway:

  sign_mandate(mandate)       the user signs their spending rules once, with the user key.
  human_approver(signer)      returns an approve_fn for the gateway. On a step-up it shows the human the exact
                              payment and, only if they say yes, signs an approval bound to that one payment.

In production both run on the user's own device (wallet app / phone passkey prompt), never on the gateway server.
In this prototype they run locally and load their private keys from .keys/; the gateway itself never loads them."""
import os, sys
import keys


def sign_mandate(mandate):
    return keys.sign(keys.load_private("user"), mandate)


def human_approver(signer, mode=None):
    """mode: 'ask' = prompt in the terminal (default when interactive), 'yes' / 'no' = scripted (tests)."""
    mode = mode or os.environ.get("APPROVER_MODE") or ("ask" if sys.stdin.isatty() else "no")
    key = keys.load_private("approver")

    def approve(accepted, reasons):
        print(f"\n  🔔 HUMAN APPROVAL NEEDED: {accepted.get('maxAmountRequired')} {accepted.get('asset')} to "
              f"{accepted.get('merchantId')} ({str(accepted.get('payTo'))[:14]}…) for {accepted.get('resource')}")
        print(f"     Why: {'; '.join(reasons)}")
        if mode == "ask":
            yes = input("     Approve this one payment? [y/N] ").strip().lower() == "y"
        else:
            yes = mode == "yes"
            print(f"     ({mode}: {'approved' if yes else 'denied'})")
        if not yes:
            return False
        session = getattr(signer, "session", None)
        if session is None:  # in-process mock signer: no session to bind to, plain yes
            return True
        return keys.sign(key, {"approve": keys.payment_digest(session, accepted)})

    return approve

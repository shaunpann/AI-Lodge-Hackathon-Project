"""Key handling for the isolated signer. Three separate key holders, each with its own Ed25519 key pair:

  user      signs the spending MANDATE once (purpose, shops, caps). Nobody downstream can loosen it.
  approver  the human's device; signs a step-up approval for ONE specific payment.
  signer    the wallet; signs payments. Merchants verify with the signer's PUBLIC key.

The signer service only ever holds its own private key plus the user's and approver's PUBLIC keys, so even a
fully compromised gateway holds no secret that can forge a mandate, an approval or a payment.

    python3 keys.py init     # creates the three key pairs in .keys/ (gitignored) and prints what goes where
In production these live on different machines: the user's key in their wallet app, the approver's key on their
phone (e.g. a passkey), the signer's key in an HSM or enclave. In this prototype they are files on one machine,
kept in separate files so each process loads only what it needs."""
import base64, hashlib, json, os, sys

KEY_DIR = os.environ.get("KEY_DIR", ".keys")
ROLES = ("user", "approver", "signer")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s)


def _ed():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519


def load_private(role):
    path = os.path.join(KEY_DIR, f"{role}.key")
    if not os.path.exists(path):
        raise SystemExit(f"Missing {path}. Run: python3 keys.py init")
    return _ed().Ed25519PrivateKey.from_private_bytes(unb64(open(path).read().strip()))


def load_public(role):
    """Public keys come from env (e.g. SIGNER_PUBKEY) if set, else from .keys/<role>.pub."""
    v = os.environ.get(f"{role.upper()}_PUBKEY")
    if not v:
        path = os.path.join(KEY_DIR, f"{role}.pub")
        if not os.path.exists(path):
            return None
        v = open(path).read().strip()
    return _ed().Ed25519PublicKey.from_public_bytes(unb64(v))


def sign(private_key, obj) -> str:
    return b64(private_key.sign(canonical(obj)))


def verify(public_key, obj, sig: str) -> bool:
    if public_key is None or not sig:
        return False
    try:
        public_key.verify(unb64(sig), canonical(obj))
        return True
    except Exception:
        return False


def dev_hmac_secret() -> bytes:
    """Secret for the in-process MOCK signer (baseline / Streamlit demo). No hard-coded fallback any more:
    if SIGNER_SECRET is not set, a random one is generated once per process and shared via the environment,
    so the in-process signer and the mock merchant agree but nothing guessable is ever used."""
    if not os.environ.get("SIGNER_SECRET"):
        os.environ["SIGNER_SECRET"] = os.urandom(32).hex()
    return os.environ["SIGNER_SECRET"].encode()


def payment_digest(session_id, accepted) -> str:
    """What a human approval is bound to: this session, this payee, this amount, this exact resource."""
    core = {k: accepted.get(k) for k in ("merchantId", "payTo", "maxAmountRequired", "asset", "network", "resource")}
    return hashlib.sha256(canonical({"session": session_id, "payment": core})).hexdigest()


def init():
    from cryptography.hazmat.primitives import serialization as s
    os.makedirs(KEY_DIR, exist_ok=True)
    for role in ROLES:
        priv_path = os.path.join(KEY_DIR, f"{role}.key")
        if os.path.exists(priv_path):
            print(f"  {role}: exists, kept")
            continue
        k = _ed().Ed25519PrivateKey.generate()
        raw = k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())
        pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
        with open(priv_path, "w") as f:
            f.write(b64(raw))
        os.chmod(priv_path, 0o600)
        open(os.path.join(KEY_DIR, f"{role}.pub"), "w").write(b64(pub))
        print(f"  {role}: created")
    print(f"\nKeys are in {KEY_DIR}/ (gitignored). Never commit or share the .key files.")


if __name__ == "__main__":
    if sys.argv[1:] == ["init"]:
        init()
    else:
        print(__doc__)

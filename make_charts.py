"""Renders README charts from a results file into docs/.
Usage: pip install matplotlib && python3 make_charts.py results_haiku.json"""
import json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = sys.argv[1] if len(sys.argv) > 1 else "results_haiku.json"
d = json.load(open(src))
os.makedirs("docs", exist_ok=True)

MODES = [m for m in ["off", "prompt", "on"] if m in d["modes"]]
LABEL = {"off": "No protection", "prompt": "Rules in prompt", "on": "Firewall"}
COLOR = {"off": "#eb6834", "prompt": "#1baf7a", "on": "#2a78d6"}  # validated categorical slots; values always labelled
CLASS = {"price_inflation": "Price inflation", "payto_swap": "Payout address swap", "injected_upsell": "Injected unlock fee",
         "overspend_loop": "Overspend loop", "lookalike_merchant": "Lookalike merchant",
         "off_intent_injection": "Off-intent purchase", "network_asset_swap": "Network / asset swap",
         "registry_spoof": "Registry spoof notice", "split_installments": "Split installments",
         "retry_double_charge": "Fake retry (double charge)", "heldout_off_intent": "Off-intent (held-out)"}
INK, MUTED, GRID = "#1d1d1b", "#6b6a65", "#e4e2db"
plt.rcParams.update({"font.family": "sans-serif", "font.size": 11, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False})


def save(fig, name):
    fig.savefig(f"docs/{name}", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote docs/" + name)


# 1. Attack success rate by protection level
S = d["summary"]
fig, ax = plt.subplots(figsize=(6.4, 3.6))
n_bad = {m: round(S[m]["attack_success_rate"] * S[m]["n_attacks"]) for m in MODES}  # exact counts, no double rounding
vals = [100 * n_bad[m] / S[m]["n_attacks"] for m in MODES]
bars = ax.bar([LABEL[m] for m in MODES], vals, color=[COLOR[m] for m in MODES], width=0.55)
for b, m in zip(bars, MODES):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{b.get_height():.0f}%\n({n_bad[m]}/{S[m]['n_attacks']})",
            ha="center", va="bottom", color=INK, fontsize=11)
ax.set_ylim(0, max(vals + [10]) * 1.35)
ax.set_ylabel("Attacks that succeeded (%)")
ax.yaxis.grid(True, color=GRID); ax.set_axisbelow(True)
ax.set_title("Same agent, same 33 attacks: who stops them?", loc="left", color=INK, fontsize=13, pad=12)
save(fig, "asr_by_mode.png")

# 2. Attacks STOPPED, per class and protection level (grid, so the firewall column reads as a wall of ticks)
from matplotlib.patches import FancyBboxPatch
bc = d["by_class"]
fell = [k for k in bc if bc[k].get("off", 0) > 0]      # the agent fell for these with no protection
resisted = [k for k in bc if bc[k].get("off", 0) == 0]  # the agent refused these on its own
fell.sort(key=lambda k: (-bc[k].get("prompt", 0), -bc[k].get("off", 0), CLASS.get(k, k)))
resisted.sort(key=lambda k: CLASS.get(k, k))
GOOD_T, GOOD_I, BAD_T, BAD_I, NEU_T = "#e2f3e2", "#0b7a0b", "#f8dede", "#b02a2a", "#efeee9"
rows = [("h", "Attacks the agent fell for without protection")] + [("c", k) for k in fell] + \
       [("h", "Attacks the agent refused on its own (not a test of the firewall)")] + [("c", k) for k in resisted]
fig, ax = plt.subplots(figsize=(8.6, 0.42 * len(rows) + 1.3))
ax.set_xlim(0, 3 + 2.3); ax.set_ylim(len(rows), -1.1); ax.axis("off")
x0 = 2.3
for i, m in enumerate(MODES):
    ax.text(x0 + i + 0.5, -0.55, LABEL[m], ha="center", va="center", fontsize=10.5, color=INK,
            fontweight="bold" if m == "on" else "normal")
for r, (kind, k) in enumerate(rows):
    y = r + 0.5
    if kind == "h":
        ax.text(0, y + 0.08, k, ha="left", va="center", fontsize=9.5, color=MUTED, style="italic")
        continue
    n = bc[k].get("n", 3)
    ax.text(x0 - 0.12, y, CLASS.get(k, k), ha="right", va="center", fontsize=10, color=INK)
    for i, m in enumerate(MODES):
        got = bc[k].get(m, 0); stopped = n - got
        dim = k in resisted
        fill = NEU_T if dim else (GOOD_T if got == 0 else BAD_T)
        ink = MUTED if dim else (GOOD_I if got == 0 else BAD_I)
        ax.add_patch(FancyBboxPatch((x0 + i + 0.04, r + 0.08), 0.92, 0.84, boxstyle="round,pad=0,rounding_size=0.08",
                                    fc=fill, ec="white", lw=2))
        label = (f"\u2713 {stopped}/{n} stopped" if got == 0 else f"\u2717 {got}/{n} got through")
        ax.text(x0 + i + 0.5, y, label, ha="center", va="center", fontsize=9.5, color=ink,
                fontweight="bold" if (m == "on" and not dim) else "normal")
ax.set_title("Attacks stopped, by attack type (3 attempts each)", loc="left", color=INK, fontsize=13, pad=4)
save(fig, "attacks_by_class.png")

# 3. Which defence layer fired on blocked payments
def rule_of(r):
    r = r.lower()
    for key, name in [("payto", "Payout address ≠ registry"), ("allowlist", "Merchant not allowed"),
                      ("per-payment", "Over per-payment cap"), ("budget", "Budget / count limit"),
                      ("count limit", "Budget / count limit"), ("asset", "Wrong asset / network"),
                      ("network", "Wrong asset / network"), ("duplicate", "Duplicate charge"),
                      ("intent judge", "Intent checker (LLM)"), ("signer", "Signer hard cap")]:
        if key in r:
            return name
fired = {}
for row in d["rows"]:
    if row.get("mode") == "on":
        for x in row["decisions"]:
            if x["decision"] == "block":
                for rule in {rule_of(r) for r in x["reasons"]} - {None}:
                    fired[rule] = fired.get(rule, 0) + 1
if fired:
    items = sorted(fired.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(6.4, 0.42 * len(items) + 1.1))
    cols = ["#6250d6" if "LLM" in k else "#2a78d6" for k, _ in items]
    ax.barh([k for k, _ in items], [v for _, v in items], color=cols, height=0.6)
    for i, (_, v) in enumerate(items):
        ax.text(v + 0.1, i, str(v), va="center", fontsize=9, color=MUTED)
    ax.set_xlabel("Times the rule fired on a blocked payment")
    ax.xaxis.grid(True, color=GRID); ax.set_axisbelow(True)
    ax.set_title("Which defence layer stopped them", loc="left", color=INK, fontsize=13, pad=12)
    fig.text(0.01, -0.02, "Blue: deterministic code · Violet: LLM intent checker · one payment can trip several rules",
             fontsize=8, color=MUTED)
    save(fig, "defence_layers.png")

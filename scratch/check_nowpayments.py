"""Pin for the NOWPayments crypto checkout (2026-11-27).

Jack's call: Stripe stays exactly as it is; crypto is an added
payment option. It first shipped for the 18+ side only, and
Jack widened it the same day: "make crypto an option for
everyone not just 18+." So the checkout endpoints take any
signed in account (no adult confirmation stamp), and the
Billing panel offers the Crypto method card to every signed
in account (the panel was rebuilt the same day into one
amount set, one method choice, and one Continue button). The 18+ stamp still gates the uncensored lanes and
content; it does not gate this payment method. The IPN
callback verifies the
HMAC SHA-512 signature over the key-sorted body, credits only
finished or confirmed payments, credits the amounts recorded
on the order at invoice creation (never amounts a callback
body claims), and is idempotent on the payment and the order,
so duplicate IPNs never double-credit. Partially paid, failed
and expired payments credit nothing. Money lands through the
same paths Stripe uses: credit_topup and the plan grant, with
arm_autopay_on_payment called wherever money lands.

The signature helpers and the IPN handler are AST-extracted
from app.py and exercised for real below against stub stores.
"""
import ast
import asyncio
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)
sys.path.insert(0, str(ROOT / "platform"))
import pricing  # noqa: E402


def fn(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"function {name} not found")


def check(label, cond):
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok: {label}")


# -- source shape ------------------------------------------------
for route in ("/api/checkout/crypto/topup",
              "/api/checkout/crypto/plan",
              "/api/nowpayments/ipn"):
    check(f"route {route} exists", f'"{route}"' in SRC)
check("config carries the crypto flag",
      '"crypto_payments_configured": nowpayments_configured()' in SRC)
check("billing panel has the crypto method card",
      'id="method-crypto"' in SRC)
# -- the rebuilt Billing panel (Jack, 2026-11-27): one
# amount set, one method choice, one Continue. The first
# crypto build duplicated every amount as its own crypto
# button grid; Jack's verdict was to remove the clutter.
# These pins keep any amount from ever appearing twice.
panel = SRC[SRC.index('id="panel-billing"')
            :SRC.index('id="panel-api"')]
for cents in ("500", "1000", "2000", "5000", "10000", "25000"):
    check(f"the ${int(cents) // 100} amount appears exactly "
          "once in the panel",
          panel.count(f'data-topup="{cents}"') == 1)
check("no per-method amount buttons survive",
      "data-topup-crypto" not in SRC
      and "with crypto" not in panel
      and 'id="buy-creator"' not in SRC
      and 'id="buy-creator-crypto"' not in SRC
      and 'id="crypto-billing"' not in SRC)
check("the panel has exactly one Continue button",
      panel.count('id="pay-continue"') == 1
      and "Continue to payment" in panel)
check("the panel has the two method cards",
      'id="method-card"' in panel
      and 'id="method-crypto"' in panel)
check("Creator stands in its own Plan section, out of the "
      "amount grid (round 2)",
      'id="amount-creator"' in panel
      and panel.index('id="amount-grid"')
      < panel.index('<p class="group-label">Plan</p>')
      < panel.index('id="amount-creator"'))
_grid = panel[panel.index('id="amount-grid"'):]
_grid = _grid[:_grid.index("</div>")]
check("no plan card sits inside the amount grid",
      'id="amount-creator"' not in _grid)
check("Continue dispatches the plan by method",
      "await startCryptoPlan()" in SRC
      and "await startCreatorCard()" in SRC)
check("Continue dispatches top ups by method",
      "await startCryptoTopup(cents)" in SRC
      and "await startTopup(cents)" in SRC)
check("the crypto starters keep their endpoints and "
      "payloads",
      "api('/api/checkout/crypto/topup'" in SRC
      and "body: JSON.stringify({ pack_cents: cents })" in SRC
      and "api('/api/checkout/crypto/plan'" in SRC)
render_start = SRC.index("function renderCryptoBilling()")
render_src = SRC[render_start:render_start + 400]
check("the studio renders the section for every signed in "
      "account when configured",
      "cfg.crypto_payments_configured" in render_src
      and "currentUser" in render_src)
check("the studio render does not consult the adult stamp",
      "adult_confirmed" not in render_src)
check("the invoice carries the callback URL per invoice",
      '"ipn_callback_url": f"{base}/api/nowpayments/ipn"' in SRC)
ipn_src = fn("nowpayments_ipn")
check("the IPN handler verifies the signature before anything",
      "nowpayments_signature_ok" in ipn_src
      and ipn_src.index("nowpayments_signature_ok")
      < ipn_src.index('status not in'))
check("only finished or confirmed payments land money",
      'status not in ("finished", "confirmed")' in ipn_src)
check("crediting is idempotent on payment and order",
      "nowpayments_credits" in ipn_src and 'order.get("credited")'
      in ipn_src)
check("top ups land through credit_topup",
      "credit_topup(user, order[\"credit_cents\"]" in ipn_src)
check("money landing arms autopay through the helper",
      "arm_autopay_on_payment" in ipn_src)
check("the 18+ crypto gate helper is gone",
      "_require_crypto_side" not in SRC)
for endpoint in ("checkout_crypto_topup", "checkout_crypto_plan"):
    ep_src = fn(endpoint)
    check(f"{endpoint} requires sign in, not the adult stamp",
          "require_user" in ep_src
          and "adult_confirmed_at" not in ep_src)
check("crypto top ups reuse the top up amount rule",
      "pricing.valid_topup_cents" in fn("checkout_crypto_topup"))
check("the crypto plan charges the Creator price",
      "pricing.CREATOR_PRICE_CENTS" in fn("checkout_crypto_plan"))

# -- the signature helpers, for real -----------------------------
ns = {"json": json, "hmac": hmac, "hashlib": hashlib}
for name in ("_nowpayments_sorted", "nowpayments_signature",
             "nowpayments_signature_ok"):
    exec(fn(name), ns)
sig_ok = ns["nowpayments_signature_ok"]
signature = ns["nowpayments_signature"]

NPKEY = "pin-" + "test-" + "key"
body = {"payment_id": 12345, "payment_status": "finished",
        "price_amount": 10, "price_currency": "usd",
        "order_id": "mf-u1-abc", "nested": {"b": 2, "a": 1}}
good = signature(body, NPKEY)
check("a correctly signed body verifies",
      sig_ok(body, good, NPKEY))
check("a tampered body does not verify",
      not sig_ok({**body, "price_amount": 9999}, good, NPKEY))
check("the wrong secret does not verify",
      not sig_ok(body, good, "some other secret"))
check("a missing signature does not verify",
      not sig_ok(body, "", NPKEY))
check("a missing secret does not verify",
      not sig_ok(body, good, ""))

# -- the checkout endpoints, for real -----------------------------
# The flip, exercised: a signed in account with NO adult stamp
# (no adult_confirmed_at key at all) creates both checkouts.
# Invoice creation is stubbed; sign in itself is require_user's
# job, covered live (401 without a session).


class HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        self.status_code = status_code
        self.detail = detail


invoice_calls = []


def fake_invoice(user, kind, amount_cents, base):
    invoice_calls.append((user["id"], kind, amount_cents))
    return "https://nowpayments.example/invoice/1"


PLAIN_USER = {"id": "u-plain"}


def require_user(request, session):
    return PLAIN_USER


def Cookie(default=None, **kwargs):
    return default


class FakeCheckoutRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


checkout_ns = {
    "Request": object, "Cookie": Cookie,
    "HTTPException": HTTPException,
    "require_user": require_user, "pricing": pricing,
    "_nowpayments_create_invoice": fake_invoice,
    "base_url": lambda request: "https://example.test",
}
for endpoint in ("checkout_crypto_topup", "checkout_crypto_plan"):
    exec(fn(endpoint), checkout_ns)
topup_ep = checkout_ns["checkout_crypto_topup"]
plan_ep = checkout_ns["checkout_crypto_plan"]

r = asyncio.run(topup_ep(
    FakeCheckoutRequest({"pack_cents": 1000}), ""))
check("an unstamped account creates a top up checkout",
      r == {"url": "https://nowpayments.example/invoice/1"}
      and invoice_calls == [("u-plain", "topup", 1000)])
r = asyncio.run(plan_ep(FakeCheckoutRequest({}), ""))
check("an unstamped account creates a plan checkout",
      r == {"url": "https://nowpayments.example/invoice/1"}
      and invoice_calls[-1]
      == ("u-plain", "plan", pricing.CREATOR_PRICE_CENTS))
try:
    asyncio.run(topup_ep(
        FakeCheckoutRequest({"pack_cents": 50}), ""))
    check("a too small top up is still refused", False)
except HTTPException as e:
    check("a too small top up is still refused",
          e.status_code == 422)
# The custom minimum is $5 since round 2: the old $1 amount
# is refused, and $5 itself goes through.
try:
    asyncio.run(topup_ep(
        FakeCheckoutRequest({"pack_cents": 100}), ""))
    check("a $1 crypto top up is refused at the $5 minimum",
          False)
except HTTPException as e:
    check("a $1 crypto top up is refused at the $5 minimum",
          e.status_code == 422)
r = asyncio.run(topup_ep(
    FakeCheckoutRequest({"pack_cents": 500}), ""))
check("a $5 crypto top up is accepted at the minimum",
      r == {"url": "https://nowpayments.example/invoice/1"}
      and invoice_calls[-1] == ("u-plain", "topup", 500))
r = asyncio.run(topup_ep(
    FakeCheckoutRequest({"pack_cents": 25000}), ""))
check("the $250 pack rides the crypto top up",
      r == {"url": "https://nowpayments.example/invoice/1"}
      and invoice_calls[-1] == ("u-plain", "topup", 25000))

# -- the IPN handler, for real -----------------------------------


class FakeRequest:
    def __init__(self, payload, sig):
        self._payload = payload
        self.headers = {"x-nowpayments-sig": sig}

    async def body(self):
        return json.dumps(self._payload).encode()


class WebStub:
    def post(self, *a, **k):
        return lambda f: f


calls = {"credit_topup": [], "add_net_paid": [], "arm": [],
         "save_user": []}
meta = {}
users = {}


def credit_topup(user, credit_cents, gross_cents):
    calls["credit_topup"].append(
        (user["id"], credit_cents, gross_cents))
    user["balance_cents"] = int(user.get("balance_cents", 0)) \
        + int(credit_cents)


def add_net_paid(user, gross_cents):
    calls["add_net_paid"].append((user["id"], gross_cents))


def arm_autopay_on_payment(user, amount_hint_cents=None):
    calls["arm"].append((user["id"], amount_hint_cents))


def save_user(user):
    calls["save_user"].append(user["id"])
    users[f"id:{user['id']}"] = user


IPN_NAME = "NOWPAYMENTS_" + "IPN_SECRET"
os.environ[IPN_NAME] = NPKEY
FAKE_NOW = 1_700_000_000.0
handler_ns = {
    "os": os, "json": json, "Request": object,
    "HTTPException": HTTPException, "web": WebStub(),
    "nowpayments_signature_ok": sig_ok,
    "_np_ipn_name": IPN_NAME,
    "_now": lambda: FAKE_NOW, "pricing": pricing,
    "meta": meta, "users": users,
    "credit_topup": credit_topup, "add_net_paid": add_net_paid,
    "arm_autopay_on_payment": arm_autopay_on_payment,
    "save_user": save_user,
}
exec(ipn_src, handler_ns)
ipn = handler_ns["nowpayments_ipn"]


def run_ipn(payload, sign=True, secret=NPKEY):
    sig = signature(payload, secret) if sign else "0" * 128
    return asyncio.run(ipn(FakeRequest(payload, sig)))


users["id:u-plain"] = {"id": "u-plain", "balance_cents": 0,
                       "plan": "free", "usage": {}}
meta["nowpayments_orders"] = {
    "ord-topup": {"user_id": "u-plain", "kind": "topup",
                  "credit_cents": 1000, "gross_cents": 1000,
                  "invoice_id": 77, "credited": False},
    "ord-plan": {"user_id": "u-plain", "kind": "plan",
                 "credit_cents": 0, "gross_cents": 2000,
                 "invoice_id": 78, "credited": False},
    "ord-partial": {"user_id": "u-plain", "kind": "topup",
                    "credit_cents": 500, "gross_cents": 500,
                    "invoice_id": 79, "credited": False},
}

# A forged callback (bad signature) is refused and credits nothing.
try:
    run_ipn({"order_id": "ord-topup", "payment_id": 501,
             "payment_status": "finished"}, sign=False)
    check("a bad-signature IPN is refused", False)
except HTTPException as e:
    check("a bad-signature IPN is refused", e.status_code == 400)
check("the forged IPN credited nothing", calls["credit_topup"] == [])

# A finished top up credits the recorded amount exactly once,
# even when the same IPN is delivered again.
payload = {"order_id": "ord-topup", "payment_id": 501,
           "invoice_id": 77, "payment_status": "finished",
           "price_amount": 9999}
r = run_ipn(payload)
check("a finished top up is received", r == {"received": True})
check("the credit is the recorded amount, not the body's claim",
      calls["credit_topup"] == [("u-plain", 1000, 1000)])
check("the balance moved by the credit",
      users["id:u-plain"]["balance_cents"] == 1000)
check("autopay arming ran for the top up",
      calls["arm"] == [("u-plain", 1000)])
r = run_ipn(payload)
check("a duplicate IPN is reported as a duplicate",
      r.get("duplicate") is True)
check("the duplicate credited nothing more",
      calls["credit_topup"] == [("u-plain", 1000, 1000)])

# A confirmed payment for the plan grants Creator the same way
# a Stripe plan purchase does.
r = run_ipn({"order_id": "ord-plan", "payment_id": 502,
             "invoice_id": 78, "payment_status": "confirmed"})
check("a confirmed plan payment is received",
      r == {"received": True})
check("the plan was granted",
      users["id:u-plain"]["plan"] == "creator")
check("the plan money counted toward the ceiling",
      calls["add_net_paid"] == [("u-plain", 2000)])
check("autopay arming ran for the plan",
      ("u-plain", None) in calls["arm"])
check("the plan grant wrote the paid-through stamp",
      users["id:u-plain"].get("plan_paid_through")
      == FAKE_NOW + pricing.CREATOR_MONTH_SECONDS)
check("the stamp records the month was paid with crypto",
      users["id:u-plain"].get("plan_paid_via") == "crypto")

# Partially paid, failed and expired payments credit nothing.
for status in ("partially_paid", "failed", "expired", "waiting"):
    r = run_ipn({"order_id": "ord-partial", "payment_id": 503,
                 "invoice_id": 79, "payment_status": status})
    check(f"a {status} payment is only recorded",
          r == {"received": True})
check("no partial or failed payment credited anything",
      len(calls["credit_topup"]) == 1)
check("the order kept its last status",
      meta["nowpayments_orders"]["ord-partial"]["last_status"]
      == "waiting")

# An IPN for an order we never made changes nothing.
r = run_ipn({"order_id": "ord-nope", "payment_id": 504,
             "payment_status": "finished"})
check("an unknown order is acknowledged without credit",
      r.get("unknown_order") is True
      and len(calls["credit_topup"]) == 1)

# -- the renewal settler, for real -------------------------------
# Round 2: a stamp-paid Creator month renews on its own.
# Route E (same night): the money order is the balance
# first, then auto reload's saved card. Grace removal
# (Jack, 2026-11-28): when neither can pay, the plan
# lapses on that settle, at the paid-through date. There
# is no grace period, no grace email, no mid-grace rescue
# and no forgiven days: a late success extends from the
# original stamp. A rail error never lapses and retries
# after a day, and while it waits the account is frozen
# (past_due). The fake charge step below models the real
# one's contract: the balance pays in full or not at all,
# the card is pulled only when card_ok, and the card's
# answer is scripted.
MONTH = pricing.CREATOR_MONTH_SECONDS
DAY = 24 * 3600
# The settler runs a minute after the FAKE_NOW stamp
# passes, the way the account's own traffic runs it in
# production; with the original-stamp anchor, a clock a
# full month late would settle the next period too, one
# claim per period, which is the catch-up the anchor
# means (the clock never stops).
NOW_BOX = {"t": FAKE_NOW + 60}
settle_meta = {}
scalls = {"charges": [], "net_paid": [], "draws": []}
charge_script = {"result": ("ok", None, "card")}


def fake_charge(user, amount_cents, card_ok=True):
    if int(user.get("balance_cents", 0)) >= amount_cents:
        user["balance_cents"] = \
            int(user.get("balance_cents", 0)) - amount_cents
        scalls["draws"].append((user["id"], amount_cents))
        return "ok", None, "balance"
    if not card_ok:
        return "unpaid", None, None
    scalls["charges"].append((user["id"], amount_cents))
    return charge_script["result"]


settle_ns = {
    "pricing": pricing, "meta": settle_meta,
    "_now": lambda: NOW_BOX["t"],
    "_plan_renewal_charge": fake_charge,
    "add_net_paid": lambda u, g: scalls["net_paid"].append(
        (u["id"], g)),
    "_record_stripe_credit": lambda *a, **k: None,
    "save_user": lambda u: None,
}
exec(fn("settle_plan_renewal"), settle_ns)
settle = settle_ns["settle_plan_renewal"]


def crypto_user(**over):
    user = {"id": "u-ren", "plan": "creator",
            "plan_paid_via": "crypto",
            "plan_paid_through": FAKE_NOW,
            "stripe_customer_id": "cus_9",
            "stripe_payment_method_id": "pm_9",
            "auto_reload": {"enabled": True, "explicit": True,
                            "amount_cents": 1000},
            "usage": {"included_seconds": 42.0}}
    user.update(over)
    return user


user = crypto_user()
settle(user)
check("a due stamp renews through the charge step, once, "
      "at the Creator price",
      scalls["charges"] == [("u-ren",
                             pricing.CREATOR_PRICE_CENTS)])
check("the renewal keeps Creator and advances the stamp "
      "one month from the original stamp",
      user["plan"] == "creator"
      and user["plan_paid_through"] == FAKE_NOW + MONTH)
check("the stamp records the renewal month was paid by card",
      user["plan_paid_via"] == "card")
check("the renewal resets the included budget and counts "
      "the money",
      user["usage"]["included_seconds"] == 0.0
      and scalls["net_paid"] == [("u-ren",
                                  pricing.CREATOR_PRICE_CENTS)])
settle(user)
check("a settled month is never charged twice",
      len(scalls["charges"]) == 1)

# Route E: the balance pays first, even with no card at all
# and auto reload off.
user = crypto_user(id="u-ren-bal", balance_cents=5000,
                   auto_reload={"enabled": False,
                                "explicit": False,
                                "amount_cents": 0},
                   stripe_customer_id=None,
                   stripe_payment_method_id=None)
settle(user)
check("a due stamp renews from the balance first",
      scalls["draws"] == [("u-ren-bal",
                           pricing.CREATOR_PRICE_CENTS)]
      and user["balance_cents"] == 3000
      and user["plan"] == "creator"
      and user["plan_paid_via"] == "balance"
      and user["plan_paid_through"] == FAKE_NOW + MONTH)
check("a balance renewal counts no money twice and pulls "
      "no card",
      len(scalls["net_paid"]) == 1
      and len(scalls["charges"]) == 1)
settle(user)
check("a balance-paid month is never drawn twice",
      len(scalls["draws"]) == 1
      and user["balance_cents"] == 3000)

# A short balance is never drawn partially: the card pays
# the whole price and the balance keeps every cent.
user = crypto_user(id="u-ren-short", balance_cents=1999)
settle(user)
check("a short balance falls through to the card whole",
      len(scalls["charges"]) == 2
      and user["balance_cents"] == 1999
      and user["plan_paid_via"] == "card"
      and len(scalls["draws"]) == 1
      and len(scalls["net_paid"]) == 2)

user = crypto_user(id="u-ren-off",
                   plan_paid_through=NOW_BOX["t"] - 60,
                   auto_reload={"enabled": False,
                                "explicit": True,
                                "amount_cents": 1000})
settle(user)
check("auto reload off and no balance lapses at once",
      user["plan"] == "free"
      and user["plan_paid_through"] is None
      and user["plan_paid_via"] is None
      and "plan_grace_until" not in user
      and len(scalls["charges"]) == 2)
user["balance_cents"] = 2000
settle(user)
check("a top up after the lapse buys nothing back",
      user["plan"] == "free"
      and user["balance_cents"] == 2000
      and len(scalls["draws"]) == 1
      and len(scalls["net_paid"]) == 2)

user = crypto_user(id="u-ren-nocard",
                   plan_paid_through=NOW_BOX["t"] - 60,
                   stripe_payment_method_id=None)
settle(user)
check("no saved card lapses at once, no grace stamp",
      user["plan"] == "free"
      and user["plan_paid_through"] is None
      and user["plan_paid_via"] is None
      and "plan_grace_until" not in user
      and len(scalls["charges"]) == 2)
settle(user)
check("a lapsed plan is never settled again",
      user["plan"] == "free" and len(scalls["charges"]) == 2)

user = crypto_user(plan_paid_through=NOW_BOX["t"] + 1000)
settle(user)
check("a stamp still in the future is untouched",
      user["plan"] == "creator" and len(scalls["charges"]) == 2)

user = crypto_user(stripe_subscription_id="sub_1")
settle(user)
check("a Stripe subscription is never settled here",
      user["plan"] == "creator" and len(scalls["charges"]) == 2)

charge_script["result"] = ("declined", None, None)
user = crypto_user(id="u-ren-dec",
                   plan_paid_through=NOW_BOX["t"] - 60)
settle(user)
check("a declined renewal lapses at once",
      user["plan"] == "free"
      and user["plan_paid_through"] is None
      and "plan_grace_until" not in user
      and len(scalls["charges"]) == 3)
settle(user)
check("a declined card is never pulled twice in a period",
      len(scalls["charges"]) == 3 and user["plan"] == "free")

charge_script["result"] = ("error", None, None)
user = crypto_user(id="u-ren-err")
settle(user)
check("a rail error never lapses the plan",
      user["plan"] == "creator" and len(scalls["charges"]) == 4)
check("a rail error freezes the account while it waits",
      user.get("past_due") is True)
settle(user)
check("a rail error is not retried within the day",
      len(scalls["charges"]) == 4)
NOW_BOX["t"] += 25 * 3600
charge_script["result"] = ("ok", None, "card")
settle(user)
check("the renewal retries after a day and lands",
      user["plan"] == "creator" and len(scalls["charges"]) == 5
      and user.get("past_due") is False)
check("a late success extends from the original stamp, "
      "gap days unforgiven",
      user["plan_paid_through"] == FAKE_NOW + MONTH
      and user["plan_paid_through"] != NOW_BOX["t"] + MONTH)
check("the grace mechanism is gone from the source",
      "plan_grace_until" not in SRC
      and "_plan_grace_email" not in SRC
      and "three grace days" not in SRC)

# The charge step itself, for real: three honest answers.
class RenewalPI:
    def __init__(self, status):
        self.id = "pi_ren_1"
        self.status = status


class CardError(Exception):
    pass


class RenewalStripe:
    def __init__(self, mode):
        self.mode = mode
        outer = self

        class PaymentIntent:
            @staticmethod
            def create(**kwargs):
                outer.kwargs = kwargs
                if outer.mode == "raise_card":
                    raise CardError("the card was declined")
                if outer.mode == "raise_other":
                    raise RuntimeError("rail down")
                return RenewalPI(outer.mode)

        self.PaymentIntent = PaymentIntent


rholder = {"stripe": RenewalStripe("succeeded")}


def renewal_stripe_client():
    if rholder["stripe"] is None:
        return None, "payments are not configured yet"
    return rholder["stripe"], None


charge_ns = {"stripe_client": renewal_stripe_client,
             "is_owner": lambda user: bool(
                 user and user.get("is_owner"))}
exec(fn("_plan_renewal_charge"), charge_ns)
renewal_charge = charge_ns["_plan_renewal_charge"]
payee = {"id": "u-ren", "stripe_customer_id": "cus_9",
         "stripe_payment_method_id": "pm_9",
         "plan_paid_via": "crypto"}
result, pi, via = renewal_charge(payee, 2000)
check("the charge step confirms a paid month",
      result == "ok" and via == "card"
      and pi.id == "pi_ren_1")
check("the charge step pulls the saved card off session",
      rholder["stripe"].kwargs.get("off_session") is True
      and rholder["stripe"].kwargs.get("confirm") is True
      and rholder["stripe"].kwargs.get("amount") == 2000
      and rholder["stripe"].kwargs.get(
          "payment_method") == "pm_9")
rholder["stripe"] = RenewalStripe("requires_payment_method")
check("an unconfirmed intent reads as a decline",
      renewal_charge(payee, 2000)[0] == "declined")
rholder["stripe"] = RenewalStripe("raise_card")
check("a card error reads as a decline",
      renewal_charge(payee, 2000)[0] == "declined")
rholder["stripe"] = RenewalStripe("raise_other")
check("a rail error reads as an error, not a decline",
      renewal_charge(payee, 2000)[0] == "error")
rholder["stripe"] = None
check("no payments rail reads as an error",
      renewal_charge(payee, 2000)[0] == "error")

# Route E answers from the real charge step: the balance
# pays first, in full or not at all, and Stripe is never
# touched when the balance covers.
rholder["stripe"] = RenewalStripe("succeeded")
payee_bal = dict(payee, balance_cents=5000)
result, pi, via = renewal_charge(payee_bal, 2000)
check("the charge step draws a covering balance first",
      result == "ok" and via == "balance" and pi is None
      and payee_bal["balance_cents"] == 3000)
check("a balance draw never touches Stripe",
      getattr(rholder["stripe"], "kwargs", None) is None)
payee_exact = dict(payee, balance_cents=2000)
result, pi, via = renewal_charge(payee_exact, 2000)
check("an exactly covering balance draws to zero",
      result == "ok" and via == "balance"
      and payee_exact["balance_cents"] == 0)
payee_short = dict(payee, balance_cents=1999)
check("a short balance with no card pull reads unpaid",
      renewal_charge(payee_short, 2000, False)
      == ("unpaid", None, None)
      and payee_short["balance_cents"] == 1999)
result, pi, via = renewal_charge(payee_short, 2000, True)
check("a short balance falls through to the card whole",
      result == "ok" and via == "card"
      and payee_short["balance_cents"] == 1999)

# The owner is never charged by his own platform (Jack,
# 2026-11-28): his renewal draws his balance when it covers
# and reads unpaid when it does not, with the card step
# never reached even when a card pull is allowed.
rholder["stripe"] = RenewalStripe("succeeded")
owner_bal = dict(payee, is_owner=True, balance_cents=5000)
result, pi, via = renewal_charge(owner_bal, 2000)
check("an owner renewal draws his covering balance",
      result == "ok" and via == "balance" and pi is None
      and owner_bal["balance_cents"] == 3000)
check("the owner's balance draw never touches Stripe",
      getattr(rholder["stripe"], "kwargs", None) is None)
owner_short = dict(payee, is_owner=True, balance_cents=1999)
check("an owner renewal short of balance reads unpaid, "
      "never a card pull",
      renewal_charge(owner_short, 2000, True)
      == ("unpaid", None, None)
      and owner_short["balance_cents"] == 1999
      and getattr(rholder["stripe"], "kwargs", None) is None)

# Route E copy: the panel note, the plan status line and
# the Terms tell the balance truth; the round 2 note that
# renewed only through the saved card is gone.
check("the crypto Creator note names the balance first",
      "renews on its own from your balance while it "
      "covers $20. Auto reload covers a shortfall when "
      "it is on." in SRC)
check("the round 2 renewal note is gone",
      "while auto reload is on with a saved card. "
      "Otherwise it covers one month" not in SRC)
check("the panel carries the plan status line",
      'id="plan-note"' in panel
      and "Creator renews from your balance. Keep $20 "
      "in it and it never lapses." in SRC
      and "Creator could not renew" not in SRC)
check("the Terms carry the balance renewal and the "
      "immediate end",
      "renews the same way from your balance while it"
      in SRC and "the plan ends at the paid-through"
      in SRC and "three grace days" not in SRC)
check("public_user exposes the plan note state",
      '"plan_paid_via": user.get("plan_paid_via")' in SRC
      and '"plan_grace_until"' not in SRC)

print("check_nowpayments: all ok")

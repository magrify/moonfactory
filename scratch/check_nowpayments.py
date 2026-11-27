"""Pin for the NOWPayments crypto checkout (2026-11-27).

Jack's call: Stripe stays exactly as it is; crypto is an added
option on the uncensored side only (the 18+ side: accounts with
the adult confirmation stamp). The IPN callback verifies the
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
check("billing panel has the crypto section",
      'id="crypto-billing"' in SRC and "Pay with crypto" in SRC
      and "data-topup-crypto" in SRC)
check("the studio renders the section only for the 18+ side",
      "currentUser.adult_confirmed" in SRC
      and "cfg.crypto_payments_configured" in SRC)
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
gate_src = fn("_require_crypto_side")
check("the crypto gate reads the adult stamp",
      'user.get("adult_confirmed_at")' in gate_src)
for endpoint in ("checkout_crypto_topup", "checkout_crypto_plan"):
    check(f"{endpoint} applies the crypto gate",
          "_require_crypto_side(user)" in fn(endpoint))
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

# -- the checkout gate, for real ---------------------------------


class HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        self.status_code = status_code
        self.detail = detail


gate_ns = {"HTTPException": HTTPException}
exec(gate_src, gate_ns)
require_side = gate_ns["_require_crypto_side"]
try:
    require_side({"id": "u-free"})
    check("an unstamped account is refused", False)
except HTTPException as e:
    check("an unstamped account is refused", e.status_code == 403)
require_side({"id": "u-adult", "adult_confirmed_at": 1.0})
check("a stamped account passes the gate", True)

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
handler_ns = {
    "os": os, "json": json, "Request": object,
    "HTTPException": HTTPException, "web": WebStub(),
    "nowpayments_signature_ok": sig_ok,
    "_np_ipn_name": IPN_NAME,
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


users["id:u-adult"] = {"id": "u-adult", "balance_cents": 0,
                       "plan": "free", "usage": {}}
meta["nowpayments_orders"] = {
    "ord-topup": {"user_id": "u-adult", "kind": "topup",
                  "credit_cents": 1000, "gross_cents": 1000,
                  "invoice_id": 77, "credited": False},
    "ord-plan": {"user_id": "u-adult", "kind": "plan",
                 "credit_cents": 0, "gross_cents": 2000,
                 "invoice_id": 78, "credited": False},
    "ord-partial": {"user_id": "u-adult", "kind": "topup",
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
      calls["credit_topup"] == [("u-adult", 1000, 1000)])
check("the balance moved by the credit",
      users["id:u-adult"]["balance_cents"] == 1000)
check("autopay arming ran for the top up",
      calls["arm"] == [("u-adult", 1000)])
r = run_ipn(payload)
check("a duplicate IPN is reported as a duplicate",
      r.get("duplicate") is True)
check("the duplicate credited nothing more",
      calls["credit_topup"] == [("u-adult", 1000, 1000)])

# A confirmed payment for the plan grants Creator the same way
# a Stripe plan purchase does.
r = run_ipn({"order_id": "ord-plan", "payment_id": 502,
             "invoice_id": 78, "payment_status": "confirmed"})
check("a confirmed plan payment is received",
      r == {"received": True})
check("the plan was granted",
      users["id:u-adult"]["plan"] == "creator")
check("the plan money counted toward the ceiling",
      calls["add_net_paid"] == [("u-adult", 2000)])
check("autopay arming ran for the plan",
      ("u-adult", None) in calls["arm"])

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

print("check_nowpayments: all ok")

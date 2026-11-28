"""Pin for the billing cluster (security fixes, 2026-11-27).

stripe-refund-dispute-no-clawback: the webhook records every
credit in a ledger and handles charge.refunded,
charge.dispute.created, charge.dispute.closed, and
checkout.session.async_payment_failed; a delayed payment
method's credit is parked at checkout completion and granted
only when the money lands.

billing-autoreload-webhook-arm-without-explicit-opt-in: a
reload fires only for a config carrying the explicit stamp.
The stamp is set by the billing endpoint, and since Jack's
autopay rule (2026-11-27) also by arm_autopay_on_payment when
the customer's own payment lands; saving a card alone never
arms. attempt_auto_reload requires the stamp.

The reversal helper is exercised for real below: it is
AST-extracted from app.py and run against plain dicts with
the repo's own pricing module.
"""
import ast
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


webhook = fn("stripe_webhook")
for etype in ("charge.refunded", "charge.dispute.created",
              "charge.dispute.closed",
              "checkout.session.async_payment_failed"):
    check(f"webhook handles {etype}", f'"{etype}"' in webhook)
check("checkout credit is recorded in the ledger",
      "_record_stripe_credit" in webhook
      and "stripe_pending_credits" in webhook)
check("unpaid async credit is parked, not granted",
      'obj.get("payment_status") == "unpaid"' in webhook)

# Autopay arming (Jack, 2026-11-27) supersedes the
# opt-in-only rule this pin first encoded: the webhook arms
# auto reload only through arm_autopay_on_payment, called
# where money lands, and never by setting the config inline.
check("the webhook arms autopay only through the helper",
      "arm_autopay_on_payment" in webhook
      and '"enabled": True' not in webhook)
endpoint = fn("billing_autoreload")
check("the billing endpoint stamps explicit opt-in",
      '"explicit": True' in endpoint)
attempt = fn("attempt_auto_reload")
check("attempt_auto_reload requires the explicit stamp",
      'cfg.get("explicit")' in attempt)

# Billing round 2 (Jack, 2026-11-27): the Auto reload
# section carries an explicit on/off switch again (the
# rebuild had folded it into state text and buttons), the
# method pills carry the card and crypto symbols, and the
# crypto plan renewal settler rides require_user.
check("the panel carries the auto reload switch",
      'id="autoreload-toggle"' in SRC
      and 'class="switch"' in SRC)
check("the switch flips auto reload through the endpoint",
      "$('autoreload-toggle').addEventListener('change'"
      in SRC and "api('/api/billing/autoreload'" in SRC)
check("the method pills carry the card and crypto symbols",
      'class="method-icon"' in SRC and "₿" in SRC
      and 'class="btc-badge"' in SRC)
check("require_user settles stamp-paid plan renewals",
      "settle_plan_renewal(user)" in fn("require_user"))
settler = fn("settle_plan_renewal")
check("the settler never settles a Stripe subscription",
      'user.get("stripe_subscription_id")' in settler)
check("the settler lapses a plan it cannot renew",
      'user["plan"] = "free"' in settler
      and '"lapsed"' in settler)
charge = fn("_plan_renewal_charge")
check("the renewal charge pulls the saved card off session",
      "off_session=True" in charge
      and '"creator_renewal"' in charge)
# Route E (Jack, 2026-11-27): the renewal money order is
# the balance first. Grace removal (Jack, 2026-11-28):
# no grace anywhere; a definitive failure lapses on the
# settle, a late success extends from the original stamp,
# and a rail error freezes dispatch while it waits.
check("the renewal charge draws the balance before the "
      "card",
      charge.index("balance_cents")
      < charge.index("stripe_client()")
      and '"balance"' in charge and '"unpaid"' in charge)
# The owner is never charged by his own platform (Jack,
# 2026-11-28): the renewal charge refuses his card step
# outright, before the rail is even built.
check("the renewal charge never pulls the owner's card",
      "is_owner(user)" in charge
      and charge.index("is_owner(user)")
      < charge.index("stripe_client()"))
check("the settler has no grace mechanism left",
      "plan_grace_until" not in settler
      and "_plan_grace_email" not in SRC
      and "3 * 24 * 3600" not in settler)
check("the settler extends from the original stamp",
      'plan_paid_through"] = through' in settler
      and "through, _now())" not in settler)
check("a rail error freezes the account while it waits",
      'user["past_due"] = True' in settler)

# Exercise the reversal helper for real.
seg = fn("_reverse_stripe_money")
ns = {"pricing": pricing}
exec(seg, ns)
reverse = ns["_reverse_stripe_money"]

user = {"balance_cents": 5000, "net_paid_cents": 10000.0}
entry = {"credit_cents": 2000, "gross_cents": 2000,
         "reversed_gross": 0}
took = reverse(user, entry, 2000)
check("full refund reverses the full credit",
      took == 2000 and user["balance_cents"] == 3000)
check("full refund removes the net paid",
      abs(user["net_paid_cents"]
          - (10000.0 - pricing.net_of(2000))) < 1e-9)
check("a replayed refund reverses nothing twice",
      reverse(user, entry, 2000) == 0
      and user["balance_cents"] == 3000)

user = {"balance_cents": 1000, "net_paid_cents": 5000.0}
entry = {"credit_cents": 2000, "gross_cents": 2000,
         "reversed_gross": 0}
reverse(user, entry, 500)
check("a partial refund reverses proportionally",
      user["balance_cents"] == 500
      and entry["reversed_gross"] == 500)
reverse(user, entry, 2000)
check("cumulative refunds stop at the payment total",
      entry["reversed_gross"] == 2000
      and user["balance_cents"] == -1000)

user = {"balance_cents": 800, "net_paid_cents": 0.0}
entry = {"credit_cents": 2000, "gross_cents": 2000,
         "reversed_gross": 0, "dispute_gross": 2000}
reverse(user, entry, 2000, net_already_removed=True)
check("a lost dispute reverses credit without double "
      "removing net paid",
      user["balance_cents"] == -1200
      and user["net_paid_cents"] == 0.0)

print("check_billing: all pins hold")

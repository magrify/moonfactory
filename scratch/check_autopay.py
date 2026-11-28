"""Pin for autopay on payment (Jack, 2026-11-27).

Any successful payment, a plan purchase or a top up, turns
auto reload on for the account; a later payment re-enables it
after a manual off. With autopay on, a job blocked on balance
triggers exactly one automatic top up sized to cover the job
(the configured amount as the floor, denominations or whole
dollars above it, a $50 single-charge ceiling), credited only
after Stripe confirms, and the job proceeds. Autopay off, a
failed charge, or a shortfall past the ceiling all leave the
old refusal untouched, and the owner is never charged.

Round 2 (Jack, 2026-11-27): the arming exclusion for the
owner account is reversed. The owner experiences the
platform as a paid user at cost, so a payment arms his auto
reload exactly like any other payer's, and his stored config
stands enabled. The charge attempt's own owner guard is a
separate, older rule (the owner's jobs are never blocked by
his balance, so a reload is never needed on them) and stays.

The helpers are exercised for real below: they are
AST-extracted from app.py and run against plain dicts with a
fake Stripe and the repo's own pricing module.
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


def is_owner(user):
    return bool(user and user.get("is_owner"))


# -- arming ---------------------------------------------------

ns = {"pricing": pricing, "is_owner": is_owner}
exec(fn("arm_autopay_on_payment"), ns)
arm = ns["arm_autopay_on_payment"]


def payer(**over):
    user = {"id": "u1", "stripe_customer_id": "cus_1",
            "stripe_payment_method_id": "pm_1",
            "balance_cents": 0}
    user.update(over)
    return user


user = payer()
arm(user)
check("a payment turns autopay on with the $10 plan default",
      user["auto_reload"] == {"enabled": True, "explicit": True,
                              "amount_cents": 1000})

user = payer()
arm(user, 5000)
check("a $50 top up starts the reload amount at $20",
      user["auto_reload"]["amount_cents"] == 2000)

user = payer()
arm(user, 700)
check("a $7 custom top up starts the reload amount at $5",
      user["auto_reload"]["amount_cents"] == 500)

user = payer()
arm(user, 100)
check("a $1 top up starts the reload amount at $1",
      user["auto_reload"]["amount_cents"] == 100)

user = payer(auto_reload={"enabled": False, "explicit": True,
                          "amount_cents": 2000})
arm(user, 500)
check("a later payment re-enables autopay after a manual off "
      "and keeps the picked amount",
      user["auto_reload"] == {"enabled": True, "explicit": True,
                              "amount_cents": 2000})

user = payer(is_owner=True)
arm(user, 5000)
check("a payment arms the owner like any other payer "
      "(round 2: the owner exclusion is reversed for "
      "auto reload)",
      user["auto_reload"] == {"enabled": True,
                              "explicit": True,
                              "amount_cents": 2000})

user = payer(stripe_payment_method_id=None)
arm(user, 5000)
check("a payment with no saved card arms nothing",
      "auto_reload" not in user)

# -- charge sizing ---------------------------------------------

exec(fn("_auto_reload_charge_cents"), ns)
size = ns["_auto_reload_charge_cents"]
check("no need known charges the configured amount",
      size(1000, 0, None) == 1000)
check("a $3 shortfall rounds up to the $5 denomination",
      size(100, 0, 300) == 500)
check("the configured amount is the floor",
      size(2000, 0, 300) == 2000)
check("a shortfall past the largest denomination rounds up "
      "to whole dollars", size(500, 0, 2300) == 2300)
check("a $50 shortfall charges exactly $50",
      size(500, 0, 5000) == 5000)
check("a shortfall past the $50 ceiling charges nothing",
      size(500, 0, 5001) is None)
check("no shortfall charges the configured amount",
      size(2000, 5000, 3000) == 2000)
check("the ceiling constant is $50",
      pricing.AUTOPAY_MAX_CHARGE_CENTS == 5000)

# -- the charge attempt -----------------------------------------

credits = []


def credit_topup(user, credit_cents, gross_cents):
    credits.append((credit_cents, gross_cents))
    user["balance_cents"] = int(user.get("balance_cents", 0)) \
        + int(credit_cents)
    user["net_paid_cents"] = float(user.get("net_paid_cents")
                                   or 0.0) \
        + pricing.net_of(gross_cents)


class FakePI:
    def __init__(self, amount, status):
        self.id = "pi_test_1"
        self.status = status
        self.amount_received = amount


class FakeStripe:
    def __init__(self, status="succeeded", raises=False):
        self.status = status
        self.raises = raises
        self.calls = []
        outer = self

        class PaymentIntent:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                if outer.raises:
                    raise RuntimeError("card declined")
                return FakePI(kwargs["amount"], outer.status)

        self.PaymentIntent = PaymentIntent


holder = {"stripe": FakeStripe()}


def stripe_client():
    return holder["stripe"], None


ns2 = {"pricing": pricing, "is_owner": is_owner,
       "stripe_client": stripe_client,
       "credit_topup": credit_topup,
       "save_user": lambda user: None, "meta": {}}
exec(fn("_auto_reload_charge_cents"), ns2)
exec(fn("attempt_auto_reload"), ns2)
exec(fn("reload_fail_detail"), ns2)
attempt = ns2["attempt_auto_reload"]
fail_detail = ns2["reload_fail_detail"]


def armed(balance=100, **over):
    user = payer(balance_cents=balance,
                 auto_reload={"enabled": True, "explicit": True,
                              "amount_cents": 500})
    user.update(over)
    return user


credits.clear()
holder["stripe"] = FakeStripe()
user = armed()
state = {}
check("insufficient balance with autopay on charges once "
      "and the job proceeds",
      attempt(user, state, needed_cents=800) is True
      and len(holder["stripe"].calls) == 1
      and holder["stripe"].calls[0]["amount"] == 1000
      and credits == [(1000, 1000)]
      and user["balance_cents"] == 1100)
check("the same job never charges twice",
      attempt(user, state, needed_cents=800) is True
      and len(holder["stripe"].calls) == 1)

credits.clear()
holder["stripe"] = FakeStripe()
user = armed(auto_reload={"enabled": False, "explicit": True,
                          "amount_cents": 500})
check("autopay off leaves the refusal unchanged",
      attempt(user, {}, needed_cents=800) is False
      and holder["stripe"].calls == [] and credits == [])

holder["stripe"] = FakeStripe()
user = armed(auto_reload={"enabled": True, "amount_cents": 500})
check("a config without the explicit stamp never fires",
      attempt(user, {}, needed_cents=800) is False
      and holder["stripe"].calls == [])

credits.clear()
holder["stripe"] = FakeStripe(status="requires_payment_method")
user = armed()
state = {}
check("a charge needing authentication refuses the job with "
      "no credit and no dispatch",
      attempt(user, state, needed_cents=800) is False
      and credits == [] and user["balance_cents"] == 100
      and fail_detail(state, "plain refusal")
      == "plain refusal, and the reload did not go through")

credits.clear()
holder["stripe"] = FakeStripe(raises=True)
user = armed()
check("a declined charge refuses the job unchanged",
      attempt(user, {}, needed_cents=800) is False
      and credits == [] and user["balance_cents"] == 100)

holder["stripe"] = FakeStripe()
user = armed(balance=0)
state = {}
check("a job needing more than the ceiling covers is refused "
      "with no charge attempted",
      attempt(user, state, needed_cents=6000) is False
      and holder["stripe"].calls == []
      and "attempted" not in state)

holder["stripe"] = FakeStripe()
user = armed(is_owner=True)
check("the owner is never charged",
      attempt(user, {}, needed_cents=800) is False
      and holder["stripe"].calls == [])

# -- saved card display facts ------------------------------------

class FakePM:
    class card:
        brand = "visa"
        last4 = "4242"


class PMStripe:
    class PaymentMethod:
        @staticmethod
        def retrieve(pm_id):
            return FakePM()


class PMStripeBroken:
    class PaymentMethod:
        @staticmethod
        def retrieve(pm_id):
            raise RuntimeError("no stripe")


ns3 = {}
exec(fn("_save_payment_method"), ns3)
save_pm = ns3["_save_payment_method"]
user = {}
save_pm(user, PMStripe, "pm_9")
check("saving a payment method stores id, brand, and last 4",
      user.get("stripe_payment_method_id") == "pm_9"
      and user.get("card_brand") == "visa"
      and user.get("card_last4") == "4242")
user = {}
save_pm(user, PMStripeBroken, "pm_9")
check("the id is stored even when the display lookup fails",
      user.get("stripe_payment_method_id") == "pm_9"
      and "card_brand" not in user)

# -- webhook and surface wiring ----------------------------------

webhook = fn("stripe_webhook")
check("the webhook arms autopay at every money landing",
      webhook.count("arm_autopay_on_payment") >= 4)
setup_branch = webhook.split('obj.get("mode") == "setup"')[1] \
    .split('elif obj.get("mode") == "subscription"')[0]
check("saving a card alone never arms autopay",
      "arm_autopay_on_payment(user" not in setup_branch)
check("the webhook stores saved cards through the helper",
      "_save_payment_method" in webhook)
check("the account surface exposes the saved card display",
      '"card_brand": user.get("card_brand")' in SRC
      and '"card_last4": user.get("card_last4")' in SRC)
check("the billing panel shows the saved card and the rule",
      "autoreload-card" in SRC
      and "ending in " in SRC
      and "turns auto reload on, so your work never stops "
          "over balance" in SRC
      and "turns on by itself the next time you pay" in SRC)
check("the panel carries the explicit auto reload switch "
      "(restored in round 2)",
      'id="autoreload-toggle"' in SRC
      and "$('autoreload-toggle').checked = "
          "!!s.auto_reload_enabled" in SRC
      and "$('autoreload-toggle').addEventListener("
          "'change'" in SRC)
check("the terms describe the sized charge",
      "up to $50" in SRC)

print("check_autopay: all pins hold")

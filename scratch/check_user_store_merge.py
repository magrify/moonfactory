"""Local checks for the user-store merge and the atomic free
slot (security fix, 2026-11-27).

Audit findings pinned here: whole-record-money-race-lost-update
(HIGH), logout-epoch-resurrected-by-stale-whole-record-save,
train-character-concurrent-submit-lost-update (same root),
api-key-touch-revoke-race. Before the fix every user mutation
was a whole-record read-modify-write: two overlapping requests
on one account lost one update (the audit's harness lost a
300-cent hold outright and defeated the lifetime free cap).

The store machinery is extracted from app.py and exec'd
against a plain dict standing in for the Modal Dict, the way
check_free_counter proves its rules. No Modal calls, no
network.

Run:  modal-venv python scratch/check_user_store_merge.py
"""
import ast
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


def class_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# --- exec the real store machinery against a dict with -------------------
# --- Modal Dict copy semantics (values cross by value, not by alias) -----
import copy as _copy


class FakeDict(dict):
    def get(self, key, default=None):
        value = super().get(key, default)
        return _copy.deepcopy(value)

    def __getitem__(self, key):
        return _copy.deepcopy(super().__getitem__(key))

    def __setitem__(self, key, value):
        super().__setitem__(key, _copy.deepcopy(value))


ns = {"threading": threading, "_copy": _copy, "time": time}
for name in ("_user_lock", "_user_snapshot", "_merge_user_record"):
    exec(func_src(name), ns)
exec(class_src("_UsersStore"), ns)
ns["_USER_DELTA_FIELDS"] = (
    "balance_cents", "net_paid_cents", "cost_settled_cents",
    "free_jobs_total")
ns["_USER_LOCKS"] = {}
ns["_USER_LOCKS_GUARD"] = threading.Lock()

raw = FakeDict()
users = ns["_UsersStore"](raw)

BASE = {
    "id": "u1", "email": "a@example.com", "plan": "creator",
    "balance_cents": 2000, "net_paid_cents": 2000,
    "cost_settled_cents": 0.0, "free_jobs_total": 4,
    "session_epoch": 3, "suspended": False,
    "usage": {"period": "2026-11", "free_jobs": 4},
}
raw["id:u1"] = dict(BASE)

# Two overlapping requests read the same committed record.
req_a = users.get("id:u1")
req_b = users.get("id:u1")
check("_mf_base" in req_a, "reads carry the base snapshot")

# A places a 300-cent hold; B, from the same stale base,
# places a 200-cent hold and settles 100 cents of cost.
req_a["balance_cents"] -= 300
users["id:u1"] = req_a
req_b["balance_cents"] -= 200
req_b["cost_settled_cents"] += 100.0
users["id:u1"] = req_b
final = raw["id:u1"]
check(final["balance_cents"] == 1500,
      f"both holds land (got {final['balance_cents']}, want 1500)")
check(final["cost_settled_cents"] == 100.0,
      "the settle from the stale writer lands too")
check("_mf_base" not in final, "the snapshot is never stored")

# A logout bumps the session epoch; a stale writer that never
# touched the epoch cannot resurrect the old one.
req_a = users.get("id:u1")
req_b = users.get("id:u1")
req_a["session_epoch"] = 9
users["id:u1"] = req_a
req_b["handle"] = "newhandle"
users["id:u1"] = req_b
final = raw["id:u1"]
check(final["session_epoch"] == 9,
      "a stale save cannot lower the session epoch (logout holds)")
check(final.get("handle") == "newhandle",
      "the stale writer's own change still lands")

# A suspension landing in one request survives another
# request's unrelated save from a stale copy.
req_a = users.get("id:u1")
req_b = users.get("id:u1")
req_a["suspended"] = True
users["id:u1"] = req_a
req_b["balance_cents"] -= 50
users["id:u1"] = req_b
final = raw["id:u1"]
check(final["suspended"] is True,
      "a suspension survives a stale whole-record save")
check(final["balance_cents"] == 1450,
      "the stale writer's deduction still composes")

# Two overlapping free-counter increments both count.
raw["id:u1"] = dict(BASE)
req_a = users.get("id:u1")
req_b = users.get("id:u1")
req_a["free_jobs_total"] += 1
users["id:u1"] = req_a
req_b["free_jobs_total"] += 1
users["id:u1"] = req_b
check(raw["id:u1"]["free_jobs_total"] == 6,
      "both free increments land (no lost count)")

# --- the atomic free slot ------------------------------------------------
meta = {"freecount:total": 0}


def free_jobs_total(user):
    return int(user.get("free_jobs_total") or 0)


def usage(user):
    u = user.get("usage")
    if not isinstance(u, dict):
        u = {"period": "2026-11", "free_jobs": 0}
    return u


def save_user(user):
    users[f"id:{user['id']}"] = user


fns = {"users": users, "meta": meta, "free_jobs_total": free_jobs_total,
       "usage": usage, "save_user": save_user,
       "_user_lock": ns["_user_lock"],
       "FREE_CLIPS_TOTAL": 5, "FREE_PLATFORM_TOTAL_CEILING": 100000}
for name in ("reserve_free_slot", "release_free_slot"):
    exec(func_src(name), fns)

raw["id:u1"] = dict(BASE)  # free_jobs_total = 4, one slot left
results = []


def racer():
    status, _fresh = fns["reserve_free_slot"]({"id": "u1"})
    results.append(status)


threads = [threading.Thread(target=racer) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check(results.count("ok") == 1,
      f"exactly one of eight racers wins the last free slot "
      f"(got {results.count('ok')})")
check(raw["id:u1"]["free_jobs_total"] == 5,
      "the counter stops exactly at the cap")
fns["release_free_slot"]({"id": "u1"})
check(raw["id:u1"]["free_jobs_total"] == 4,
      "release returns the slot")

# --- the api key touch ----------------------------------------------------
kns = {"threading": threading, "time": time,
       "api_key_digest": lambda k: "dg:" + k}
exec("import hashlib\n"
     "def api_key_digest(k):\n"
     "    return 'dg:' + hashlib.sha256(k.encode()).hexdigest()[:16]",
     kns)
for name in ("api_key_revoke", "api_key_touch"):
    exec(func_src(name), kns)
kns["_API_KEY_LOCK"] = threading.Lock()
store = {}
key = "mf_live_testkey"
store[kns["api_key_digest"](key)] = {
    "id": "k1", "user_id": "u1", "revoked": False,
    "last_used_at": 0}
store["user:u1"] = [kns["api_key_digest"](key)]
check(kns["api_key_revoke"](store, "u1", "k1") is True, "revoke works")
kns["api_key_touch"](store, key, now=10000)
entry = store[kns["api_key_digest"](key)]
check(entry["revoked"] is True,
      "a touch after revoke cannot resurrect the key")
check(not entry.get("last_used_at"),
      "a revoked key is not stamped")

# --- source pins: every create core claims its free slot atomically -------
for flow in ("create_job_core", "create_image_core",
             "create_template_core", "create_short_core"):
    check("reserve_free_slot(user)" in func_src(flow),
          f"{flow} claims its free slot atomically")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_user_store_merge: all checks passed")

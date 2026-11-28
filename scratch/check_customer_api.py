"""Local checks for the customer API (2026-11-27).

Both competitors offer a developer API and Moonfactory was
website only; the owner ordered the API built. The design rule
this suite pins: there is no parallel system. The /api/v1
endpoints resolve an API key to the owning account and then
call the very cores the website's own endpoints call, so an
API job is a normal job in the same library, gated by the same
gates, and charged to the same topped up balance the website
spends. There is no separate API wallet, no separate top up,
and no API credit anywhere in the build.

Behavior is proven the way the repo proves behavior locally:
the module level key helpers are exec'd against plain dicts
(create, resolve, list, revoke, touch), and the endpoint
wiring is pinned by source (each v1 endpoint resolves its key
first, and each create or quote surface on both sides calls
the one shared core). No Modal calls, no network, no jobs.

Run:  modal-venv python scratch/check_customer_api.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402

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


# -- the key store ------------------------------------------------------

check('api_keys = modal.Dict.from_name("moonfactory-api-keys"'
      in SRC, "the API key store is not declared")
check(app.API_KEY_PREFIX == "mf_live_",
      "the key prefix is not mf_live_")
check(app.API_KEY_RATE_LIMIT_PER_MINUTE == 120,
      "the per key pace limit is not 120 a minute")

# -- behavior: the key lifecycle against a plain dict -------------------

store = {}
full, entry = app.api_key_create(store, "u1", "laptop scripts", now=1000.0)
check(full.startswith("mf_live_"), "a minted key lacks the prefix")
check(len(full) > 30, "a minted key is suspiciously short")
check(entry["user_id"] == "u1" and entry["name"] == "laptop scripts",
      "the stored entry lost its owner or its name")
check(entry["revoked"] is False and entry["last_used_at"] is None,
      "a fresh entry starts revoked or already used")
check(app.api_key_digest(full) in store,
      "the key is not stored under its digest")
check(full not in str(store.get(app.api_key_digest(full))),
      "the full key itself is stored somewhere on the record")

resolved = app.api_key_resolve(store, full)
check(resolved is not None and resolved["id"] == entry["id"],
      "a fresh key does not resolve to its own entry")
check(app.api_key_resolve(store, "mf_live_wrongwrongwrong") is None,
      "an unknown key resolves")
check(app.api_key_resolve(store, "") is None,
      "an empty key resolves")
check(app.api_key_resolve(store, None) is None,
      "a missing key resolves")
check(app.api_key_resolve(store, "sk_live_whatever") is None,
      "a key without the platform prefix resolves")

views = app.api_key_list(store, "u1")
check(len(views) == 1 and views[0]["id"] == entry["id"],
      "the owner list does not show the key")
check(views[0]["prefix"] == entry["prefix"],
      "the list view lost the display prefix")
check(all(full not in str(v) and app.api_key_digest(full) not in str(v)
          for v in views),
      "a list view leaks the full key or its digest")
check(app.api_key_list(store, "u2") == [],
      "another account sees this account's keys")
check(app.api_key_active_count(store, "u1") == 1,
      "the live key count is wrong")

# Touch stamps last use once, then throttles its own writes.
app.api_key_touch(store, full, now=2000.0)
check(store[app.api_key_digest(full)]["last_used_at"] == 2000.0,
      "a resolve does not stamp last use")
app.api_key_touch(store, full, now=2100.0)
check(store[app.api_key_digest(full)]["last_used_at"] == 2000.0,
      "the last use stamp writes on every call")
app.api_key_touch(store, full, now=2400.0)
check(store[app.api_key_digest(full)]["last_used_at"] == 2400.0,
      "the last use stamp never refreshes")

# Revocation is scoped to the owner and takes effect at once.
check(app.api_key_revoke(store, "u2", entry["id"]) is False,
      "another account revoked this account's key")
check(app.api_key_resolve(store, full) is not None,
      "a failed revoke still killed the key")
check(app.api_key_revoke(store, "u1", entry["id"]) is True,
      "the owner cannot revoke their own key")
check(app.api_key_resolve(store, full) is None,
      "a revoked key still resolves")
check(app.api_key_revoke(store, "u1", entry["id"]) is False,
      "a second revoke claims success")
check(app.api_key_active_count(store, "u1") == 0,
      "a revoked key still counts as live")
views = app.api_key_list(store, "u1")
check(len(views) == 1 and views[0]["revoked"] is True,
      "the list view hides the revoked key or its state")

# -- wiring: key management endpoints -----------------------------------

for route in ('@web.get("/api/me/api-keys")',
              '@web.post("/api/me/api-keys")',
              '@web.delete("/api/me/api-keys/{key_id}")'):
    check(route in SRC, f"the account area route {route} is missing")
create_key_src = func_src("create_api_key")
check("api_key_create(" in create_key_src
      and '"key": full_key' in create_key_src,
      "key creation does not return the full key exactly once")
check("API_KEY_MAX_PER_ACCOUNT" in create_key_src,
      "key creation does not enforce the live key cap")
check("api_key_revoke(" in func_src("delete_api_key"),
      "the revoke endpoint does not revoke")
check("require_user" in func_src("list_api_keys")
      and "require_user" in create_key_src
      and "require_user" in func_src("delete_api_key"),
      "a key management endpoint is not behind the session")

# -- wiring: the v1 surface ----------------------------------------------

for route in ('@web.get("/api/v1/models")',
              '@web.get("/api/v1/balance")',
              '@web.post("/api/v1/quote")',
              '@web.post("/api/v1/images/quote")',
              '@web.post("/api/v1/jobs")',
              '@web.post("/api/v1/images")',
              '@web.get("/api/v1/jobs")',
              '@web.get("/api/v1/jobs/{job_id}")',
              '@web.get("/api/v1/jobs/{job_id}/file")'):
    check(route in SRC, f"the v1 route {route} is missing")

for name in ("v1_models", "v1_balance", "v1_quote", "v1_quote_image",
             "v1_create_job", "v1_create_image", "v1_list_jobs",
             "v1_get_job", "v1_job_file"):
    check("require_api_user(authorization)" in func_src(name),
          f"{name} does not resolve its API key first")

auth_src = func_src("require_api_user")
check("api_key_resolve(" in auth_src,
      "the API auth does not resolve through the key store")
check("_rate_limited(\"apiv1\", entry[\"id\"], "
      "API_KEY_RATE_LIMIT_PER_MINUTE)" in auth_src,
      "the API auth does not pace each key")
check('users.get(f"id:{entry[\'user_id\']}")' in auth_src,
      "the API auth does not resolve the key to its own account")

# -- parity: both surfaces call the one core ------------------------------

check("return quote_job_core(user, body)" in func_src("quote_job"),
      "the website quote does not call the shared quote core")
check("return quote_job_core(user, body)" in func_src("v1_quote"),
      "the API quote does not call the shared quote core")
check("return quote_image_core(user, body)" in func_src("quote_image"),
      "the website image quote does not call the shared core")
check("return quote_image_core(user, body)" in func_src("v1_quote_image"),
      "the API image quote does not call the shared core")

check("return create_job_core(" in func_src("create_job"),
      "the website create does not call the shared create core")
check("create_job_core(" in func_src("v1_create_job"),
      "the API create does not call the shared create core")
check("return create_image_core(user, body, client_ip(request))"
      in func_src("create_image"),
      "the website image create does not call the shared core")
check("create_image_core(user, body, client_ip(request))"
      in func_src("v1_create_image"),
      "the API image create does not call the shared core")
check("list_jobs_core(" in func_src("list_jobs")
      and "list_jobs_core(" in func_src("v1_list_jobs"),
      "the two job lists do not share the one list core")
check("get_job_core(" in func_src("get_job")
      and "get_job_core(" in func_src("v1_get_job"),
      "the two job reads do not share the one read core")
check("job_file_core(" in func_src("job_file")
      and "job_file_core(" in func_src("v1_job_file"),
      "the two file reads do not share the one file core")

# The cores still carry the gates (the parity claim itself).
video_core = func_src("create_job_core")
check("enforce_prompt_gate(" in video_core,
      "the shared video core lost the illegal content gate")
check("adult_gate_refusal(" in video_core,
      "the shared video core lost the adult gate")
check("ceiling_allows(" in video_core,
      "the shared video core lost the hard cost ceiling")
image_core = func_src("create_image_core")
check("enforce_prompt_gate(" in image_core,
      "the shared image core lost the illegal content gate")
check("adult_gate_refusal(" in image_core,
      "the shared image core lost the adult gate")

# The API wrappers run the website's own account checks, and
# the bot check stays a website only step (the key is the API
# credential check in its place).
checks_src = func_src("_api_create_account_checks")
check('_rate_limited(bucket, user["id"], 10)' in checks_src
      and 'user["verified"]' in checks_src
      and 'user.get("past_due")' in checks_src,
      "the API create checks drifted from the website's")
check("bot_check_ok" not in func_src("v1_create_job")
      and "bot_check_ok" not in func_src("v1_create_image"),
      "an API create runs the browser bot check")
check("bot_check_ok(body, client_ip(request), user)"
      in func_src("create_job")
      and "bot_check_ok(body, client_ip(request), user)"
      in func_src("create_image"),
      "a website create lost its bot check in the split")

# -- no separate API billing ----------------------------------------------

check("api_balance" not in SRC and "api_credit" not in SRC
      and "api_wallet" not in SRC,
      "a separate API balance, credit, or wallet appeared")
check('"/api/v1/balance"' in SRC
      and "public_user(user)" in func_src("v1_balance"),
      "the API balance read is not the account's own balance view")

# -- docs ------------------------------------------------------------------

check('@web.get("/api-docs"' in SRC, "the docs page route is missing")
check("API_DOCS_HTML" in SRC, "the docs page body is missing")
check("no separate API wallet" in app.API_DOCS_HTML,
      "the docs do not state the one balance rule plainly")
check("/api/v1/jobs" in app.API_DOCS_HTML
      and "curl" in app.API_DOCS_HTML,
      "the docs lost the endpoint list or the curl example")
check('{"detail": "a valid API key is required"}' in app.API_DOCS_HTML,
      "the docs do not show the error shape")
check('href="/api-docs"' in SRC,
      "nothing in the app links to the docs page")
check('id="api-keys-list"' in SRC and 'id="api-key-create"' in SRC,
      "the API surface lost its API keys section")
check("loadApiKeys" in SRC,
      "the API surface does not load the key list")

# -- placement: API is its own menu entry and panel (Jack, 2026-11-27) --
# The API keys management moved OUT of the Account panel into its
# own panel, reached from its own "API" entry in the profile menu,
# a sibling of Account and Billing. It is NOT a studio tab: the
# tab bar keeps exactly Create / Clip / Clone.

check('<a href="#" id="menu-api">API</a>' in SRC,
      "the profile menu has no API entry of its own")
check("$('menu-api').onclick" in SRC
      and "openPanel('panel-api')" in SRC,
      "the API menu entry does not open the API panel")
check("$('menu-api').onclick" in SRC
      and "loadApiKeys();" in SRC.split("$('menu-api').onclick")[1][:200],
      "opening the API panel does not load the key list")
acct = SRC[SRC.index('id="panel-account"'):SRC.index('id="panel-billing"')]
check('id="api-keys-list"' not in acct
      and 'id="api-key-create"' not in acct
      and "API keys" not in acct,
      "the Account panel still carries the API keys section")
api_panel = SRC[SRC.index('id="panel-api"'):SRC.index('id="panel-ec-library"')]
for needle, what in (
        ('id="api-keys-list"', "the key list"),
        ('id="api-key-name"', "the key name input"),
        ('id="api-key-create"', "the create key button"),
        ('id="api-key-new"', "the one time key display"),
        ('id="api-key-msg"', "the key message line"),
        ("https://magrify--moonfactory-api.us-east.modal.run",
         "the base address"),
        ('href="/api-docs"', "the link to the API docs")):
    check(needle in api_panel, f"the API panel is missing {what}")
# -- panel copy: each fact once, plainly (Jack, 2026-11-28) --
# The panel used to state the one-balance rule twice in nearly the
# same words ("charged to the same balance as the website" in the
# intro and again under API keys) and splashed the base address
# across a headed block. Now the intro says the balance fact once,
# the keys section keeps only the one-time-display fact, and the
# base address is one quiet line with a copy button.
check("API jobs charge to the same balance you top up here."
      in api_panel,
      "the API panel intro does not state the one balance rule")
check(api_panel.count("same balance") == 1,
      "the API panel states the balance fact more than once")
check("A key shows in full one time only, at the moment you create it."
      in api_panel,
      "the API keys section lost the one time display fact")
check("as the website" not in api_panel,
      "the API panel still says 'as the website'")
check("Base address" not in api_panel
      and "Every call starts from that address" not in api_panel,
      "the API panel still features the base address block")
check("Base URL:" in api_panel and 'id="api-base-copy"' in api_panel
      and "$('api-base-copy').onclick" in SRC,
      "the API panel lost the quiet base URL line or its copy button")
check("open Account, then find the API keys section"
      not in app.API_DOCS_HTML,
      "the docs still send readers to the Account panel for keys")
check(SRC.count('id="tab-') == 3
      and 'id="tab-api"' not in SRC,
      "the studio tab bar is not exactly Create / Clip / Clone")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_customer_api")

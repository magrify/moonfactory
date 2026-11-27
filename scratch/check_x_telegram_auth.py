"""Local checks for X and Telegram social sign-in
(Jack, 2026-11-25: "proceed w x and telegram").

The decision these pins hold: neither provider can return
a verified email, so on Jack's call their accounts are
keyed by the provider's own user id instead. Rules pinned
here:

- The account model carries provider identities: save_user
  indexes an account by its provider id alongside the id
  (canonical) and email indexes, and an email index is only
  written for an account that has an email. A provider
  account mirrors the Google path's record (no password,
  provider-verified, acceptance stamped and logged) with
  email None and the provider id in place of the email, and
  resolution reloads the canonical record through the id
  index before trusting it.
- X is OAuth 2.0 with PKCE, mirroring the Google flow: the
  start route 404s unconfigured, the PKCE verifier stays
  server side in the one-time state record, the state is
  spent before the token exchange, the identity comes from
  /2/users/me, and the sign-in lands through the shared
  redirect tail (parked creation + login session cookie).
- Telegram is the Login Widget: the studio embeds the
  widget only when the bot token and username are
  configured, the widget's answer is posted to the login
  endpoint, and the endpoint believes it only when the
  HMAC-SHA256 check against the bot token passes and the
  auth_date is fresh. The HMAC check itself is exercised
  behaviorally below, not just read.
- The dialog stacks Google, X, Telegram, OR divider,
  Continue with email; each provider row starts hidden and
  renders only when /api/config reports it configured, and
  the divider and shield line follow any configured
  provider.

No Modal calls, no network: the wiring is pinned in source
and the dialog in the served HTML; the HMAC verification
runs for real against Telegram's published algorithm.

Run:  modal-venv python scratch/check_x_telegram_auth.py
"""
import ast
import hashlib
import hmac
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
HTML = app.APP_HTML


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the dialog stacks the providers in the design's order -------
auth = HTML.split('<div id="auth"', 1)[1].split(
    '<div id="adult-gate"', 1)[0]
order = [auth.find('id="auth-google"'), auth.find('id="auth-x"'),
         auth.find('id="auth-telegram"'), auth.find('id="auth-or"'),
         auth.find('id="auth-email-toggle"')]
check(all(pos > -1 for pos in order) and order == sorted(order),
      "the dialog stacks Google, X, Telegram, OR, email")
for el in ("auth-x", "auth-telegram"):
    check(f'id="{el}" style="display:none"' in auth
          or f'id="{el}" href="/api/auth/x/start" '
             'style="display:none"' in auth,
          f"{el} starts hidden until config proves the provider")

# -- the account model carries provider identities ---------------
save = func_src("save_user")
check('if committed.get("email"):' in save
      and 'users[f"email:{committed[\'email\']}"] = committed' in save,
      "the email index is written only for accounts with one")
check('users[f"{provider}:{committed[\'provider_id\']}"] = committed' in save,
      "save_user indexes the account by its provider identity")

resolver = func_src("_provider_account")
check('users.get(f"{provider}:{provider_id}")' in resolver,
      "a provider sign-in resolves by the provider user id")
check('users.get(f"id:{found[\'id\']}") or found' in resolver,
      "resolution reloads the canonical id record")
check('"email": None' in resolver and '"pw": ""' in resolver,
      "a provider account carries no email and no password")
check('"auth_provider": provider' in resolver
      and '"provider_id": str(provider_id)' in resolver,
      "the record stamps its provider and provider id")
check('"verified": True' in resolver,
      "a provider-created account starts verified")
check('"terms_accepted_at": _now()' in resolver
      and '"legal_version": LEGAL_VERSION' in resolver
      and "record_acceptance(user, ACCEPTANCE_SIGNUP_LEGAL,"
      in resolver,
      "a provider-created account stamps and logs the "
      "acceptance like the Google path")
check('"display_name": display_name or None' in resolver,
      "the provider profile name rides the record")

pub = func_src("public_user")
check('"email": user.get("email")' in pub
      and '"display_name": user.get("display_name")' in pub
      and '"auth_provider": user.get("auth_provider")' in pub,
      "public_user tolerates an email-less account and "
      "surfaces its provider label")

stripe = func_src("ensure_stripe_customer")
check('if user.get("email"):' in stripe
      and 'create_kwargs["email"] = user["email"]' in stripe,
      "a Stripe customer is created without an email when "
      "the account has none")

# -- /api/config reports every provider ---------------------------
check('"x": _x_oauth_configured(),' in SRC
      and '"telegram": _telegram_configured(),' in SRC
      and '"telegram_bot_username":' in SRC,
      "/api/config reports the X and Telegram states")
xconf = func_src("_x_oauth_configured")
check('os.environ.get("X_CLIENT_ID")' in xconf
      and 'os.environ.get("X_CLIENT_SECRET")' in xconf,
      "X is configured only with both credentials present")
tgconf = func_src("_telegram_configured")
check('os.environ.get("TELEGRAM_BOT_TOKEN")' in tgconf
      and "_telegram_bot_username()" in tgconf,
      "Telegram is configured only with token and username")

# -- X: OAuth 2.0 with PKCE, Google's shape -----------------------
start = func_src("x_oauth_start")
check("if not _x_oauth_configured():" in start
      and "status_code=404" in start,
      "the X start route 404s when no credentials are carried")
check('meta[f"oauth-state:x:{state}"]' in start
      and '"verifier": verifier' in start,
      "the PKCE verifier stays server side in the state record")
check('"code_challenge_method": "S256"' in start
      and "hashlib.sha256(verifier.encode())" in start,
      "the authorize request carries the S256 challenge")
check("https://twitter.com/i/oauth2/authorize" in start,
      "the start route sends the browser to X")
check("/api/auth/x/callback" in start
      and '"scope": "tweet.read users.read"' in start,
      "the authorize request names the callback and scopes")

cb = func_src("x_oauth_callback")
check('url="/app?oauth=error"' in cb,
      "every X failure lands back on the studio with the reason")
spend = cb.find('"verifier": "", "expires": 0}')
exchange = cb.find("https://api.twitter.com/2/oauth2/token")
check(-1 < spend < exchange,
      "the X state is spent before the token exchange runs")
check('"code_verifier": record["verifier"]' in cb,
      "the token exchange answers the PKCE challenge")
check('auth=(os.environ["X_CLIENT_ID"],' in cb,
      "the token exchange authenticates as a confidential "
      "client")
check("https://api.twitter.com/2/users/me" in cb,
      "the identity comes from X's user lookup")
check('_provider_account("x", x_id, display_name)' in cb,
      "the X identity resolves its provider account")
check("return _social_signin_redirect(user, record, request)" in cb,
      "the X sign-in lands through the shared redirect tail")

tail = func_src("_social_signin_redirect")
check("_land_parked_create(user, record, request)" in tail
      and '_make_session(user["id"],' in tail
      and "max_age=7 * 24 * 3600, httponly=True, samesite=\"lax\""
      in tail,
      "the shared tail lands the parked creation and issues "
      "the login session cookie")

# -- Telegram: the widget and its verified answer ------------------
modes = SRC.split("let authMode = 'create';", 1)[1].split(
    "// File payloads are the one thing", 1)[0]
check("https://telegram.org/js/telegram-widget.js?22" in modes
      and "data-telegram-login" in modes
      and "data-onauth" in modes
      and "onTelegramAuth(user)" in modes,
      "the studio embeds Telegram's widget against the "
      "configured bot")
check("if (telegram && providers.telegram_bot_username)" in modes
      and "loadTelegramWidget(providers.telegram_bot_username)"
      in modes,
      "the widget loads only when Telegram is configured")
check("api('/api/auth/telegram/login'" in modes
      and "await afterSignIn(r.user);" in modes,
      "the widget's answer posts to the login endpoint and "
      "completes like the email sign in")
check("$('auth-x').onclick = e => followProviderStart(e, "
      "'/api/auth/x/start');" in modes,
      "the X pill parks and follows its start route like "
      "Google's")
check("const anyProvider = google || x || telegram;" in modes
      and "$('auth-or').style.display = anyProvider ? '' : 'none'"
      in modes
      and "$('auth-secure').style.display = anyProvider ? '' : "
      "'none'" in modes,
      "the divider and shield line follow any configured "
      "provider")

login = func_src("telegram_login")
check("if not _telegram_configured():" in login
      and "status_code=404" in login,
      "the Telegram endpoint 404s when unconfigured")
check('_rate_limited("telegram-login", ip, 10)' in login,
      "the Telegram endpoint is rate limited like sign-in")
hash_at = login.find("telegram_login_hash_ok(")
account_at = login.find('_provider_account("telegram"')
check(-1 < hash_at < account_at,
      "the HMAC check runs before any account is resolved")
check('os.environ["TELEGRAM_BOT_TOKEN"]' in login,
      "the check keys on the deployment's bot token")
check("auth_date < _now() - 24 * 3600" in login
      and "auth_date > _now() + 300" in login,
      "a stale or future-dated answer is refused")
check('JSONResponse({"status": "ok", "user": public_user(user)})'
      in login and '_make_session(user["id"],' in login,
      "a verified answer returns the login response and "
      "session")

# -- the HMAC check itself, exercised ------------------------------
TOKEN = "123456:TEST-BOT-TOKEN"
payload = {
    "id": 424242,
    "first_name": "Test",
    "last_name": "User",
    "username": "tester",
    "auth_date": 1700000000,
}
data_check = "\n".join(
    f"{k}={payload[k]}" for k in sorted(payload))
good_hash = hmac.new(
    hashlib.sha256(TOKEN.encode()).digest(),
    data_check.encode(), hashlib.sha256).hexdigest()
signed = dict(payload, hash=good_hash)
check(app.telegram_login_hash_ok(TOKEN, signed),
      "a payload signed the Telegram way verifies")
tampered = dict(signed, first_name="Forged")
check(not app.telegram_login_hash_ok(TOKEN, tampered),
      "one altered field breaks the verification")
check(not app.telegram_login_hash_ok(TOKEN, dict(payload)),
      "a payload with no hash does not verify")
check(not app.telegram_login_hash_ok("", signed),
      "no bot token, no verification")
check(not app.telegram_login_hash_ok("999:OTHER", signed),
      "another bot's token does not verify this payload")

if failures:
    print("FAIL check_x_telegram_auth")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_x_telegram_auth")

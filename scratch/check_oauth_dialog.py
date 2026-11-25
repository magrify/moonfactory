"""Local checks for the PhotoStudioGen-shaped sign-in dialog
and the Google social sign-in behind it (Jack, 2026-11-25).

Rules pinned here:
- The dialog carries the design Jack pointed at: the Play
  Hollow mark centered in a tile, a bold Create your account
  heading, an Already have an account? Sign in toggle line
  that swaps the dialog between create and sign in modes, a
  Continue with Google pill, an OR hairline divider, a
  Continue with email pill that opens the email form, a Need
  help? mailto, a shield line, and a by-continuing line
  naming the Terms, Privacy Policy, and Acceptable Use
  Policy with links.
- A provider button never ships dead: the Google row, the
  divider, and the passwordless line all start hidden, and
  the studio shows them only when /api/config reports the
  deployment carries Google's credentials. No X or Telegram
  button exists in the markup at all (neither flow can
  supply a verified email to this account model, so neither
  is half-wired).
- The email form is the same form as before, folded: the
  acceptance checkbox lives in create mode only, sign in
  mode hides it and the Create account button, and both
  buttons keep their original handlers (pinned elsewhere).
- The Google flow is the standard authorization code flow:
  the start route 404s unconfigured, state is a server-side
  one-time nonce, the callback spends the state before the
  token exchange, only a Google-verified email resolves an
  account, a new account mirrors sign-up's record with the
  acceptance stamped and logged, an existing unverified
  account is verified by the sign-in, a parked creation
  lands on the account, and the session cookie issued is
  the login cookie.

No Modal calls, no network: the wiring is pinned in source
and the dialog in the served HTML.

Run:  modal-venv python scratch/check_oauth_dialog.py
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
HTML = app.APP_HTML


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the dialog carries the design ------------------------------
auth = HTML.split('<div id="auth"', 1)[1].split(
    '<div id="adult-gate"', 1)[0]
check('class="auth-tile"' in auth
      and "M23.3 8A16 16 0 1 0 32.6 37.5" in auth,
      "the Play Hollow mark sits centered in its tile")
check('<h2 id="auth-heading">Create your account</h2>' in auth,
      "the dialog opens on the Create your account heading")
check('<span id="auth-toggle-text">Already have an account?</span>'
      in auth and 'id="auth-mode-link">Sign in</a>' in auth,
      "the toggle line offers Sign in under the heading")
check('id="auth-google" href="/api/auth/google/start"' in auth
      and "Continue with Google" in auth,
      "the Google pill points at the OAuth start route")
check('id="auth-or"' in auth and "<span>OR</span>" in auth,
      "the OR hairline divider is present")
check('id="auth-email-toggle"' in auth
      and "Continue with email" in auth,
      "the email path opens from its own pill")
check("Continue with X" not in HTML
      and "Continue with Telegram" not in HTML,
      "no X or Telegram button ships (neither is wired)")
check('<a href="mailto:support@magrify.com">Need help?</a>' in auth,
      "Need help? mails the support address")
check("Secure passwordless authentication" in auth,
      "the shield line is present")
check("By continuing, you agree to our" in auth,
      "the by-continuing line is present")
for href in ("/terms", "/privacy", "/acceptable-use"):
    check(f'href="{href}" target="_blank" rel="noopener"' in auth,
          f"the dialog links {href} in a new tab")

# -- nothing renders that cannot answer --------------------------
for el in ("auth-oauth", "auth-or", "auth-secure"):
    check(f'id="{el}" style="display:none"' in auth,
          f"{el} starts hidden until config proves the provider")
check('id="auth-email-form" style="display:none"' in auth,
      "the email form starts folded behind its pill")
check('id="login" style="width:100%;display:none"' in auth
      and 'id="signup" style="width:100%"' in auth,
      "the dialog opens in create mode: Create account shows, "
      "Sign in waits for the toggle")

# -- the mode toggle and provider wiring in the studio script ----
modes = SRC.split("let authMode = 'create';", 1)[1].split(
    "// File payloads are the one thing", 1)[0]
check("create ? 'Create your account' : 'Sign in'" in modes,
      "the toggle swaps the heading wording")
check("'Already have an account?' : 'New to Moonfactory?'"
      in modes, "the toggle swaps its own line")
check("$('auth-accept-row').style.display = create ? '' : 'none'"
      in modes, "the acceptance box belongs to create mode only")
check("$('signup').style.display = create ? '' : 'none'" in modes
      and "$('login').style.display = create ? 'none' : ''" in modes,
      "each mode shows exactly its own submit button")
check("function openEmailForm()" in modes
      and "$('auth-email-toggle').onclick" in modes,
      "the email pill opens the folded form")
check("api('/api/auth/park'" in modes
      and "pending_create: serializePending(pendingCreate)" in modes,
      "the Google click parks the built creation first")
check("window.location.href = href;" in modes,
      "the Google click then follows the start route")
check("cfg.oauth_providers && cfg.oauth_providers.google" in modes,
      "provider visibility reads /api/config, not a guess")
check("applyOAuthProviders(); buildCatalog();" in SRC,
      "the studio applies provider visibility when config lands")
check("get('oauth') === 'error'" in modes,
      "a failed social sign in reopens the dialog with the reason")

# -- /api/config reports only what the deployment carries --------
check('"oauth_providers": {"google": _google_oauth_configured()}'
      in SRC, "/api/config reports the Google credential state")
helper = func_src("_google_oauth_configured")
check('os.environ.get("GOOGLE_OAUTH_CLIENT_ID")' in helper
      and 'os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")' in helper,
      "configured means both Google credentials are present")

# -- the park endpoint -------------------------------------------
park = func_src("oauth_park")
check("_clean_pending_create(body.get(\"pending_create\"))" in park,
      "the park stores a cleaned pending creation")
check('_rate_limited("oauth-park", ip, 5)' in park,
      "the park endpoint is rate limited like sign-up")
check('meta[f"oauth-park:{park_id}"]' in park,
      "the park lives server side under its own id")

# -- the start route ----------------------------------------------
start = func_src("google_oauth_start")
check("if not _google_oauth_configured():" in start
      and "status_code=404" in start,
      "the start route 404s when no credentials are carried")
check('meta[f"oauth-state:{state}"]' in start,
      "the state is a server-side nonce, not a signed guess")
check("https://accounts.google.com/o/oauth2/v2/auth" in start,
      "the start route sends the browser to Google")
check("/api/auth/google/callback" in start
      and '"scope": "openid email profile"' in start,
      "the authorize request names the callback and scopes")

# -- the callback --------------------------------------------------
cb = func_src("google_oauth_callback")
check('url="/app?oauth=error"' in cb,
      "every failure lands back on the studio with the reason")
spend = cb.find('{"park": "", "expires": 0}')
exchange = cb.find("https://oauth2.googleapis.com/token")
check(-1 < spend < exchange,
      "the state is spent before the token exchange runs")
check("https://openidconnect.googleapis.com/v1/userinfo" in cb,
      "the identity comes from Google's userinfo")
check('info.get("email_verified") is not True' in cb,
      "an unverified Google email resolves nothing")
check('users.get(f"email:{email}")' in cb,
      "accounts resolve by the verified email")
check('"auth_provider": "google"' in cb and '"pw": ""' in cb,
      "a provider account carries no password")
check('"verified": True' in cb,
      "a provider-created account starts verified")
check('"terms_accepted_at": _now()' in cb
      and '"legal_version": LEGAL_VERSION' in cb,
      "a provider-created account stamps the acceptance")
check("record_acceptance(user, ACCEPTANCE_SIGNUP_LEGAL,"
      in cb and "legal_version=LEGAL_VERSION)" in cb,
      "a provider-created account joins the acceptance log")
check('user["verified"] = True' in cb,
      "an existing unverified account is verified by the "
      "sign-in")
check('user["pending_create"] = parked["pending_create"]' in cb,
      "the parked creation lands on the account")
check('resp.set_cookie(\n            "mf_session", '
      '_make_session(user["id"],' in cb
      and "max_age=7 * 24 * 3600, httponly=True, samesite=\"lax\""
      in cb,
      "the callback issues the login session cookie")

if failures:
    print("FAIL check_oauth_dialog")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_oauth_dialog")

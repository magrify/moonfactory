"""Pin for the sign-in page split (Jack, 2026-11-27).

Jack, on the signed-out page: "why is create/clip/clone on
the sign in/create account page? remove it." The page a
request with no valid session is served (on / and /app
alike) is now AUTH_HTML: header and logo, the sign-in card,
the footer, and nothing else. The studio is absent from the
response, not hidden in it: no #studio, no tab bar, no
panes, no #jobs, no account or billing panels. A request
with a valid session gets APP_HTML, today's studio page,
unchanged. The card and its script are sliced out of
APP_HTML at module load (the library page's pattern), so
this pin also holds the two surfaces to one source: the
card in AUTH_HTML is the studio's card stood up visible,
the script is the studio's auth code, and the only
page-specific pieces are its afterSignIn (reload into
/app, where the server serves the studio to the now
signed in browser) and its boot.

The same run raised the service age floor from 13 to 18
on Jack's order ("change this 13 to 18"), so the card's
age confirmation here reads 18, matching the Terms.

No Modal calls, no network: reads the page constants off
the imported app and the route wiring off the source.

Run:  modal-venv python scratch/check_auth_page.py
"""
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
AUTH = app.AUTH_HTML
HTML = app.APP_HTML

# --- the sign-in page carries the whole auth surface ---------------------
for marker in ('id="auth"', 'id="auth-heading"',
               'id="auth-google"', 'href="/api/auth/google/start"',
               'id="auth-x"', 'href="/api/auth/x/start"',
               'id="auth-telegram"', 'id="auth-email-toggle"',
               'id="auth-email-form"', 'id="email"', 'id="password"',
               'id="botcheck"', 'id="accept-terms"',
               'id="login"', 'id="signup"', 'id="forgot-password"',
               'id="auth-msg"', 'id="auth-mode-link"',
               "New accounts must verify their email",
               "By continuing, you agree to our"):
    check(marker in AUTH, f"the sign-in page lost {marker}")
check('id="auth" class="card" style="max-width:460px' in AUTH,
      "the sign-in card stands visible on the sign-in page")
check('id="auth" class="card" style="display:none' not in AUTH,
      "the sign-in card must not start hidden on its own page")
check("and I confirm I am at least 18 years old." in AUTH,
      "the card's age confirmation reads 18 on the sign-in page")

# --- and nothing of the studio --------------------------------------------
for marker in ('id="studio"', 'id="tab-create"', 'id="tab-clip"',
               'id="tab-clone"', 'id="create-pane"', 'id="clip-pane"',
               'id="clone-pane"', 'id="jobs"', 'id="image-jobs"',
               'id="panel-account"', 'id="panel-billing"',
               'id="adult-gate"', 'id="profile"', 'id="nav-signin"',
               "showGuestStudio", "buildCatalog", "loadJobs"):
    check(marker not in AUTH,
          f"the sign-in page still carries studio markup: {marker}")

# --- one source: the card and the script are the studio's own -------------
check('id="auth" class="card" style="display:none' in HTML,
      "the studio page's card still starts hidden, opened in place")
check(AUTH != HTML and len(AUTH) < len(HTML),
      "the sign-in page is its own smaller page, not the studio page")
check(app.AUTH_ETAG != app.APP_ETAG,
      "the sign-in page carries its own ETag")
for marker in ("async function botFieldsOnSubmit",
               "function applyOAuthProviders()",
               "function setAuthMode(mode)",
               "$('signup').onclick", "$('login').onclick",
               "window.onTelegramAuth"):
    check(marker in AUTH,
          f"the sign-in page's script lost the studio auth code: {marker}")
check("async function afterSignIn(u) { window.location.assign('/app'); }"
      in AUTH,
      "a finished sign in reloads into /app for the full studio")
check("if ($('nav-signin')) $('nav-signin').onclick" in AUTH,
      "the studio's Sign in link wiring is guarded on this page")
check("$('auth-back').style.display = 'none';" in AUTH,
      "the card's Back to your video button is folded away here")

# --- the routes split on the session ---------------------------------------
landing = SRC.split("def landing(", 1)[1].split('@web.get("/app"', 1)[0]
studio_route = SRC.split("def studio(", 1)[1].split(
    '@web.get("/terms"', 1)[0]
for name, route in (("root", landing), ("/app", studio_route)):
    check("current_user(request, mf_session)" in route,
          f"the {name} route decides from the session")
    check("return _shell_page(request, APP_HTML, APP_ETAG)" in route,
          f"the {name} route serves the studio page to a session")
    check("return _shell_page(request, AUTH_HTML, AUTH_ETAG)" in route,
          f"the {name} route serves the sign-in page without one")

# --- the age floor is 18 everywhere it surfaces ----------------------------
check("at least 13 years old" not in SRC,
      "the old 13 year service age floor is gone from the app")
check("at least 18 years old" in app.TERMS_HTML,
      "the Terms carry the 18 year service age floor")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_auth_page: all pins hold")

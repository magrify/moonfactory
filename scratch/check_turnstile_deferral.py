"""Local checks for the deferred Turnstile check on the
sign-in dialog (Jack, 2026-11-24).

Rules pinned here:
- The auth dialog opens instantly with no verification
  running: the page-load widget render no longer touches the
  dialog's botcheck container, which is what sat at
  Verifying... inside the still hidden dialog, worst in
  phone in-app browsers.
- The check executes only at the moment of the submit press,
  as part of that submit: the signup handler runs the widget
  on demand (execution execute, token from the execute
  callback) and sends the token with the signup body, exactly
  the field the server has always verified.
- Protection is unchanged in strength: the signup endpoint
  still runs the raw server verifier before anything is
  stored, the gated creation endpoints still route through
  bot_check_ok, and /api/login carries no bot check today and
  gains none (its rate limit is its protection, as before).
  The owner exemption is untouched: botFields and
  renderBotChecks keep their exemption lines, and the
  on-submit path returns nothing for the exempt owner.
- The login handler never calls any bot-fields helper: sign
  in sends email and password only, as today.

No Modal calls, no network: the wiring is pinned in source.

Run:  modal-venv python scratch/check_turnstile_deferral.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

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
            return ast.get_source_segment(SRC, node)
    return None


def js_block(start_marker, end_marker):
    """A studio function's source, sliced on the same markers
    the library page builder uses."""
    if start_marker not in SRC:
        return ""
    return SRC.split(start_marker, 1)[1].split(end_marker, 1)[0]


# --- the dialog widget is not rendered at page load ---------------------------

render_src = js_block("function renderBotChecks()",
                      "function dropBotChecks()")
check("['botcheck2', 'botcheck3', 'botcheck4', 'botcheck5', "
      "'botcheck6', 'botcheck-mf1', 'botcheck-mf2']" in render_src,
      "page load renders exactly the seven creation widgets "
      "(the five originals plus the movie flow's two)")
check("'botcheck'," not in render_src
      and "['botcheck']" not in render_src,
      "page load no longer renders the dialog's botcheck widget")
check("deferred to the Create account" in render_src,
      "the render list says why the dialog widget is absent")

# --- the on-submit execution path ---------------------------------------------

check("async function botFieldsOnSubmit(containerId)" in SRC,
      "the studio carries the on-submit bot check")
submit_src = js_block("async function botFieldsOnSubmit(containerId)",
                      "async function api(")
check("if (botExempt) return {};" in submit_src,
      "the on-submit path asks for nothing once the owner is "
      "signed in")
check("execution: 'execute'" in submit_src,
      "the dialog widget renders in execute mode, inert until "
      "the submit press runs it")
check("turnstile.execute" in submit_src,
      "the submit press executes the widget")
check("turnstile_token" in submit_src,
      "the executed check yields the token the server verifies")
check("return await solvePow();" in submit_src,
      "with no site key the on-submit path falls back to the "
      "same proof of work as before")

check("botFieldsOnSubmit('botcheck')" in SRC,
      "the signup handler runs the check on submit")
check("botFields('botcheck')" not in SRC,
      "nothing polls the dialog widget the old way anymore")

# --- the server still verifies, exactly as before ------------------------------

signup_src = func_src("signup") or ""
check("_verify_bot_check(body, ip)" in signup_src,
      "the signup endpoint still runs the raw server verifier")
check(signup_src.index("legal_acceptance_stamp")
      < signup_src.index("_verify_bot_check"),
      "the acceptance refusal still lands before the bot check")

login_src = func_src("login") or ""
check("_verify_bot_check" not in login_src
      and "bot_check_ok" not in login_src,
      "/api/login carries no bot check, as today")
check("_rate_limited(\"login\", ip, 10)" in login_src,
      "/api/login keeps its rate limit")

for name in ("create_job", "create_image", "create_short"):
    src = func_src(name) or ""
    check("bot_check_ok(body, client_ip(request), user)" in src,
          f"{name} still routes its bot check through the guard")

# The owner exemption lines the deferral must not disturb.
check("if (botExempt) return {};" in SRC,
      "botFields keeps its owner exemption line")
check("if (botExempt) return;\n  if (cfg.turnstile_site_key && window.turnstile)" in SRC,
      "renderBotChecks keeps its owner exemption lines")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_turnstile_deferral")

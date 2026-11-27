"""Pin for the gate-coverage cluster (security fixes, 2026-11-27):
every creation path crosses the shared gates.

- negative_prompt is gated in both create cores
  (negative-prompt-bypasses-illegal-content-gate).
- prompt_help gates its text before the LLM dispatch
  (prompt-help-endpoint-bypasses-illegal-content-gate).
- create_drama gate_texts include wardrobe and role
  (drama-create-gate-omits-wardrobe-and-role-texts).
- Character create and every editclone creation cross the
  suspension bar; train_character crosses verified, past
  due, suspension, a pace limit, and the hard ceiling, and
  claims the character under the account lock before the
  submit (suspension-not-enforced-on-all-create-paths,
  train-character-account-gates-absent,
  train-character-hard-cost-ceiling-not-consulted,
  train-character-concurrent-submit-lost-update,
  train-character-refund-after-fal-accept-orphans-job,
  train-character-limbo-reconciled-only-on-list).
- Character pictures are classified and stamped at
  creation on both character paths, and a character's
  verdict gates its hosted references at consumption
  (input-classifier-character-laundering).
- Drama resume and the retry drama branch share account
  checks and a lifetime, non-resettable attempt budget;
  the driver bars suspended accounts at dispatch
  (drama-resume-resets-attempts-unbounded-redispatch).

Source-level pins over platform/app.py, in the house style.
"""
import ast
import re
import sys
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent
       / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


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


# negative_prompt gated in both create cores
for name in ("create_api_job", "create_job_core"):
    seg = fn(name)
    check(f"{name} gates negative_prompt",
          'body.get("negative_prompt")' in seg
          and "enforce_prompt_gate" in seg)

# prompt_help gates before dispatch
seg = fn("prompt_help")
check("prompt_help gates its text",
      'enforce_prompt_gate(\n            user, "prompt-help"' in seg
      or 'enforce_prompt_gate(user, "prompt-help"' in seg
      or '"prompt-help"' in seg and "enforce_prompt_gate" in seg)

# drama gate texts include wardrobe and role
seg = fn("create_drama")
check("create_drama gates wardrobe",
      'person.get("wardrobe")' in seg)
check("create_drama gates role", 'person.get("role")' in seg)

# suspension bars
seg = fn("create_character")
check("create_character crosses the suspension bar",
      "enforce_prompt_gate" in seg)
check("create_character stamps an input verdict",
      "classify_input_images" in seg and '"input_verdict"' in seg)
seg = fn("_editclone_new_job")
check("editclone creations cross the suspension bar",
      "enforce_prompt_gate" in seg)
seg = fn("process_editclone_job")
check("editclone character-from-frames stamps a verdict",
      "classify_input_images" in seg and '"input_verdict"' in seg)

# character consumption enforces the verdict
seg = fn("create_job_core")
check("character consumption re-judges a verdictless character",
      'character.get("input_verdict")' in seg
      and "input_gate_refusal" in seg
      and '"_char_verdict"' in seg)
seg = fn("create_api_job")
check("create_api_job stamps a flagged character verdict",
      'body.get("_char_verdict")' in seg
      and 'char_verdict.get("flagged")' in seg)

# train gates, ceiling, claim, persistence split, reconciler
seg = fn("train_character")
check("train checks verified", 'user["verified"]' in seg)
check("train checks past_due", 'user.get("past_due")' in seg)
check("train crosses the suspension bar",
      "enforce_prompt_gate" in seg)
check("train is pace limited", '_rate_limited("train"' in seg)
check("train consults the hard ceiling",
      "ceiling_allows(user, pricing.CHARACTER_TRAIN_RAW_CENTS)"
      in seg)
check("train claims under the account lock",
      "_user_lock(user" in seg
      and 'fresh_character.get("train_status") == "training"' in seg)
check("train keeps a recovery marker after provider accept",
      '"train_recovery"' in seg and '"train_request_id"' in seg)
# The refund path wraps only the submit: the persistence code
# (train_status_url assignment) sits AFTER the try/except.
post = seg.index('character["train_status_url"] = ')
refund = seg.index('user["balance_cents"] = int(user.get('
                   '"balance_cents", 0)) + charge')
check("train persists queue urls outside the refund path",
      post > refund)
seg = fn("reconcile_training_characters")
check("an eager training reconciler exists",
      'train_status") != "training"' in seg
      and "TRAINING_POLL_LIMIT_SECONDS" in seg)
check("the reconciler is scheduled",
      "def training_reconciler" in SRC
      and "modal.Period(hours=1)" in SRC)

# drama resume: shared checks, lifetime cap, driver bar
seg = fn("_drama_resume_checks")
check("resume checks verified/past_due/suspension",
      'user["verified"]' in seg and 'user.get("past_due")' in seg
      and "enforce_prompt_gate" in seg)
seg = fn("_drama_requeue_failed_shots")
check("requeue enforces the lifetime cap",
      "DRAMA_SHOT_LIFETIME_ATTEMPTS" in seg)
check("requeue never resets the lifetime counter",
      '["lifetime_attempts"] = 0' not in SRC
      and '"lifetime_attempts"] = 0' not in SRC)
seg = fn("resume_drama")
check("resume uses the shared checks and capped requeue",
      "_drama_resume_checks(user, request)" in seg
      and "_drama_requeue_failed_shots(rec)" in seg)
seg = fn("retry_job")
check("retry drama branch uses the shared checks",
      "_drama_resume_checks(user, request)" in seg
      and "_drama_requeue_failed_shots(rec)" in seg)
seg = fn("mirror_drama_status")
check("driver increments the lifetime counter",
      'shot["lifetime_attempts"]' in seg)
check("driver bars suspended accounts at dispatch",
      'user.get("suspended")' in seg)

print("check_gates: all pins hold")

"""Local checks for the Improve honesty fixes (Jack,
2026-11-21): a pick that sends the words back untouched is
reported as unchanged instead of as an update, and a very
short script is expanded into full narration lines instead
of polished. No Modal calls, no network: imports the
platform modules, stubs the provider boundary, exercises
the instruction routing and the unchanged comparison the
endpoint composes, and pins the endpoint and page wiring
in the source.

Run:  modal-venv python scratch/check_improve_honesty.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

for _key in ("SILK_API_KEY", "GEMINI_API_KEY",
             "OPENROUTER_API_KEY", "VENICE_API_KEY",
             "REFUSELESS_API_KEY", "MORPH_API_KEY"):
    os.environ.pop(_key, None)

import llm_providers  # noqa: E402
import shorts  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()

# --- the threshold ---------------------------------------------------------

check(shorts.SHORT_SCRIPT_EXPAND_WORDS == 40,
      "the short script expansion threshold is 40 words")

words39 = " ".join(["word"] * 39)
words40 = " ".join(["word"] * 40)
check("full narration script"
      in shorts.prompt_help_instruction(words39, "script"),
      "a 39 word script takes the expansion instruction")
check("Rewrite the narration script"
      in shorts.prompt_help_instruction(words40, "script"),
      "a 40 word script takes the polish instruction")

# --- the expansion instruction ----------------------------------------------

tiny = shorts.prompt_help_instruction("fat guy dancing", "script")
check("full narration script about the same subject" in tiny,
      "expansion asks for a full narration script on the subject")
check("one line for each scene" in tiny,
      "expansion keeps one line for each scene")
check("any facts given in it" in tiny,
      "expansion keeps any facts given")
check("reads well out loud" in tiny,
      "expansion asks for plain language that reads out loud")
check("under 1500 characters" in tiny,
      "expansion keeps the script well under the 2200 cap")
check("Rewrite the narration script" not in tiny,
      "expansion is not the polish instruction")
check(tiny.endswith("Script follows. fat guy dancing"),
      "expansion instruction carries the submitted script")

# --- the polish instruction, byte identical to before ------------------------

long_script = (
    "First line of a longer script that talks about the "
    "morning rush at the diner and the people who come in "
    "early for coffee and pie.\n"
    "Second line keeps going with more of the story so the "
    "whole script is long enough to count as a real script "
    "for polishing.")
expected_polish = (
    "Rewrite the narration script below so it reads better "
    "out loud. Keep the same facts in the same order, keep "
    "one line for each scene, and keep the line breaks. "
    "Make each line clear and easy to say. Return only the "
    "rewritten script, with no words before or after it "
    "and no quotation marks. Script follows. " + long_script)
check(shorts.prompt_help_instruction(long_script, "script")
      == expected_polish,
      "the polish instruction for a normal script is unchanged")

expected_video = (
    "Rewrite the draft below into one clear and detailed "
    "a video generation prompt. Keep the same subject and "
    "the same intent. Add concrete detail about the "
    "subject, the setting, the light, and how the camera "
    "moves. Return only the improved prompt, with no words "
    "before or after it and no quotation marks. "
    "Draft follows. a cat")
check(shorts.prompt_help_instruction("a cat", "video")
      == expected_video,
      "the video prompt instruction is unchanged")

# --- unchanged detection ------------------------------------------------------

check(shorts.help_unchanged(
    "First line.\n\nSecond line.", "First line.\nSecond line.",
    "script"),
    "a script answer with only blank lines dropped is unchanged")
check(shorts.help_unchanged(
    "First line.  \nSecond line.", "First line.\nSecond line.",
    "script"),
    "a script answer with line padding dropped is unchanged")
check(not shorts.help_unchanged(
    "First line.\nSecond line.", "First line.\nOther line.",
    "script"),
    "a script answer with new words is changed")
check(shorts.help_unchanged(
    "a  cat   naps", "a cat naps", "video"),
    "a prompt answer with whitespace folded is unchanged")
check(not shorts.help_unchanged(
    "a cat naps", "a dog naps", "video"),
    "a prompt answer with new words is changed")
check(not shorts.help_unchanged("a cat naps", "", "video"),
      "an empty answer is never unchanged")

# --- end to end through improve_prompt, provider stubbed ----------------------

real_provider_chat = llm_providers._provider_chat
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
try:
    seen = {}

    def echo_chat(prompt, provider_id, api_key, model):
        seen["prompt"] = prompt
        return prompt.split("Script follows. ", 1)[1]

    llm_providers._provider_chat = echo_chat
    out = shorts.improve_prompt(long_script, None, "script")
    check(shorts.help_unchanged(long_script, out, "script"),
          "an echoed script reads as unchanged end to end")

    def expand_chat(prompt, provider_id, api_key, model):
        seen["prompt"] = prompt
        return ("A big guy steps onto the dance floor.\n"
                "The music starts and he starts moving.")

    llm_providers._provider_chat = expand_chat
    out = shorts.improve_prompt("fat guy dancing", None, "script")
    check("full narration script" in seen["prompt"],
          "a tiny script sends the expansion instruction")
    check(out.startswith("A big guy steps onto the dance floor."),
          "the expanded script comes back as the help answer")
    check(not shorts.help_unchanged("fat guy dancing", out, "script"),
          "an expanded script reads as changed")
finally:
    llm_providers._provider_chat = real_provider_chat
    os.environ.pop("OPENROUTER_API_KEY", None)

# --- endpoint and page wiring -------------------------------------------------

check('"unchanged": shorts.help_unchanged(' in SRC,
      "the prompt help endpoint returns the unchanged flag")
check("shorts.improve_prompt(text, draft_model, kind)" in SRC,
      "the endpoint still improves through shorts.improve_prompt")

fn = SRC.split("async function promptHelp(", 1)[1]
fn = fn.split("$('prompt-help-btn')", 1)[0]
check("if (r.unchanged)" in fn,
      "the page branches on the unchanged flag")
check(fn.index("if (r.unchanged)") < fn.index("ta.value = r.text"),
      "the unchanged branch runs before the field is replaced")
check("back unchanged. Try another model or add more to your "
      in fn,
      "the page carries the honest unchanged message")
check("That model sent your " in fn
      and " first." in fn,
      "the honest message names the field both times")
for frag in ("That model sent your ",
             " back unchanged. Try another model or add more "
             "to your ",
             " first."):
    check(":" not in frag and ";" not in frag,
          "the honest message copy follows the plain copy rules")
check("is updated. Read it over and change anything you like."
      in fn,
      "the success line still shows when the text did change")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_improve_honesty")

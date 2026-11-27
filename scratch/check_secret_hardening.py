"""Pins for the needs-validation hardening (2026-11-27):
require_admin fails closed with no ADMIN_KEY configured;
_session_secret refuses empty; editclone_probe mounts no
secret bundle (it reads none)."""
import ast
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
    raise AssertionError(name)


def check(label, cond):
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok: {label}")


seg = fn("require_admin")
check("require_admin has no NUL sentinel default",
      '"\\0"' not in seg and "not expected" in seg)
seg = fn("_session_secret")
check("_session_secret raises when unconfigured",
      "raise RuntimeError" in seg)
probe = fn("editclone_probe")
head = SRC.split("def editclone_probe")[0].rsplit(
    "@app.function(", 1)[1].split(")")[0]
check("editclone_probe mounts no secrets", "Secret" not in head)
check("every issued cookie carries the Secure attribute",
      SRC.count('samesite="lax")') == 0
      and SRC.count("secure=True") >= 8)
print("check_secret_hardening: all pins hold")

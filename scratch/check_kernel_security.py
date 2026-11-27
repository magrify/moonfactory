"""Local checks for the worker-side kernel security fixes.

Pins the shape of three fixes without any network, Kaggle or
Modal call:

- per-family worker keys: each Kaggle kernel reads its own
  UserSecrets name first (MOONFACTORY_HF_KEY / _VOICE_KEY /
  _AUDIO_KEY / _COMFY_KEY), falling back to the shared
  MOONFACTORY_TEMPLATE_KEY and then the dataset key file;
- per-job claim tokens: each kernel sends X-Claim-Bind: 1 on
  its claim request and X-Claim-Token (through a job_headers
  helper fed from the job payload) on its per-job calls, so a
  job claimed without a token still works key-only;
- no floating acquisitions in the worker files: every git
  clone is followed in the same setup flow by a checkout of
  a 40-hex commit, every pip install spec carries an exact
  == pin, comfy-cli's ComfyUI install carries --commit, and
  no unversioned curl|sh uv installer remains.

Files covered: the four Kaggle kernels (the comfy template
AND its generated kernel, which must carry the same fixes),
modal-worker/app.py and modal-worker/audio_app.py. The
comfy model manifest (hash-verified) and CosyVoice (already
commit-pinned before this pass) are out of scope by design;
requirements files installed from cloned repos are pinned
transitively by the clone commits.

Run:  python3 scratch/check_kernel_security.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

KERNELS = {
    "worker-kaggle/hf_worker_kernel.py": "MOONFACTORY_HF_KEY",
    "worker-kaggle/voice_worker_kernel.py": "MOONFACTORY_VOICE_KEY",
    "worker-kaggle/audio_worker_kernel.py": "MOONFACTORY_AUDIO_KEY",
    "worker-kaggle/comfy_kernel_template.py": "MOONFACTORY_COMFY_KEY",
    "worker-kaggle/comfy_worker_kernel.py": "MOONFACTORY_COMFY_KEY",
}
# Each family reads its key from its OWN key dataset
# (2026-11-27 rotation completion); no kernel may read
# another family's dataset.
KEY_DATASETS = {
    "worker-kaggle/hf_worker_kernel.py": "moonfactory-hf-key",
    "worker-kaggle/voice_worker_kernel.py": "moonfactory-voice-key",
    "worker-kaggle/audio_worker_kernel.py": "moonfactory-audio-key",
    "worker-kaggle/comfy_kernel_template.py": "moonfactory-comfy-key",
    "worker-kaggle/comfy_worker_kernel.py": "moonfactory-comfy-key",
}
FILES = list(KERNELS) + ["modal-worker/app.py",
                         "modal-worker/audio_app.py"]
SOURCES = {rel: (ROOT / rel).read_text() for rel in FILES}
SHA40 = re.compile(r"[0-9a-f]{40}")

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# --- per-family keys and claim tokens, per kernel -------------------------
for rel, family_secret in KERNELS.items():
    src = SOURCES[rel]
    check(f'get_secret("{family_secret}")' in src
          or f'"{family_secret}"' in src,
          f"{rel} reads its family secret {family_secret}")
    check('"MOONFACTORY_TEMPLATE_KEY"' in src,
          f"{rel} keeps the shared MOONFACTORY_TEMPLATE_KEY fallback")
    check(KEY_DATASETS[rel] in src and "worker_key.txt" in src,
          f"{rel} keeps its own family dataset key file fallback")
    for other_rel, other_ds in KEY_DATASETS.items():
        if other_ds != KEY_DATASETS[rel]:
            check(other_ds not in src,
                  f"{rel} must not read another family's key "
                  f"dataset ({other_ds})")
    check('"X-Claim-Bind": "1"' in src,
          f"{rel} sends X-Claim-Bind: 1 on its claim request")
    check('"X-Claim-Token"' in src,
          f"{rel} sends X-Claim-Token on per-job calls")
    check('job.get("claim_token")' in src,
          f"{rel} captures claim_token from the claimed job payload")
    check("def job_headers(claim_token=None)" in src
          and "if claim_token:" in src,
          f"{rel} routes per-job headers through job_headers, "
          "which omits the token header when no token was issued")

# The generated comfy kernel must not drift from its template
# on any of the security surface.
template = SOURCES["worker-kaggle/comfy_kernel_template.py"]
generated = SOURCES["worker-kaggle/comfy_worker_kernel.py"]
for needle in ("MOONFACTORY_COMFY_KEY", "MOONFACTORY_TEMPLATE_KEY",
               "X-Claim-Bind", "X-Claim-Token", "claim_token"):
    check(needle in template and needle in generated,
          f"comfy template and generated kernel both carry {needle}")

# --- no unpinned git clone --------------------------------------------------
for rel, src in SOURCES.items():
    for m in re.finditer(r"git.{0,12}clone", src):
        window = src[m.start():m.start() + 1500]
        pinned = re.search(r"checkout[^0-9a-f]{0,40}[0-9a-f]{40}",
                           window)
        check(bool(pinned),
              f"{rel}: git clone at offset {m.start()} is followed "
              "by a checkout of a 40-hex commit in the same flow")
    check("astral.sh/uv/install.sh" not in src,
          f"{rel}: no unversioned curl|sh uv installer remains")

# ComfyUI itself in the Modal image arrives through comfy-cli:
# its install must carry the pinned commit, and comfy-cli
# itself an exact version.
modal_app = SOURCES["modal-worker/app.py"]
check(re.search(r"comfy --skip-prompt install --nvidia --commit "
                r"[0-9a-f]{40}", modal_app) is not None,
      "modal-worker/app.py pins ComfyUI via comfy install --commit <sha>")
check('"comfy-cli==1.29.0"' in modal_app
      or "comfy-cli==1.29.0" in modal_app,
      "modal-worker/app.py pins comfy-cli to an exact version")
check("https://astral.sh/uv/0.13.10/install.sh"
      in SOURCES["modal-worker/audio_app.py"],
      "modal-worker/audio_app.py uses the versioned uv installer URL")
check('"uv==0.13.10"' in SOURCES["worker-kaggle/audio_worker_kernel.py"],
      "the audio kernel installs uv at a pinned version via pip")

# --- no bare pip install ----------------------------------------------------
PKG_SPEC = re.compile(r"^[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?$")

for rel, src in SOURCES.items():
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # .pip_install("pkg") / .pip_install("a", "b") call sites
        if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute) and node.func.attr == "pip_install":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(
                        arg.value, str):
                    check("==" in arg.value,
                          f"{rel}: pip_install spec {arg.value!r} "
                          "carries an exact == pin")
        # pip_install([...]) / pip_install_checked / uv_pip list arguments
        if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Name) and node.func.id in (
                "pip_install", "pip_install_checked", "uv_pip"):
            for arg in node.args:
                if isinstance(arg, (ast.List, ast.Tuple)):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str):
                            check("==" in elt.value or "@" in elt.value,
                                  f"{rel}: install spec {elt.value!r} "
                                  "carries an exact pin")
        # Any all-constant list/tuple of package specs that mixes
        # pinned and unpinned entries is a partial pin.
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in node.elts):
            values = [e.value for e in node.elts]
            if any("==" in v for v in values) and all(
                    PKG_SPEC.match(v.split("==")[0].split(">=")[0]
                                   .split("<")[0]) and " " not in v
                    for v in values):
                for v in values:
                    check("==" in v or "@" in v,
                          f"{rel}: package spec {v!r} in a pinned "
                          "list carries an exact pin")
    # Shell-form "pip install a b c" command strings.
    for m in re.finditer(r"[\"']pip install ([^\"']+)[\"']", src):
        if "{" in m.group(1) or "->" in m.group(1):
            continue  # an f-string log line naming an install
        for token in m.group(1).split():
            if token in ("||", "true", "-q", "-r") or token.endswith(
                    ".txt") or token.startswith("-"):
                continue
            check("==" in token,
                  f"{rel}: shell pip install token {token!r} "
                  "carries an exact == pin")

# Named spot checks for the installs the audit called out.
spot = [
    ("worker-kaggle/voice_worker_kernel.py", "modelscope==1.40.3"),
    ("worker-kaggle/voice_worker_kernel.py", "faster-whisper==1.2.1"),
    ("worker-kaggle/voice_worker_kernel.py", "x-transformers==2.32.4"),
    ("worker-kaggle/audio_worker_kernel.py", "kokoro-onnx==0.6.1"),
    ("worker-kaggle/audio_worker_kernel.py", "kokoro==0.9.4"),
    ("worker-kaggle/audio_worker_kernel.py", "av==18.1.1"),
    ("worker-kaggle/comfy_kernel_template.py", "imageio-ffmpeg==0.8.1"),
    ("worker-kaggle/comfy_worker_kernel.py", "imageio-ffmpeg==0.8.1"),
    ("modal-worker/app.py", "huggingface_hub==2.5.0"),
    ("modal-worker/app.py", "fastapi[standard]==0.165.0"),
    ("modal-worker/app.py", "gguf==0.19.0"),
    ("modal-worker/audio_app.py", "kokoro==0.9.4"),
    ("modal-worker/audio_app.py", "av==18.1.1"),
]
for rel, needle in spot:
    check(needle in SOURCES[rel], f"{rel} pins {needle}")
for rel in FILES:
    src = SOURCES[rel]
    check("kokoro>=0.9.2" not in src and '"av<19"' not in src
          and '"torch"],' not in src,
          f"{rel}: no range spec survives among the audited installs")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_kernel_security: all checks passed")

"""Local checks for the 2026-11-24 audio move.

Jack's order: Modal is almost maxed out, so the platform's
self-contained audio stages move to Kaggle like the house
lanes did. The ACE-Step music bed stage (per-job custom
beds and the cached named-style beds) and the Kokoro
voiceover stage now run on the Kaggle audio worker; the
word check and the transcription stage stay on the Modal
audio app (both run inside the delivery path against the
finished audio). This suite pins the move's shape without
any network, Modal, or Kaggle call:

- every bed and voiceover call site routes through the
  _audio_bytes seam and no generate_bed / generate_voiceover
  callable is invoked from the platform anymore, while the
  word check and transcribe calls stay;
- the callers' fallbacks are untouched (the house bed with
  music_fallback / audio_fallback, the voiceover
  "unavailable" flag);
- the seam itself behaves, exercised for real with fakes: a
  finished child is rejoined with no kernel wake, a queued
  child is waited on and collected, a failed child raises
  its recorded error, and a wait that runs out closes the
  child and raises;
- child ids are deterministic in the stage inputs, keyed by
  the same (caption, seed) and (voice, text) hashes the
  Modal audio app cached by, so a repeat request costs a
  file read, never a second render;
- the worker endpoints mirror the other workers' claim
  shape behind the same worker key, the wake rides
  kaggle_push with its own slug and meta keys, and the
  kernel ships in both Modal images;
- the kernel parses and carries the Modal stages' own
  recipes (the ACE-Step turbo bed parameters, the Kokoro
  24 kHz voiceover), and the Modal audio app still defines
  its callables as the uncalled revert path.

Run:  modal-venv python scratch/check_audio_kaggle.py
"""
import ast
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLATFORM = (ROOT / "platform" / "app.py").read_text()
AUDIO_APP = (ROOT / "modal-worker" / "audio_app.py").read_text()
KERNEL_PATH = ROOT / "worker-kaggle" / "audio_worker_kernel.py"
KERNEL = KERNEL_PATH.read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


PTREE = ast.parse(PLATFORM)


def fn_source(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(PLATFORM, node)
    raise AssertionError(f"{name} not found")


# --- routing -------------------------------------------------------------

check('"sinclip-audio", "generate_bed"' not in PLATFORM
      and '"sinclip-audio", "generate_voiceover"' not in PLATFORM,
      "a bed or voiceover callable is still invoked from the platform")
check('"sinclip-audio", "word_check"' in PLATFORM,
      "the word check no longer rides the Modal audio app")
check('"sinclip-audio", "transcribe"' in PLATFORM,
      "the transcription stage no longer rides the Modal audio app")

for fn_name, stage in (("_short_custom_bed_for_job", "bed"),
                       ("_short_bed_for_job", "bed"),
                       ("_bed_for_job", "bed"),
                       ("_voiceover_for_job", "voiceover")):
    body = fn_source(PTREE, fn_name)
    check("_audio_bytes" in body and f'"{stage}"' in body,
          f"{fn_name} does not route through the audio seam")

processor = PLATFORM.split("def process_short_job", 1)[1].split(
    "@app.function", 1)[0]
check('"voiceover", {"text": line, "voice": voice}' in processor,
      "the shorts narration loop does not route through the audio seam")
check("_queue_short_bed_child(job)" in processor,
      "the shorts runner does not queue its bed at the start of the run")
check('job["music_fallback"] = True'
      in fn_source(PTREE, "_short_bed_for_job")
      and 'job["music_fallback"] = True'
      in fn_source(PTREE, "_short_custom_bed_for_job"),
      "a short bed failure lost its house bed fallback flag")
check('job["audio_fallback"] = True' in fn_source(PTREE, "_bed_for_job"),
      "a video bed failure lost its house bed fallback flag")
check('job["voiceover_status"] = "unavailable"'
      in fn_source(PTREE, "_voiceover_for_job"),
      "a voiceover failure lost its unavailable flag")

comfy_file = fn_source(PTREE, "comfy_job_file")
check('job.get("music_prompt")' in comfy_file
      and "_queue_audio_child" in comfy_file
      and "_bed_payload_for_job" in comfy_file,
      "a finished house render does not queue its custom bed early")

# --- the seam's shape ------------------------------------------------------

check('AUDIO_KERNEL_SLUG = "magrify/moonfactory-audio-worker"' in PLATFORM,
      "the audio kernel slug is not pinned")
wake = fn_source(PTREE, "_wake_audio_kernel")
check("kaggle_push" in wake and "AUDIO_KERNEL_SLUG" in wake
      and "HF_KEY_DATASET" in wake and "gpu=True" in wake,
      "the audio wake does not ride kaggle_push like the other workers")
check('meta.get("audio:heartbeat")' in PLATFORM
      and 'meta["audio:heartbeat"]' in PLATFORM,
      "the audio heartbeat meta keys are missing")
check(PLATFORM.count('/ "worker-kaggle" / "audio_worker_kernel.py"') == 2,
      "the audio kernel does not ship in both Modal images")
for route in ('"/api/worker/audio/heartbeat"',
              '"/api/worker/audio/jobs/next"',
              '"/api/worker/audio/jobs/{job_id}/file"',
              '"/api/worker/audio/jobs/{job_id}/fail"'):
    check(route in PLATFORM, f"the audio worker route {route} is missing")
check("def _audio_worker_ok" in PLATFORM
      and "return _hf_worker_ok(x_api_key)" in PLATFORM,
      "the audio endpoints do not ride the shared worker key")

# --- the seam's behavior, exercised with fakes -----------------------------

TMP = Path(tempfile.mkdtemp(prefix="audio-kaggle-check-"))


class FakeVolume:
    def reload(self):
        pass

    def commit(self):
        pass


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.on_sleep = None

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.on_sleep:
            self.on_sleep()


def fresh_namespace():
    jobs = {}
    clock = FakeClock()
    wakes = []
    ns = {
        "hashlib": hashlib,
        "Path": Path,
        "time": clock,
        "jobs": jobs,
        "meta": {},
        "data_volume": FakeVolume(),
        "FILES_DIR": TMP,
        "_now": lambda: 1_700_000_000.0,
        "_wake_audio_kernel": lambda: wakes.append(1),
        "SHORTS_CUSTOM_MUSIC": "custom",
        "SHORTS_MUSIC_STYLES": {
            "house": {"label": "Dreamy ambient", "caption": "",
                      "bpm": 96},
            "calm": {"label": "Calm piano", "caption": "calm piano",
                     "bpm": 80},
        },
    }
    for name in ("_audio_key", "_audio_child_id", "_audio_child_path",
                 "_queue_audio_child", "_audio_bytes",
                 "_bed_payload_for_job", "_queue_short_bed_child"):
        exec(fn_source(PTREE, name), ns)
    return ns, jobs, clock, wakes


PARENT = {"id": "parentjob1", "user_id": "user1"}
BED = {"caption": "lofi piano, instrumental, no vocals", "seed": 7}

# Deterministic ids in the stage inputs, on the Modal app's own keys.
ns, jobs, clock, wakes = fresh_namespace()
bed_key = hashlib.sha256(
    f"{BED['caption']}|{BED['seed']}".encode()).hexdigest()[:16]
check(ns["_audio_key"]("bed", BED) == bed_key,
      "the bed key is not the (caption, seed) hash the Modal app cached by")
check('f"{caption}|{seed}"' in AUDIO_APP
      or 'f"{caption}|{seed}"'.replace("caption", "caption") in AUDIO_APP,
      "the Modal audio app's bed key formula moved")
voice_key = hashlib.sha256("af_heart|hello world".encode()).hexdigest()[:16]
check(ns["_audio_key"]("voiceover",
                      {"text": "hello world", "voice": "af_heart"})
      == voice_key,
      "the voiceover key is not the (voice, text) hash the Modal app used")
child_id = ns["_audio_child_id"]("bed", bed_key)
check(child_id == f"aud-bed-{bed_key}", "the bed child id is not stable")

# A finished child is rejoined with no queue and no wake.
ns, jobs, clock, wakes = fresh_namespace()
path = ns["_audio_child_path"](child_id)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(b"RIFF-done")
jobs[child_id] = {"id": child_id, "kind": "audio_stage",
                  "status": "done", "created_at": 1.0}
check(ns["_audio_bytes"]("bed", BED, PARENT, 100) == b"RIFF-done",
      "a finished bed child was not rejoined")
check(not wakes, "rejoining a finished child woke the kernel")

# A queued child is waited on and collected when the kernel lands it.
ns, jobs, clock, wakes = fresh_namespace()

def land_bed():
    rec = jobs[child_id]
    rec["status"] = "done"
    p = ns["_audio_child_path"](child_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF-made")

clock.on_sleep = land_bed
check(ns["_audio_bytes"]("bed", BED, PARENT, 100) == b"RIFF-made",
      "a waited bed child was not collected")
rec = jobs[child_id]
check(rec.get("kind") == "audio_stage"
      and rec.get("provider") == "kaggle-audio"
      and rec.get("audio_stage") == "bed"
      and rec.get("caption") == BED["caption"]
      and rec.get("seed") == BED["seed"],
      "the queued bed child does not carry its stage inputs")
check(bool(wakes), "queueing a bed child did not wake the kernel")

# A failed child raises its recorded error.
ns, jobs, clock, wakes = fresh_namespace()

def fail_bed():
    rec = jobs[child_id]
    rec["status"] = "error"
    rec["internal_error"] = "ace exploded"

clock.on_sleep = fail_bed
try:
    ns["_audio_bytes"]("bed", BED, PARENT, 100)
    check(False, "a failed bed child did not raise")
except RuntimeError as exc:
    check("ace exploded" in str(exc),
          "a failed bed child raised without its recorded error")

# A wait that runs out closes the child and raises.
ns, jobs, clock, wakes = fresh_namespace()
try:
    ns["_audio_bytes"]("bed", BED, PARENT, 30)
    check(False, "an unmade bed did not raise at the wait bound")
except RuntimeError as exc:
    check("wait ended first" in str(exc),
          "the wait bound raised the wrong error")
check(jobs[child_id].get("status") == "error",
      "the wait bound did not close the child")

# Queueing is idempotent while a child is open, and a failed
# child is queued fresh by the next request.
ns, jobs, clock, wakes = fresh_namespace()
first = ns["_queue_audio_child"]("bed", BED, PARENT)
again = ns["_queue_audio_child"]("bed", BED, PARENT)
check(first["id"] == again["id"] == child_id
      and again["created_at"] == first["created_at"],
      "queueing an open child made a second child")
jobs[child_id]["status"] = "error"
fresh = ns["_queue_audio_child"]("bed", BED, PARENT)
check(fresh["status"] == "queued",
      "a failed child was not queued fresh on the next request")

# The bed payload derivation matches the finishing chain's own.
ns, jobs, clock, wakes = fresh_namespace()
job = {"id": "jobxyz", "music_prompt": "dark synthwave"}
payload = ns["_bed_payload_for_job"](job)
want_seed = int(hashlib.sha256(b"jobxyz").hexdigest()[:8], 16) % 90000 + 10000
check(payload == {"caption": "dark synthwave, instrumental, no vocals",
                  "seed": want_seed},
      "the bed payload derivation moved")
check(ns["_bed_payload_for_job"]({"id": "jobxyz"}) is None,
      "a job with no music prompt made a bed payload")

# The shorts early queue mirrors the bed routing.
ns, jobs, clock, wakes = fresh_namespace()
ns["_queue_short_bed_child"]({"id": "s1", "user_id": "u",
                              "short_music": "none"})
check(not jobs, "a No music short queued a bed")
ns["_queue_short_bed_child"]({"id": "s2", "user_id": "u",
                              "short_music": "house"})
check(not jobs, "a house bed short queued a bed")
ns["_queue_short_bed_child"]({"id": "s3", "user_id": "u",
                              "short_music": "calm"})
calm_key = hashlib.sha256(
    "calm piano, instrumental, no vocals|7".encode()).hexdigest()[:16]
check(f"aud-bed-{calm_key}" in jobs,
      "a named style short did not queue its style bed")
jobs.clear()
ns["_queue_short_bed_child"]({
    "id": "s4", "user_id": "u", "short_music": "custom",
    "music_prompt": "warm ukulele"})
custom = ns["_bed_payload_for_job"](
    {"id": "s4", "music_prompt": "warm ukulele"})
custom_key = ns["_audio_key"]("bed", custom)
check(f"aud-bed-{custom_key}" in jobs,
      "a custom music short did not queue its per job bed")

# --- the kernel --------------------------------------------------------------

ast.parse(KERNEL)
check("acestep-v15-turbo" in KERNEL and "ACE-Step-1.5" in KERNEL,
      "the kernel does not carry the ACE-Step turbo recipe")
for token in ("duration=10", "inference_steps=8", "bpm=92",
              '"[Instrumental]"', "instrumental=True"):
    check(token in KERNEL, f"the kernel bed recipe lost {token}")
check("KPipeline" in KERNEL and "24000" in KERNEL,
      "the kernel does not carry the Kokoro voiceover recipe")
for route in ("/api/worker/audio/heartbeat", "/api/worker/audio/jobs/next",
              "/api/worker/audio/jobs/", "/fail"):
    check(route in KERNEL, f"the kernel does not call {route}")
check("IDLE_LIMIT_SECONDS = 600" in KERNEL
      and "SESSION_CAP_SECONDS" in KERNEL,
      "the kernel lost its idle shutdown or session cap")
check("moonfactory-hf-key" in KERNEL and "worker_key.txt" in KERNEL,
      "the kernel does not read the shared worker key dataset")

# --- the revert path ---------------------------------------------------------

check("def generate_bed(" in AUDIO_APP
      and "def generate_voiceover(" in AUDIO_APP,
      "the Modal audio callables are gone, so the revert path is lost")
check("def word_check(" in AUDIO_APP and "def transcribe(" in AUDIO_APP,
      "the Modal audio app lost the stages that stay on it")

if failures:
    print(f"FAIL ({len(failures)}):")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("check_audio_kaggle: all checks passed")

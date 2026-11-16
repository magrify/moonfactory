"""
Sinclip audio stage on Modal: ACE-Step 1.5 music bed generation.

Standalone app ("sinclip-audio") so the music stage can iterate without
touching the ComfyUI worker. Uses ACE-Step 1.5 in DiT-only mode
(acestep-v15-turbo, no language model): for an instrumental bed we supply
caption + metadata ourselves, so the LM planner is unnecessary weight.

The project is installed at image build time with uv (its documented
install route); generation runs through the project's own venv python as
a subprocess so dependency quirks stay inside the project's environment.

generate_bed(caption, seed) is the callable the Moonfactory platform
uses for per-job custom audio (a Creator-plan feature): pass a music
description, get back wav bytes. Called with no arguments it produces
the house bed, exactly as before. Every (caption, seed) pair is cached
in the Volume, so a repeat call costs a container start, not a render.

Usage:
  modal run audio_app.py             # generate the house bed, save locally
  modal run audio_app.py --caption "lofi piano, soft rain" --seed 42
"""

import hashlib
import json
import subprocess
from pathlib import Path

import modal

app = modal.App("sinclip-audio")

VOLUME_NAME = "sinclip-models"
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

ACE_ROOT = "/opt/acestep"

ace_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "curl")
    .run_commands(
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5.git " + ACE_ROOT,
        "cd " + ACE_ROOT + " && $HOME/.local/bin/uv sync",
    )
)

HOUSE_CAPTION = (
    "gentle uplifting instrumental background music, soft piano melody, "
    "warm strings, light airy pads, calm and dreamy, floating over "
    "green hills under a bright sky, cinematic ambient bed, no vocals"
)
HOUSE_SEED = 12345

GEN_SCRIPT = r'''
import json, sys, traceback
sys.path.insert(0, "/opt/acestep")

from acestep.handler import AceStepHandler
from acestep.inference import GenerationParams, GenerationConfig, generate_music

with open("/tmp/ace_params.json") as fh:
    spec = json.load(fh)
caption = spec["caption"]
seed = int(spec["seed"])

handler = AceStepHandler()
handler.initialize_service(
    project_root="/opt/acestep",
    config_path="acestep-v15-turbo",
    device="cuda",
)

params = GenerationParams(
    caption=caption,
    lyrics="[Instrumental]",
    instrumental=True,
    bpm=92,
    keyscale="C Major",
    timesignature="4",
    duration=10,
    inference_steps=8,
    shift=3.0,
    seed=seed,
    thinking=False,
    use_cot_metas=False,
    use_cot_caption=False,
    use_cot_lyrics=False,
    use_cot_language=False,
)
config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[seed])

try:
    result = generate_music(handler, None, params, config, save_dir="/tmp/ace_out")
except Exception:
    traceback.print_exc()
    raise

if not result.success:
    raise RuntimeError(f"ACE-Step generation failed: {result.error}")
path = result.audios[0]["path"]
print(f"GENERATED {path}", flush=True)
'''


def _bed_key(caption: str, seed: int) -> str:
    return hashlib.sha256(f"{caption}|{seed}".encode()).hexdigest()[:16]


@app.function(
    image=ace_image,
    gpu="A10G",
    volumes={"/sinclip-models": volume},
    timeout=1800,
)
def generate_bed(caption: str = HOUSE_CAPTION, seed: int = HOUSE_SEED) -> bytes:
    """Generate (or fetch from the Volume cache) a 10 s instrumental
    bed for the given caption. The house bed keeps its original path;
    custom beds are cached under audio/custom/ by caption+seed hash."""
    is_house = caption == HOUSE_CAPTION and seed == HOUSE_SEED
    dest_dir = Path("/sinclip-models/audio")
    dest = (dest_dir / "music_bed_first.wav") if is_house else (
        dest_dir / "custom" / f"{_bed_key(caption, seed)}.wav"
    )
    if dest.exists():
        print(f"cache hit {dest}", flush=True)
        return dest.read_bytes()
    Path("/tmp/ace_params.json").write_text(
        json.dumps({"caption": caption, "seed": seed})
    )
    script_path = Path("/tmp/gen_bed.py")
    script_path.write_text(GEN_SCRIPT)
    proc = subprocess.run(
        [f"{ACE_ROOT}/.venv/bin/python", str(script_path)],
        capture_output=True, text=True, timeout=1500,
    )
    print(proc.stdout[-6000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-6000:], flush=True)
        raise RuntimeError(f"ACE-Step subprocess failed rc={proc.returncode}")
    out_dir = Path("/tmp/ace_out")
    wavs = sorted(out_dir.rglob("*.wav"))
    if not wavs:
        raise RuntimeError(f"no wav produced; /tmp/ace_out contains: {list(out_dir.rglob('*'))}")
    data = wavs[0].read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    volume.commit()
    print(f"persisted {dest} ({len(data)} bytes)", flush=True)
    return data


@app.local_entrypoint()
def main(caption: str = "", seed: int = 0):
    if caption:
        use_seed = seed or 12345
        data = generate_bed.remote(caption, use_seed)
        out = Path(__file__).parent.parent / "outputs" / "custom_bed_test.wav"
    else:
        data = generate_bed.remote()
        out = Path(__file__).parent.parent / "outputs" / "music_bed_first.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"saved {out} ({len(data)} bytes)")

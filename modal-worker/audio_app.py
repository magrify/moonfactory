"""
Sinclip audio stage on Modal: ACE-Step 1.5 music bed generation.

Standalone app ("sinclip-audio") so the music stage can iterate without
touching the ComfyUI worker. Uses ACE-Step 1.5 in DiT-only mode
(acestep-v15-turbo, no language model): for an instrumental bed we supply
caption + metadata ourselves, so the LM planner is unnecessary weight.

The project is installed at image build time with uv (its documented
install route); generation runs through the project's own venv python as
a subprocess so dependency quirks stay inside the project's environment.

Usage:
  modal run audio_app.py             # generate the bed, save wav locally
"""

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

GEN_SCRIPT = r'''
import sys, traceback
sys.path.insert(0, "/opt/acestep")

from acestep.handler import AceStepHandler
from acestep.inference import GenerationParams, GenerationConfig, generate_music

handler = AceStepHandler()
handler.initialize_service(
    project_root="/opt/acestep",
    config_path="acestep-v15-turbo",
    device="cuda",
)

params = GenerationParams(
    caption=(
        "gentle uplifting instrumental background music, soft piano melody, "
        "warm strings, light airy pads, calm and dreamy, floating over "
        "green hills under a bright sky, cinematic ambient bed, no vocals"
    ),
    lyrics="[Instrumental]",
    instrumental=True,
    bpm=92,
    keyscale="C Major",
    timesignature="4",
    duration=10,
    inference_steps=8,
    shift=3.0,
    seed=12345,
    thinking=False,
    use_cot_metas=False,
    use_cot_caption=False,
    use_cot_lyrics=False,
    use_cot_language=False,
)
config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[12345])

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


@app.function(
    image=ace_image,
    gpu="A10G",
    volumes={"/sinclip-models": volume},
    timeout=1800,
)
def generate_bed() -> bytes:
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
    dest_dir = Path("/sinclip-models/audio")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "music_bed_first.wav"
    dest.write_bytes(data)
    volume.commit()
    print(f"persisted {dest} ({len(data)} bytes)", flush=True)
    return data


@app.local_entrypoint()
def main():
    data = generate_bed.remote()
    out = Path(__file__).parent.parent / "outputs" / "music_bed_first.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"saved {out} ({len(data)} bytes)")

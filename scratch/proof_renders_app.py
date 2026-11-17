"""Approved proof renders for Moonfactory (2026-11-16).

MODAL SPEND: APPROVED BY JACK IN CHAT, Nov 16, 2026, verbatim:
"Approve all 5 proof renders (~$1.42 total)". This harness runs
EXACTLY the five approved jobs and nothing else:

  2a owner Production (Wan 2.2 house lane), balloon start image,
     speech_script + music prompt  (~$0.17)
  2b owner Draft, same audio payload  (~$0.12)
  3a Fun Camera Pan Left, distilled recipe, speed 3.0  (~$0.10)
  3b Fun Camera Pan Left, mid recipe, speed 1.0  (~$0.25)
  3c Fun Camera Pan Left, stock recipe, speed 3.0  (~$0.78)

2a/2b go through the platform API on the owner account (reused,
never created). 3a/3b/3c go direct to the worker endpoint because
the platform API does not forward camera_recipe. Benchmark scene,
negative, seed 12345 and the balloon start image match BUILD-LOG
runs 18/21 so the camera numbers are comparable.

The harness functions are CPU-only; GPU spend happens inside the
five jobs themselves. State lives in a Modal Dict so every phase
is idempotent and resumable.

Usage:
  modal run proof_renders_app.py --action submit-audio
  modal run proof_renders_app.py --action submit-camera-early
  modal run proof_renders_app.py --action status
  modal run proof_renders_app.py --action submit-stock   (after guard check)
  modal run proof_renders_app.py --action collect
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-proof-renders")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests", "numpy", "pillow")
    .add_local_file(
        str(Path(__file__).parent.parent / "modal-worker" / "test_start_720.png"),
        "/assets/test_start_720.png",
    )
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
wjobs = modal.Dict.from_name("sinclip-jobs", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-proof-state", create_if_missing=True)
volume = modal.Volume.from_name("sinclip-models", create_if_missing=False)

OWNER_EMAIL = "support@magrify.com"
WORKER_BASE = "https://magrify--sinclip-comfyui-api.us-east.modal.run"
L40S_USD_PER_SEC = 0.000542  # Modal list rate, same basis as runs 18/21

SCENE = ("a red hot air balloon rising over green hills, "
         "white clouds drifting, bright daylight")
NEGATIVE = "blurry, distorted face, extra fingers, morphing, flicker, text, watermark"
SEED = 12345
SPEECH = "A red balloon drifts over the quiet harbor."
MUSIC = "gentle ambient piano and soft strings, calm dreamy mood, slow tempo"

ESTIMATES = {"2a": 0.17, "2b": 0.12, "3a": 0.10, "3b": 0.25, "3c": 0.78}


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


def _owner_session(base):
    import requests
    rec = users.get(f"email:{OWNER_EMAIL}")
    assert rec, "owner record missing"
    s = requests.Session()
    s.cookies.set("mf_session",
                  make_session(rec["id"], os.environ["SESSION_SECRET"]))
    return s, rec


def _bot_fields(sess, base):
    c = sess.get(f"{base}/api/challenge", timeout=30).json()
    return {
        "challenge_id": c["challenge_id"],
        "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
    }


def _start_b64():
    return base64.b64encode(
        Path("/assets/test_start_720.png").read_bytes()).decode()


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def submit_audio(base: str) -> dict:
    """Jobs 2a (Production) + 2b (Draft) on the owner account."""
    import requests

    base = base.rstrip("/")
    out = {}
    if state.get("2a") and state.get("2b"):
        return {"already_submitted": True,
                "2a": state["2a"], "2b": state["2b"]}
    sess, owner = _owner_session(base)
    state["owner_before"] = {k: owner.get(k) for k in (
        "plan", "net_paid_cents", "balance_cents", "free_jobs_total",
        "cost_settled_cents", "is_owner")}
    img = _start_b64()
    for label, lane in (("2a", "production"), ("2b", "draft")):
        if state.get(label):
            out[label] = state[label]
            continue
        r = sess.post(f"{base}/api/jobs", timeout=60, json={
            "lane": lane,
            "prompt": SCENE,
            "seed": SEED,
            "start_image_png_b64": img,
            "speech_script": SPEECH,
            "music_prompt": MUSIC,
            **_bot_fields(sess, base),
        })
        assert r.status_code == 202, (label, r.status_code, r.text[:300])
        job = r.json()
        state[label] = {"platform_job_id": job["id"],
                        "worker_job_id": None, "submitted_at": time.time()}
        out[label] = state[label]
    return out


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def submit_camera(labels: str) -> dict:
    """Worker-direct Fun Camera jobs. labels = comma list from 3a,3b,3c."""
    import requests

    key = os.environ["WORKER_API_KEY"]
    specs = {
        "3a": {"camera_recipe": "distilled", "camera_speed": 3.0},
        "3b": {"camera_recipe": "mid", "camera_speed": 1.0},
        "3c": {"camera_recipe": "stock", "camera_speed": 3.0},
    }
    out = {}
    img = _start_b64()
    for label in [x.strip() for x in labels.split(",") if x.strip()]:
        if state.get(label):
            out[label] = state[label]
            continue
        spec = specs[label]
        r = requests.post(f"{WORKER_BASE}/jobs", timeout=60,
                          headers={"X-API-Key": key}, json={
                              "lane": "production",
                              "prompt": SCENE,
                              "negative_prompt": NEGATIVE,
                              "seed": SEED,
                              "start_image_png_b64": img,
                              "aspect_ratio": "9:16",
                              "segments": 1,
                              "camera_pose": "Pan Left",
                              "camera_speed": spec["camera_speed"],
                              "camera_recipe": spec["camera_recipe"],
                          })
        assert r.status_code == 202, (label, r.status_code, r.text[:300])
        state[label] = {"worker_job_id": r.json()["job_id"],
                        "submitted_at": time.time(), **spec}
        out[label] = state[label]
    return out


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def status(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    sess, _owner = _owner_session(base)
    key = os.environ["WORKER_API_KEY"]
    out = {}
    for label in ("2a", "2b", "3a", "3b", "3c"):
        st = state.get(label)
        if not st:
            out[label] = {"submitted": False}
            continue
        entry = {"submitted": True}
        if st.get("platform_job_id"):
            # No single-job GET route exists; the list endpoint
            # mirrors every owner job (drives finishing too).
            r = sess.get(f"{base}/api/jobs", timeout=60)
            if r.status_code == 200:
                j = next((x for x in r.json().get("jobs") or []
                          if x.get("id") == st["platform_job_id"]), None)
                if j:
                    entry["platform_status"] = j.get("status")
                    entry["voiceover_status"] = j.get("voiceover_status")
                    entry["error"] = j.get("error")
            rec = pjobs.get(st["platform_job_id"]) or {}
            entry["worker_job_id"] = rec.get("worker_job_id")
            entry["record"] = {k: rec.get(k) for k in (
                "status", "charged_cents", "included_cost_cents",
                "cost_cents", "settled_cost_cents", "render_seconds",
                "voiceover_status", "audio_fallback", "bytes")}
            if rec.get("worker_job_id"):
                st["worker_job_id"] = rec["worker_job_id"]
                state[label] = st
        wj = st.get("worker_job_id")
        if wj:
            wr = wjobs.get(wj) or {}
            entry["worker_status"] = wr.get("status")
            entry["worker_error"] = wr.get("error")
            entry["render_seconds"] = wr.get("render_seconds")
            entry["worker_bytes"] = wr.get("bytes")
        out[label] = entry
    return out


# ---------------------------------------------------------------------------
# Measurement (runs inside the collect function)
# ---------------------------------------------------------------------------

def _ffprobe(path):
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=120)
    return json.loads(probe.stdout)


def _loudnorm(path):
    import re
    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af",
         "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    m = re.search(r"\{\s*\"input_i\".*?\}", proc.stderr, re.S)
    if not m:
        return None
    d = json.loads(m.group(0))
    return {"integrated_lufs": float(d["input_i"]),
            "true_peak_db": float(d["input_tp"]),
            "lra": float(d["input_lra"])}


def analyze_audio(path):
    """Speech-presence evidence: per-window energy in the speech
    band (300-3400 Hz) vs the full band, plus LUFS. A Kokoro line
    over a ducked music bed shows a block of speech-band-dominant
    windows while the line runs, then bed-only ratios after."""
    import subprocess
    import wave

    import numpy as np

    info = _ffprobe(path)
    streams = [{"type": s["codec_type"], "codec": s["codec_name"]}
               for s in info["streams"]]
    result = {"streams": streams,
              "duration": float(info["format"]["duration"]),
              "bytes": Path(path).stat().st_size}
    if not any(s["type"] == "audio" for s in streams):
        result["verdict"] = "NO AUDIO STREAM"
        return result
    result["loudnorm"] = _loudnorm(path)
    wav = Path(str(path) + ".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ac", "1", "-ar", "16000",
         str(wav)], capture_output=True, timeout=300, check=True)
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()),
                                dtype=np.int16).astype(np.float64) / 32768.0
    win = int(sr * 0.05)  # 50 ms windows
    n = len(samples) // win
    freqs = np.fft.rfftfreq(win, 1 / sr)
    speech_band = (freqs >= 300) & (freqs <= 3400)
    full_band = (freqs >= 100) & (freqs <= 7500)
    ratios, rms = [], []
    for i in range(n):
        seg = samples[i * win:(i + 1) * win] * np.hanning(win)
        spec = np.abs(np.fft.rfft(seg)) ** 2
        tot = spec[full_band].sum()
        ratios.append(float(spec[speech_band].sum() / tot) if tot > 0 else 0.0)
        rms.append(float(np.sqrt((samples[i * win:(i + 1) * win] ** 2).mean())))
    ratios = np.array(ratios)
    rms = np.array(rms)
    audible = rms > 0.003
    speechy = audible & (ratios > 0.55)
    per_quarter = []
    q = int(sr * 0.25) // win
    for i in range(0, n, q):
        chunk = speechy[i:i + q]
        per_quarter.append(round(float(chunk.mean()), 2) if len(chunk) else 0.0)
    dur = len(samples) / sr
    head = speechy[:int(min(4.0, dur) / 0.05)]
    tail_start = int(max(0.0, dur - 1.5) / 0.05)
    result["speech"] = {
        "speechy_fraction_first_4s": round(float(head.mean()), 3) if len(head) else None,
        "speechy_fraction_last_1.5s": round(float(speechy[tail_start:].mean()), 3) if n > tail_start else None,
        "speechy_fraction_by_quarter_second": per_quarter,
        "max_window_rms": round(float(rms.max()), 4),
    }
    result["verdict"] = (
        "SPEECH PRESENT" if len(head) and head.mean() > 0.25
        else "NO CLEAR SPEECH")
    return result


def measure_camera(path):
    """Run-18 method (red-blob centroid + phase drift) extended with
    a frame-to-frame phase-correlation translation series sampled
    every 0.5 s: cumulative drift in px at full res and % of the
    720 px frame width."""
    import subprocess

    import numpy as np
    from PIL import Image

    info = _ffprobe(path)
    dur = float(info["format"]["duration"])
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    width = vstream["width"]

    def frame_at(t, tag):
        outp = Path(f"/tmp/cam_{Path(path).stem}_{tag}.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(path),
             "-frames:v", "1", str(outp)],
            capture_output=True, timeout=120, check=True)
        return np.asarray(Image.open(outp).convert("RGB")).astype(int)

    def blob(frame):
        r, g, b = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
        mask = (r > 120) & (r - g > 50) & (r - b > 50)
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return {"area_frac": 0.0, "cx": None, "cy": None}
        h, w = mask.shape
        return {"area_frac": round(float(mask.mean()), 5),
                "cx": round(float(xs.mean() / w), 4),
                "cy": round(float(ys.mean() / h), 4)}

    def drift(a, b):
        ga = a.mean(axis=2)[::4, ::4]
        gb = b.mean(axis=2)[::4, ::4]
        fa = np.fft.rfft2(ga - ga.mean())
        fb = np.fft.rfft2(gb - gb.mean())
        corr = fa * np.conj(fb)
        corr /= np.abs(corr) + 1e-9
        shift = np.fft.irfft2(corr, s=ga.shape)
        y, x = np.unravel_index(np.argmax(shift), shift.shape)
        if x > ga.shape[1] // 2:
            x -= ga.shape[1]
        if y > ga.shape[0] // 2:
            y -= ga.shape[0]
        return int(x) * 4, int(y) * 4  # back to full-res px

    times = [round(0.5 * i, 1) for i in range(int(dur / 0.5) + 1)]
    if times[-1] > dur - 0.05:
        times[-1] = round(dur - 0.05, 2)
    frames = [frame_at(t, f"{i}") for i, t in enumerate(times)]
    series = []
    cum_x = cum_y = 0
    for i, (t, fr) in enumerate(zip(times, frames)):
        entry = {"t": t, **blob(fr)}
        if i:
            dx, dy = drift(frames[i - 1], fr)
            cum_x += dx
            cum_y += dy
            entry["step_drift_px"] = {"x": dx, "y": dy}
            entry["cum_drift_px"] = {"x": cum_x, "y": cum_y}
        series.append(entry)
    first_last = drift(frames[0], frames[-1])
    cx0 = series[0].get("cx")
    cx1 = series[-1].get("cx")
    result = {
        "duration": dur, "width": width,
        "series": series,
        "first_last_drift_px": {"x": first_last[0], "y": first_last[1]},
        "cumulative_drift_px": {"x": cum_x, "y": cum_y},
        "cumulative_drift_pct_width": round(100 * cum_x / width, 2),
        "centroid_delta_cx": (round(cx1 - cx0, 4)
                              if cx0 is not None and cx1 is not None else None),
    }
    mag = abs(result["cumulative_drift_pct_width"])
    result["verdict"] = ("REAL PAN" if mag >= 5 else
                         "WEAK" if mag >= 1 else "NONE")
    return result


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    volumes={"/models": volume},
    timeout=1800,
)
def collect(base: str) -> dict:
    """For every finished job not yet collected: fetch the finished
    file, stash it in the Volume under proof_renders/, measure it,
    and record settled-cost facts. Idempotent."""
    import requests

    base = base.rstrip("/")
    sess, _owner = _owner_session(base)
    key = os.environ["WORKER_API_KEY"]
    results = dict(state.get("results") or {})
    outdir = Path("/models/proof_renders")
    outdir.mkdir(parents=True, exist_ok=True)

    for label in ("2a", "2b", "3a", "3b", "3c"):
        if label in results:
            continue
        st = state.get(label)
        if not st:
            continue
        entry = {"label": label, "estimate_usd": ESTIMATES[label]}
        if st.get("platform_job_id"):
            pid = st["platform_job_id"]
            r = sess.get(f"{base}/api/jobs", timeout=60)
            pub = None
            if r.status_code == 200:
                pub = next((x for x in r.json().get("jobs") or []
                            if x.get("id") == pid), None)
            if not pub or pub.get("status") != "done":
                continue
            rec = pjobs.get(pid) or {}
            entry["platform_record"] = {k: rec.get(k) for k in (
                "status", "charged_cents", "included_cost_cents",
                "cost_cents", "settled_cost_cents", "render_seconds",
                "voiceover_status", "audio_fallback", "bytes",
                "worker_job_id")}
            fr = sess.get(f"{base}/api/jobs/{pid}/file", timeout=600)
            assert fr.status_code == 200, (label, fr.status_code)
            dest = outdir / f"{label}.mp4"
            dest.write_bytes(fr.content)
            entry["audio_analysis"] = analyze_audio(dest)
            # Raw worker file for contrast: the worker render is
            # silent; the platform file must carry the mixed audio.
            wj = rec.get("worker_job_id")
            if wj:
                wr = requests.get(f"{WORKER_BASE}/jobs/{wj}/file",
                                  headers={"X-API-Key": key}, timeout=600)
                if wr.status_code == 200:
                    rawp = outdir / f"{label}_raw.mp4"
                    rawp.write_bytes(wr.content)
                    rinfo = _ffprobe(rawp)
                    entry["raw_worker_streams"] = [
                        s["codec_type"] for s in rinfo["streams"]]
                    rawp.unlink()
                wrec = wjobs.get(wj) or {}
                entry["render_seconds"] = wrec.get("render_seconds")
        else:
            wj = st["worker_job_id"]
            wrec = wjobs.get(wj) or {}
            if wrec.get("status") != "done":
                continue
            entry["render_seconds"] = wrec.get("render_seconds")
            entry["worker_record"] = {k: wrec.get(k) for k in (
                "status", "camera_pose", "camera_speed", "camera_recipe",
                "render_seconds", "bytes", "error")}
            fr = requests.get(f"{WORKER_BASE}/jobs/{wj}/file",
                              headers={"X-API-Key": key}, timeout=600)
            assert fr.status_code == 200, (label, fr.status_code)
            dest = outdir / f"{label}.mp4"
            dest.write_bytes(fr.content)
            entry["camera_measurement"] = measure_camera(dest)
        if entry.get("render_seconds"):
            entry["video_gpu_usd"] = round(
                entry["render_seconds"] * L40S_USD_PER_SEC, 4)
        entry["volume_file"] = f"proof_renders/{label}.mp4"
        results[label] = entry
        state["results"] = results
    volume.commit()
    return results


data_volume = modal.Volume.from_name("moonfactory-data", create_if_missing=True)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    volumes={"/models": volume, "/data": data_volume},
    timeout=900,
)
def audio_proof() -> dict:
    """Decisive mix evidence for 2a/2b: least-squares fit of the
    delivered mix against the job's own cached voiceover wav and
    music bed wav (moonfactory-data/files/{voice,beds}/<job>.wav).
    The finishing chain mixes voice at 1.0 and the bed at 0.22, so
    the fit should recover gain_voice ~= 1.0 (up to the final
    loudnorm scale) and gain_bed ~= 0.22 x that same scale, with
    the voice's correlation peak at its placed offset."""
    import subprocess
    import wave

    import numpy as np

    def load_wav(path, sr=16000):
        conv = Path(str(path) + ".16k.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-ac", "1", "-ar", str(sr),
             str(conv)], capture_output=True, timeout=300, check=True)
        with wave.open(str(conv), "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()),
                                 dtype=np.int16).astype(np.float64) / 32768.0

    out = {}
    for label in ("2a", "2b"):
        st = state.get(label) or {}
        pid = st.get("platform_job_id")
        if not pid:
            continue
        rec = pjobs.get(pid) or {}
        entry = {"voiceover_status": rec.get("voiceover_status"),
                 "audio_fallback": rec.get("audio_fallback")}
        voice_p = Path(f"/data/files/voice/{pid}.wav")
        bed_p = Path(f"/data/files/beds/{pid}.wav")
        mix_p = Path(f"/models/proof_renders/{label}.mp4")
        entry["voice_wav_exists"] = voice_p.exists()
        entry["bed_wav_exists"] = bed_p.exists()
        if voice_p.exists() and mix_p.exists():
            mix = load_wav(mix_p)
            voice = load_wav(voice_p)
            sr = 16000
            # FFT cross-correlation, locally normalized: find the
            # lag at which the voice sits in the mix. Lag L means
            # voice[0] aligns with mix[L].
            nfft = 1 << int(np.ceil(np.log2(len(mix) + len(voice))))
            fm = np.fft.rfft(mix, nfft)
            fv = np.fft.rfft(voice[::-1], nfft)
            xcorr = np.fft.irfft(fm * fv, nfft)
            # xcorr[k] for k in [0, len(mix)) is the raw correlation
            # at lag k - (len(voice) - 1); search lags -0.25s..+2s.
            base = len(voice) - 1
            lo = max(0, base - sr // 4)
            hi = min(len(xcorr), base + 2 * sr)
            window = xcorr[lo:hi]
            # Local mix energy for normalization.
            cs = np.concatenate(([0.0], np.cumsum(mix ** 2)))
            lags = np.arange(lo, hi) - base
            starts = np.clip(lags, 0, len(mix))
            ends = np.clip(lags + len(voice), 0, len(mix))
            energy = cs[ends] - cs[starts]
            vnorm = np.linalg.norm(voice)
            ncc = np.where(energy > 0,
                           window / (np.sqrt(np.maximum(energy, 1e-12))
                                     * vnorm), 0.0)
            k = int(np.argmax(np.abs(ncc)))
            lag = int(lags[k])
            entry["voice_ncc_peak"] = round(float(ncc[k]), 3)
            entry["voice_offset_s"] = round(lag / sr, 3)
            # Joint least squares at the found lag:
            # mix ~= a*voice(shifted by lag) + b*bed(at 0).
            n = len(mix)
            v = np.zeros(n)
            if lag >= 0:
                seg = voice[:max(0, n - lag)]
                v[lag:lag + len(seg)] = seg
            else:
                seg = voice[-lag:]
                v[:len(seg)] = seg[:n]
            cols = [v]
            names = ["voice_gain"]
            if bed_p.exists():
                bed = load_wav(bed_p)
                b = np.zeros(n)
                b[:min(n, len(bed))] = bed[:min(n, len(bed))]
                cols.append(b)
                names.append("bed_gain")
            A = np.stack(cols, axis=1)
            coef, *_ = np.linalg.lstsq(A, mix, rcond=None)
            fit = A @ coef
            entry["mix_fit"] = {nm: round(float(c), 3)
                                for nm, c in zip(names, coef)}
            entry["mix_fit_r2"] = round(
                float(1 - ((mix - fit) ** 2).sum() / (mix ** 2).sum()), 3)
            if len(coef) == 2 and coef[1] > 0 and coef[0] > 0:
                entry["voice_to_bed_gain_ratio"] = round(
                    float(coef[0] / coef[1]), 2)
        out[label] = entry
    return out


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=300,
)
def gallery_check(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    sess, _owner = _owner_session(base)
    out = {}
    g = sess.get(f"{base}/api/gallery", timeout=30)
    items = (g.json().get("items") or []) if g.status_code == 200 else []
    ids = {it.get("id") or it.get("job_id") for it in items}
    for label in ("2a", "2b"):
        st = state.get(label) or {}
        pid = st.get("platform_job_id")
        out[label] = {"platform_job_id": pid, "in_gallery": pid in ids}
    out["gallery_items"] = len(items)
    owner = users.get(f"email:{OWNER_EMAIL}") or {}
    out["owner_after"] = {k: owner.get(k) for k in (
        "plan", "net_paid_cents", "balance_cents", "free_jobs_total",
        "cost_settled_cents", "is_owner")}
    out["owner_before"] = state.get("owner_before")
    return out


@app.local_entrypoint()
def main(action: str, base: str = "https://magrify--moonfactory-api.us-east.modal.run"):
    if action == "submit-audio":
        print(json.dumps(submit_audio.remote(base), indent=1, default=str))
    elif action == "submit-camera-early":
        print(json.dumps(submit_camera.remote("3a,3b"), indent=1, default=str))
    elif action == "submit-stock":
        print(json.dumps(submit_camera.remote("3c"), indent=1, default=str))
    elif action == "status":
        print(json.dumps(status.remote(base), indent=1, default=str))
    elif action == "collect":
        print(json.dumps(collect.remote(base), indent=1, default=str))
    elif action == "gallery":
        print(json.dumps(gallery_check.remote(base), indent=1, default=str))
    elif action == "audio-proof":
        print(json.dumps(audio_proof.remote(), indent=1, default=str))
    else:
        raise SystemExit(f"unknown action {action}")

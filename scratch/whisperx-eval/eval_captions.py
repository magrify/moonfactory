"""WhisperX vs the current caption timing path (2026-11-26).

The platform's captions are timed from the word timestamps
faster-whisper (base model, int8, word_timestamps=True)
hears in modal-worker/audio_app.py's _transcribe_words.
This eval runs that exact configuration against WhisperX
(same base model, then wav2vec2 forced alignment) on two
existing speech clips and measures, for every word start,
the distance to the nearest waveform energy onset
(librosa onset detection): a word that starts when the
sound actually starts scores near zero.

  clip A: scratch/framehush-verify/voice.wav
          (known script: "Welcome to Moonfactory")
  clip B: scratch/beat-sync-proof/work/narration.wav
          (12.5s of narration; the systems are compared on
          the words they both hear, in order)

Run:  ~/workspace/.venvs/wx/bin/python eval_captions.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
CLIPS = [
    ("A", ROOT / "scratch" / "framehush-verify" / "voice.wav",
     "Welcome to Moonfactory"),
    ("B", ROOT / "scratch" / "beat-sync-proof" / "work" / "narration.wav",
     None),
]


def onsets(path):
    import librosa
    y, sr = librosa.load(str(path), sr=16000, mono=True)
    times = librosa.onset.onset_detect(
        y=y, sr=sr, units="time", backtrack=True)
    return sorted(float(t) for t in times)


def nearest_onset_dist(start, onset_times):
    import bisect
    i = bisect.bisect_left(onset_times, start)
    cands = []
    if i < len(onset_times):
        cands.append(abs(onset_times[i] - start))
    if i > 0:
        cands.append(abs(onset_times[i - 1] - start))
    return min(cands) if cands else None


def fw_words(model, path):
    segments, _info = model.transcribe(
        str(path), word_timestamps=True, language="en", beam_size=1)
    heard = []
    for seg in segments:
        for w in (seg.words or []):
            heard.append({"word": w.word.strip(),
                          "start": float(w.start)})
    return heard


def wx_words(path):
    import whisperx
    model = whisperx.load_model("base", "cpu", compute_type="int8",
                                language="en")
    audio = whisperx.load_audio(str(path))
    result = model.transcribe(audio, batch_size=8)
    align_model, metadata = whisperx.load_align_model(
        language_code="en", device="cpu")
    aligned = whisperx.align(result["segments"], align_model, metadata,
                             audio, "cpu", return_char_alignments=False)
    heard = []
    for seg in aligned["segments"]:
        for w in (seg.get("words") or []):
            if "start" in w:
                heard.append({"word": w["word"].strip(),
                              "start": float(w["start"])})
    return heard


def score(words, onset_times):
    dists = [nearest_onset_dist(w["start"], onset_times) for w in words]
    dists = [d for d in dists if d is not None]
    if not dists:
        return None
    dists.sort()
    n = len(dists)
    return {"words": n,
            "mean_onset_dist_s": round(sum(dists) / n, 4),
            "median_onset_dist_s": round(dists[n // 2], 4),
            "p90_onset_dist_s": round(dists[int(n * 0.9)], 4)}


def main():
    from faster_whisper import WhisperModel
    fw = WhisperModel("base", device="cpu", compute_type="int8")
    report = {}
    for tag, path, script in CLIPS:
        onset_times = onsets(path)
        a = fw_words(fw, path)
        b = wx_words(path)
        entry = {
            "clip": str(path.relative_to(ROOT)),
            "script": script,
            "energy_onsets": len(onset_times),
            "faster_whisper": score(a, onset_times),
            "whisperx": score(b, onset_times),
            "faster_whisper_words": a,
            "whisperx_words": b,
        }
        report[tag] = entry
        print(f"clip {tag}: {entry['clip']}")
        print(f"  energy onsets: {len(onset_times)}")
        print(f"  faster-whisper: {entry['faster_whisper']}")
        print(f"  whisperx:       {entry['whisperx']}")
        if script:
            print(f"  fw words: {[(w['word'], round(w['start'], 3)) for w in a]}")
            print(f"  wx words: {[(w['word'], round(w['start'], 3)) for w in b]}")
    (HERE / "results.json").write_text(json.dumps(report, indent=2))
    print("EVAL DONE ->", HERE / "results.json")


if __name__ == "__main__":
    sys.exit(main())

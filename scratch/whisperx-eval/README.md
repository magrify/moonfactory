# Caption timing eval: faster-whisper vs WhisperX (2026-11-26/27)

Question: are WhisperX's word timings (faster-whisper
transcript + wav2vec2 forced alignment) measurably tighter
than the current caption timing path, which is
faster-whisper's own word timestamps (base model, int8,
word_timestamps=True, the exact configuration of
_transcribe_words)?

Method: for every word start, the distance to the nearest
waveform energy onset (librosa onset detection,
backtracked). A word that starts when the sound starts
scores near zero. Harness: eval_captions.py; full word
lists in results.json.

## Results

Clip A: scratch/framehush-verify/voice.wav, 8 words of
real generated speech ("Today is a beautiful day, don't
you think?"), 21 energy onsets.

- faster-whisper: mean 0.0925s, median 0.084s, p90 0.220s
- WhisperX:       mean 0.0407s, median 0.017s, p90 0.136s

Clip C: jfk.flac (the faster-whisper project's own test
asset, 11s, 22 words, known transcript), 61 energy onsets.

- faster-whisper: mean 0.0975s, median 0.096s, p90 0.152s
- WhisperX:       mean 0.0266s, median 0.023s, p90 0.062s

Clip B (scratch/beat-sync-proof/work/narration.wav) was
excluded: it carries no speech at all (both systems hear
zero words; it is a music/placeholder track from the beat
proof, as are that folder's line_*.wav and vox_*.wav).

Verdict: WhisperX is measurably tighter on every metric
on both speech clips, roughly 2.3 to 3.7 times closer to
the onsets on the mean and about 4 to 5 times on the
median.

## Wiring (the consequence)

The platform's word check / transcribe stages run on the
Kaggle audio worker (worker-kaggle/audio_worker_kernel.py,
via _word_check_via_kaggle), so the retime lives there:
_align_words in the kernel's WORDCHECK_SCRIPT retimes the
words that LEAVE each stage (transcribe mode's words, and
word check mode's final heard words, aligned against the
repaired track when a fix was spliced in). The word
check's own comparisons and splice windows keep the
original timings. setup_whisper installs WhisperX best
effort after the checked faster-whisper install, with
the Modal stage's pins repeated so the resolver keeps
them; any failure anywhere in the aligner (install,
model or tokenizer download, a word sequence that does
not line up) leaves the faster-whisper timings untouched.
The Modal audio app (the fallback stage) is unchanged.

The kernel's own _align_words (extracted from
WORDCHECK_SCRIPT and run against both clips with the
kernel's own _transcribe_words) reproduces the win:
voice.wav mean 0.0925s to 0.0376s, median 0.084s to
0.020s; jfk.flac mean 0.0975s to 0.0255s, median 0.096s
to 0.021s. (Slightly different from the raw WhisperX
pipeline numbers above because the kernel aligns one
hand-built segment; the conclusion is the same.)

Environment notes from the local install: pip needs a
TMPDIR on a real disk (the /tmp tmpfs cannot hold the
torch wheels), faster-whisper must stay at the production
pin 1.2.1 with av<19 (a newer PyAV breaks its decode
call), and WhisperX's alignment needs NLTK's punkt_tab
data, which the aligner fetches on first use.

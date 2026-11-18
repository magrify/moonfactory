# Moonfactory story template score: an original light march in Bb at 120 bpm (one quarter note is
# one footfall, the grid the shorts' music is built on), synthesized with the shorts template's own
# instrument kit (dsp.py + instruments.py from claude-animated-shorts, MIT). The arrangement loops an
# eight bar theme with oompah brass, snare, fife and a whistle chorus, and lands a soft whoosh and a
# bell on every scene change the platform's plan reports. Mastered to about -15 LUFS with a true
# peak ceiling, written as an exact length 48 kHz stereo wav.
#
# Usage: python3 motion_audio.py <duration_seconds> <scene_starts_csv> <out.wav>
import sys

import numpy as np

from dsp import SR, ns, rng, pan, st, place, fades, dbg, make_ir, convolve_st, integrated, true_peak, write_wav, bp
import instruments as I

F = I.m2f
Q = 0.5            # one quarter note at 120 bpm
BAR = 4 * Q        # 2 seconds

# theme: (bar, beat, length in quarters, midi) — an original tune in Bb major
THEME = [
    (0, 0, 1, 70), (0, 1, .5, 74), (0, 1.5, .5, 77), (0, 2, 1, 82), (0, 3, 1, 77),
    (1, 0, 1, 79), (1, 1, .5, 77), (1, 1.5, .5, 74), (1, 2, 2, 70),
    (2, 0, 1, 75), (2, 1, .5, 79), (2, 1.5, .5, 82), (2, 2, 1, 81), (2, 3, 1, 79),
    (3, 0, 1, 77), (3, 1, .5, 74), (3, 1.5, .5, 72), (3, 2, 2, 74),
    (4, 0, 1, 70), (4, 1, .5, 74), (4, 1.5, .5, 77), (4, 2, 1, 82), (4, 3, 1, 86),
    (5, 0, 1, 82), (5, 1, .5, 79), (5, 1.5, .5, 75), (5, 2, 1, 77), (5, 3, .5, 74), (5, 3.5, .5, 77),
    (6, 0, 1, 79), (6, 1, .5, 75), (6, 1.5, .5, 79), (6, 2, 1, 81), (6, 3, 1, 79),
    (7, 0, 2, 77), (7, 2, 1, 74), (7, 3, 1, 70),
]
BARS = ["Bb", "Bb", "Eb", "F", "Bb", "Eb", "F7", "Bb"]
CH = {
    "Bb": dict(oom=(34, 41), pah=[53, 58, 62], hi=[58, 62, 65]),
    "Eb": dict(oom=(39, 46), pah=[55, 58, 63], hi=[58, 63, 67]),
    "F": dict(oom=(41, 36), pah=[53, 57, 60], hi=[57, 60, 65]),
    "F7": dict(oom=(41, 36), pah=[51, 57, 60], hi=[57, 60, 63]),
}


def whoosh(r, dur=0.5):
    n = ns(dur)
    nz = r.standard_normal(n)
    y = bp(nz, 500, 2600, 1)
    u = np.arange(n) / max(n - 1, 1)
    return y * (np.sin(np.pi * u) ** 1.5)


def build(duration: float, scenes: list) -> np.ndarray:
    r = rng(7)
    n_bars = int(np.ceil(duration / BAR))
    total = ns(duration + 0.1)
    music = np.zeros((2, total))
    fx = np.zeros((2, total))

    def put(buf, x, t, p=0.0, g=1.0):
        place(buf, pan(x, p) if np.ndim(x) == 1 else x, t, g)

    for bar in range(n_bars):
        t_bar = bar * BAR
        if t_bar >= duration:
            break
        ch = CH[BARS[bar % 8]]
        # oompah: tuba on the beat, horns after it
        for beat, root in ((0, ch["oom"][0]), (2, ch["oom"][1])):
            put(music, I.brass(F(root), Q * 0.92, vel=0.5, r=rng(bar * 4 + beat), kind="tuba"), t_bar + beat * Q, -0.08)
        for beat in (1, 3):
            for m in ch["pah"]:
                put(music, I.brass(F(m), Q * 0.42, vel=0.30, r=rng(bar * 8 + beat), kind="horn"), t_bar + beat * Q, 0.08)
        # snare on the footfall grid
        for beat in range(4):
            put(music, I.snare(vel=0.34 if beat % 2 else 0.46, r=rng(bar * 16 + beat)), t_bar + beat * Q, 0.03)
        # warm pad under the bar
        put(music, I.pad([F(m) for m in ch["hi"]], BAR, vel=0.13, r=rng(bar * 3 + 1)), t_bar, 0.0)
        # theme on the fife, every pass; glock doubles long notes on the first pass
        for (b, beat, ln, m) in THEME:
            if b != bar % 8:
                continue
            t_n = t_bar + beat * Q
            if t_n >= duration:
                continue
            put(music, I.fife(F(m), ln * Q * 0.94, vel=0.42, r=rng(bar * 32 + int(beat * 2))), t_n, -0.04)
            if bar < 8 and ln >= 1:
                put(music, I.glock(F(m + 12), vel=0.20, r=rng(bar * 64 + int(beat * 2))), t_n, 0.10)

    # whistle chorus joins from the second pass, in four bar phrases
    for start_bar in range(8, n_bars, 4):
        notes = []
        for k in range(4):
            bar = start_bar + k
            if bar >= n_bars:
                break
            for (b, beat, ln, m) in THEME:
                if b == bar % 8:
                    notes.append((bar * BAR + beat * Q, ln * Q, m, 0.5))
        if notes:
            t0, y = I.whistle(notes, rng(start_bar), detune=4.0)
            put(music, y, t0, 0.05, 0.5)

    # scene change cues: a soft whoosh into each cut, a bell as the new scene lands
    for s in scenes:
        if s <= 0.05 or s >= duration - 0.3:
            continue
        put(fx, whoosh(rng(int(s * 10) + 3)), max(0.0, s - 0.13), 0.0, 0.85)
        put(fx, I.glock(F(82), vel=0.30, r=rng(int(s * 10) + 4)), s + 0.04, 0.06)
        for (tt_, vv) in I.snare_roll(max(0.0, s - 0.32), s - 0.02, 0.08, 0.26, rng(int(s * 10) + 5)):
            put(fx, I.snare(vel=vv, r=rng(int(tt_ * 100))), tt_, 0.03)
    # title bell and the golden ending
    put(fx, I.triangle(vel=0.5, r=rng(11)), 0.45, 0.0)
    put(fx, I.glock(F(82), vel=0.32, r=rng(12)), 0.5, 0.05)
    if scenes:
        outro = scenes[-1]
        put(fx, I.cymbal(vel=0.22, r=rng(13)), outro, 0.0)
        for j, m in enumerate((82, 86, 89)):
            put(fx, I.musicbox(F(m), vel=0.30, r=rng(14 + j)), outro + 0.15 + j * 0.19, 0.05)

    # a little room on the music, then the master
    ir = make_ir(1.15, 5, length=1.3)
    wet = convolve_st(music, ir)
    mix = music + 0.12 * wet[: music.shape[0], : music.shape[1]] + fx
    mix = mix[:, : ns(duration)]
    # closing fade so the film lands instead of stopping
    n = mix.shape[1]
    tail = np.ones(n)
    k = ns(1.1)
    tail[-k:] = np.linspace(1, 0, k) ** 1.4
    mix = mix * tail[None, :] * fades(np.ones(n), 0.004, 0.0)[None, :]
    lufs = integrated(mix)
    mix = mix * dbg(-15.0 - lufs)
    tp_db = true_peak(mix)
    if tp_db > -1.2:
        mix = mix * dbg(-1.2 - tp_db)
    return mix


def main():
    duration = float(sys.argv[1])
    scenes = [float(x) for x in sys.argv[2].split(",") if x.strip()]
    out = sys.argv[3]
    mix = build(duration, scenes)
    write_wav(out, mix, bits=24)
    print(f"wrote {out}: {mix.shape[1] / SR:.3f}s at {SR} Hz, "
          f"{integrated(mix):.1f} LUFS, true peak {true_peak(mix):.1f} dBTP")


if __name__ == "__main__":
    main()

"""Perceptual fingerprints, for measuring whether uniqualization actually works.

``verify_quality.py`` answers "does the output still look and sound right".
This module answers the opposite question: "would a content-matching system
still recognise the output as the source".

Both fingerprints here are the textbook constructions that real matchers are
built on, so a distance measured against them is a meaningful proxy:

  * :func:`video_hashes` is a pHash — average-downscale to 32x32 luma, 2D DCT,
    threshold the low-frequency block against its median. This is what makes
    grain useless as a defence (the downscale averages it away) and what makes
    a low-frequency field effective (it lands in the coefficients that survive).
  * :func:`audio_fingerprint` is the Haitsma-Kalker construction — energy in
    33 log-spaced bands per frame, one bit per band pair from the sign of the
    second-order difference. Robust to level, EQ and mild time-stretch by
    design, which is exactly why audio is the hard side of the problem.

No third-party dependencies: the app ships without numpy, and a gate that
cannot run is not a gate.
"""

import math
import subprocess
from typing import List, Optional, Sequence, Tuple

# ─── Video: pHash ────────────────────────────────────────────────────────

PHASH_GRID = 32       # frame is averaged down to this before the transform
PHASH_LOW = 8         # side of the retained low-frequency DCT block
PHASH_BITS = PHASH_LOW * PHASH_LOW - 1   # the DC term is dropped

_dct_cache: dict = {}


def _dct_basis(n: int, k: int) -> List[List[float]]:
    """First ``k`` DCT-II basis rows for length ``n``, cached."""
    key = (n, k)
    basis = _dct_cache.get(key)
    if basis is None:
        basis = [
            [
                math.cos(math.pi * (2 * x + 1) * u / (2 * n))
                * (math.sqrt(1.0 / n) if u == 0 else math.sqrt(2.0 / n))
                for x in range(n)
            ]
            for u in range(k)
        ]
        _dct_cache[key] = basis
    return basis


def _phash(gray: Sequence[int], n: int = PHASH_GRID, low: int = PHASH_LOW) -> int:
    """pHash of one ``n`` x ``n`` 8-bit luma frame.

    Only the first ``low`` coefficients of each transform are evaluated. The
    rest are discarded anyway, and computing them would cost 16x more.
    """
    basis = _dct_basis(n, low)
    # Rows first, keeping only the low horizontal frequencies.
    partial = []
    for y in range(n):
        row = gray[y * n:(y + 1) * n]
        partial.append([
            sum(basis[u][x] * row[x] for x in range(n)) for u in range(low)
        ])
    # Then the same down each retained column.
    coeffs: List[float] = []
    for u in range(low):
        column = [partial[y][u] for y in range(n)]
        coeffs.extend(
            sum(basis[v][y] * column[y] for y in range(n)) for v in range(low)
        )

    values = coeffs[1:]   # drop DC: it only carries average brightness
    median = sorted(values)[len(values) // 2]
    bits = 0
    for i, value in enumerate(values):
        if value > median:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def image_hash(ffmpeg: str, path: str) -> Optional[int]:
    """pHash of a still.

    Not expressible through ``video_hashes``: the image demuxer hands ffmpeg a
    single frame lasting 1/25 s, and any ``fps=`` rate at or below that samples
    zero frames out of it. Same downscale otherwise, so the numbers from the two
    functions are directly comparable.
    """
    cell = PHASH_GRID * PHASH_GRID
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", path,
         "-vf", f"scale={PHASH_GRID}:{PHASH_GRID}:flags=area,format=gray",
         "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if len(proc.stdout) < cell:
        return None
    return _phash(proc.stdout[:cell])


def video_hashes(ffmpeg: str, path: str, rate: float = 4.0,
                 limit: int = 240) -> List[int]:
    """pHash per sampled frame, ``rate`` frames per second of content.

    ``flags=area`` is the box filter a real matcher uses for the downscale, and
    it is load-bearing here: it is what averages high-frequency grain out of the
    hash while leaving a low-frequency field intact.
    """
    cell = PHASH_GRID * PHASH_GRID
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", path,
         "-vf", f"fps={rate},scale={PHASH_GRID}:{PHASH_GRID}:flags=area,format=gray",
         "-frames:v", str(limit), "-f", "rawvideo", "-"],
        capture_output=True,
    )
    buf = proc.stdout
    return [_phash(buf[i * cell:(i + 1) * cell])
            for i in range(len(buf) // cell)]


def video_distance(reference: Sequence[int],
                   candidate: Sequence[int]) -> Optional[float]:
    """Median per-frame Hamming distance, each frame free to match anywhere.

    Every candidate frame is compared against *every* reference frame and keeps
    its best match. That models an attacker who has already solved alignment,
    so the number is a floor on what a real matcher would see rather than an
    artefact of the trim offset or the speed change.
    """
    if not reference or not candidate:
        return None
    best = sorted(min(hamming(c, r) for r in reference) for c in candidate)
    return best[len(best) // 2]


# ─── Audio: Haitsma-Kalker ───────────────────────────────────────────────

AUDIO_RATE = 8000
AUDIO_FRAME = 2048
AUDIO_HOP = 512
AUDIO_BANDS = 33            # 33 edges -> 32 bits per frame
AUDIO_BAND_LOW = 300.0
AUDIO_BAND_HIGH = 2000.0


def _fft(real: List[float]) -> List[complex]:
    """Iterative radix-2 FFT. Length must be a power of two."""
    n = len(real)
    data = [complex(v, 0.0) for v in real]
    # Bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]
    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        for start in range(0, n, length):
            w = complex(1.0, 0.0)
            half = length // 2
            for k in range(start, start + half):
                even = data[k]
                odd = data[k + half] * w
                data[k] = even + odd
                data[k + half] = even - odd
                w *= step
        length <<= 1
    return data


def _band_edges() -> List[int]:
    """Log-spaced FFT bin edges across the band the fingerprint looks at."""
    ratio = AUDIO_BAND_HIGH / AUDIO_BAND_LOW
    edges = []
    for i in range(AUDIO_BANDS + 1):
        freq = AUDIO_BAND_LOW * (ratio ** (i / AUDIO_BANDS))
        edges.append(int(freq * AUDIO_FRAME / AUDIO_RATE))
    return edges


def _decode_mono(ffmpeg: str, path: str) -> List[float]:
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", path,
         "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    raw = proc.stdout
    count = len(raw) // 2
    return [int.from_bytes(raw[i * 2:i * 2 + 2], "little", signed=True) / 32768.0
            for i in range(count)]


def audio_fingerprint(ffmpeg: str, path: str) -> List[int]:
    """One 32-bit sub-fingerprint per frame."""
    samples = _decode_mono(ffmpeg, path)
    if len(samples) < AUDIO_FRAME * 2:
        return []

    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (AUDIO_FRAME - 1))
              for i in range(AUDIO_FRAME)]
    edges = _band_edges()

    energies: List[List[float]] = []
    for start in range(0, len(samples) - AUDIO_FRAME, AUDIO_HOP):
        frame = [samples[start + i] * window[i] for i in range(AUDIO_FRAME)]
        spectrum = _fft(frame)
        power = [abs(spectrum[i]) ** 2 for i in range(AUDIO_FRAME // 2)]
        bands = []
        for b in range(AUDIO_BANDS):
            lo, hi = edges[b], max(edges[b] + 1, edges[b + 1])
            bands.append(sum(power[lo:hi]))
        energies.append(bands)

    prints = []
    for n in range(1, len(energies)):
        bits = 0
        for m in range(AUDIO_BANDS - 1):
            delta = ((energies[n][m] - energies[n][m + 1])
                     - (energies[n - 1][m] - energies[n - 1][m + 1]))
            if delta > 0:
                bits |= 1 << m
        prints.append(bits)
    return prints


def audio_bit_error_rate(reference: Sequence[int],
                         candidate: Sequence[int]) -> Optional[float]:
    """Lowest bit error rate over every frame alignment of the two prints.

    Below roughly 0.35 the Haitsma-Kalker paper calls two excerpts the same
    recording, so a higher number is the goal. As with the video side, the
    alignment is searched rather than assumed.
    """
    if len(reference) < 8 or len(candidate) < 8:
        return None
    span = min(len(reference), len(candidate))
    best: Optional[float] = None
    for offset in range(-(len(reference) - span), len(candidate) - span + 1):
        errors = 0
        compared = 0
        for i in range(span):
            j = i + offset
            if 0 <= j < len(candidate) and i < len(reference):
                errors += hamming(reference[i], candidate[j])
                compared += 32
        if compared == 0:
            continue
        rate = errors / compared
        if best is None or rate < best:
            best = rate
    return best

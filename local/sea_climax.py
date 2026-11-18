"""Where the sea level's colour change lands: the start of the first full chorus.

The best source available wins:

1. NetEase's own chorus time (``/api/song/chorus``) for NetEase songs. It is
   a highlight, not always the first chorus (sometimes the last chorus, the
   bridge or the pre-chorus), so it is used only when the lyric or the audio
   agrees that a chorus starts there.
2. The timed lyric: the most repeated block of two or more lines; its first
   occurrence after the opening verse.
3. The audio: a chroma self-similarity, time-lag search for the segment that
   repeats most (as pychorus and Goto's RefraiD do), weighed with the
   arrangement getting fuller.
4. The old rule: where the arrangement gets fuller and stays full.

The moment is then snapped to the nearest strong drum within half a second.
Everything here is plain numpy and runs in well under a second a song.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np

RATE = 8000
STEP = 0.5                      # seconds per fullness value
SNAP_S = 0.5
AGREE_S = 3.0                   # NetEase's time counts as confirmed this close to a chorus start
SEARCH = (0.10, 0.75)           # share of the song a change may land in


# --- arrangement fullness --------------------------------------------------------

def fullness(pcm: np.ndarray, drum_times: np.ndarray) -> np.ndarray:
    """How full the arrangement is every ``STEP`` seconds, as a z-score."""
    window = 1024
    hop = int(RATE * STEP)
    count = max(0, (len(pcm) - window) // hop)
    if count < 8:
        return np.zeros(0)
    index = np.arange(window)[None, :] + hop * np.arange(count)[:, None]
    frames = pcm[index] * np.hanning(window).astype(np.float32)[None, :]
    spectrum = np.abs(np.fft.rfft(frames, axis=1)) + 1e-9
    freqs = np.fft.rfftfreq(window, 1.0 / RATE)
    loud = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    low = 20 * np.log10(spectrum[:, freqs < 150.0].mean(axis=1))
    bands = np.array_split(np.arange(spectrum.shape[1]), 24)
    band_db = np.stack([20 * np.log10(spectrum[:, b].mean(axis=1)) for b in bands], axis=1)
    filled = (band_db > np.percentile(band_db, 40)).mean(axis=1)
    times = np.arange(count) * STEP
    if drum_times.size:
        density = (np.searchsorted(drum_times, times + 1.0) - np.searchsorted(drum_times, times - 1.0)) / 2.0
    else:
        density = np.zeros(count)
    feats = np.stack([loud, low, density, filled], axis=1)
    feats = (feats - feats.mean(axis=0)) / (feats.std(axis=0) + 1e-6)
    return feats[:, :2].mean(axis=1) * 0.5 + feats[:, 2] * 0.3 + feats[:, 3] * 0.2


def lift(full: np.ndarray, moment: float, span: float = 12.0) -> float:
    """How much fuller the ``span`` seconds after ``moment`` are than those before."""
    if full.size == 0:
        return 0.0
    i, n = int(round(moment / STEP)), int(span / STEP)
    before, after = full[max(0, i - n): i], full[i: i + n]
    if before.size < 2 or after.size < 2:
        return 0.0
    return float(after.mean() - before.mean())


def energy_arrival(full: np.ndarray) -> float | None:
    """The old rule: where the arrangement gets fuller and stays full."""
    count = full.size
    if count < 120:
        return None
    span, build = int(30 / STEP), int(15 / STEP)
    best, best_score = None, -1e9
    for i in range(int(count * 0.15), int(count * 0.75)):
        before, after = full[max(0, i - span): i], full[i: i + span]
        if len(before) < span * 0.5 or len(after) < span * 0.5:
            continue
        uplift = float(after.mean() - before.mean())
        held = float((after > before.mean() + 0.5 * before.std()).mean())
        jump = float(full[i: i + 8].mean() - full[max(0, i - 8): i].mean())
        score = uplift * held + 0.6 * jump
        if score > best_score:
            best, best_score = i * STEP, score
    return best


# --- lyric repetition -------------------------------------------------------------

_CREDIT = re.compile(
    r"^\s*(?:produced|written|composed|arranged|mixed|mastered|engineered|assistant|recorded|"
    r"photography|lyrics?|music|vocals?|programming|作词|作曲|编曲|制作|混音|母带|录音|和声|"
    r"监制|出品|发行|词|曲)\b|\bby\b.*\b(?:at|and)\b|[:：]")
_BRACKETS = re.compile(r"[\(\[（【「『].*?[\)\]）】」』]")
_FILLER = {"oh", "ooh", "oooh", "ah", "ahh", "yeah", "yea", "la", "na", "hey", "woo", "woah", "whoa",
           "uh", "huh", "mm", "mmm", "hmm", "ha", "baby", "da", "ay", "eh", "ohh", "yeh", "ya"}


def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = _BRACKETS.sub(" ", text)
    words = re.findall(r"[^\W_]+", text)
    if not words or all(word in _FILLER or re.fullmatch(r"(?:la|na|oh|da|woo|hey)+", word) for word in words):
        return ""
    return "".join(words)


def lyric_lines(timed) -> list[tuple[float, str]]:
    """(start, normalised text) for the sung lines, without credits and fillers."""
    out = []
    for item in timed or ():
        try:
            start, text = float(item[0]), str(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if _CREDIT.search(text):
            continue
        norm = _normal(text)
        if len(norm) >= 2:
            out.append((start, norm))
    out.sort(key=lambda item: item[0])
    return out


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def _alike(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or max(len(a), len(b)) > 1.6 * min(len(a), len(b)):
        return False
    x, y = _bigrams(a), _bigrams(b)
    return 2.0 * len(x & y) / (len(x) + len(y)) >= 0.72


def repeated_blocks(texts: list[str]) -> list[list[tuple[int, int]]]:
    """Groups of repeated line blocks, each a list of (first, end) line indices.

    Two lines repeat when they are alike *and* a neighbour repeats with them,
    so a block is at least two lines and a lone hook line does not count. A
    block starts where a repetition starts (a chorus after a pre-chorus that
    is not always sung still starts at its own first line); blocks that
    repeat each other form one group.
    """
    n = len(texts)
    if n < 4:
        return []
    same = np.zeros((n + 2, n + 2), dtype=bool)
    for i in range(n):
        for j in range(i + 1, n):
            if _alike(texts[i], texts[j]):
                same[i + 1, j + 1] = same[j + 1, i + 1] = True
    run = same[1:-1, 1:-1] & (same[:-2, :-2] | same[2:, 2:])
    # A repetition starts at (i, j) when the lines before them do not repeat.
    starts = run & ~np.pad(run, ((1, 0), (1, 0)))[:-1, :-1]
    repeated = run.any(axis=1)
    blocks, first = [], None
    for i in range(n + 1):
        boundary = i == n or not repeated[i] or (first is not None and starts[i].any())
        if first is not None and boundary:
            blocks.append((first, i))
            first = None
        if i < n and repeated[i] and first is None:
            first = i
    if not blocks:
        return []
    owner = np.full(n, -1)
    for b, (lo, hi) in enumerate(blocks):
        owner[lo:hi] = b
    parent = list(range(len(blocks)))

    def root(b):
        while parent[b] != b:
            parent[b] = parent[parent[b]]
            b = parent[b]
        return b

    for i, j in zip(*np.nonzero(run)):
        if owner[i] >= 0 and owner[j] >= 0:
            parent[root(owner[i])] = root(owner[j])
    groups: dict[int, list[tuple[int, int]]] = {}
    for b, block in enumerate(blocks):
        groups.setdefault(root(b), []).append(block)
    out = []
    for members in groups.values():
        members.sort()
        merged = [list(members[0])]
        for lo, hi in members[1:]:
            last = merged[-1]
            # Pieces of one occurrence join; a section sung twice in a row stays two.
            if lo < last[1] or (lo == last[1] and not run[last[0]:last[1], lo:hi].any()):
                last[1] = max(last[1], hi)
            else:
                merged.append([lo, hi])
        if len(merged) >= 2:
            out.append([(lo, hi) for lo, hi in merged])
    return out


CORE_REPEATS = 3        # a block sung this often is the chorus proper, not its lead-in
UNBROKEN_LIFT = 0.4     # how much more NetEase's moment must lift inside one unbroken block


def chorus_sections(texts: list[str]):
    """(sections, blocks per group). Each section is a list of occurrences,
    each a list of (first, end, group) blocks sung one after the other.

    Group B continues group A when B follows A (at most one unrepeated line
    between) in at least two and three quarters of A's occurrences. So a
    chorus split by an inner repetition is one section, and a pre-chorus
    that always leads into the chorus is that section's head.
    """
    groups = repeated_blocks(texts)
    blocks = sorted((lo, hi, g) for g, group in enumerate(groups) for lo, hi in group)
    count: dict[int, int] = {}
    follow: dict[tuple[int, int], int] = {}
    for _lo, _hi, g in blocks:
        count[g] = count.get(g, 0) + 1
    for (_lo, hi, g), (lo2, _hi2, g2) in zip(blocks, blocks[1:]):
        if g != g2 and lo2 - hi <= 1:
            follow[(g, g2)] = follow.get((g, g2), 0) + 1
    linked = {pair for pair, times in follow.items() if times >= 2 and times >= 0.75 * count[pair[0]]}
    occurrences: list[list[tuple[int, int, int]]] = []
    for block in blocks:
        current = occurrences[-1] if occurrences else None
        if (current is not None and block[0] - current[-1][1] <= 1 and (current[-1][2], block[2]) in linked
                and block[2] not in {item[2] for item in current}):
            current.append(block)
        else:
            occurrences.append([block])
    by_head: dict[int, list] = {}
    for occurrence in occurrences:
        by_head.setdefault(occurrence[0][2], []).append(occurrence)
    return [items for items in by_head.values() if len(items) >= 2], count


def lyric_section(timed, duration: float) -> dict | None:
    """The first full chorus in the timed lyric, or None.

    The chorus is the repeated section covering the most lines (a section
    first sung after 60% of the song counts a third: an outro or a bridge).
    Its first occurrence after the opening verse (four sung lines and twelve
    seconds at least) is the one. Inside it, the chorus proper starts at the
    first block sung ``CORE_REPEATS`` times or more; blocks before that are
    its lead-in (a pre-chorus that is always sung with it).

    Returns {"pick", "head", "end", "unbroken"}: the chorus start, where the
    occurrence starts and ends, and whether it is one block with no inner
    boundary (so the lyric cannot say where inside it the chorus starts).
    """
    lines = lyric_lines(timed)
    sections, count = chorus_sections([text for _start, text in lines])
    latest = duration * SEARCH[1] if duration > 0 else float("inf")
    best, best_cover = None, 0
    for section in sections:
        inside = np.zeros(len(lines), dtype=bool)
        for occurrence in section:
            inside[occurrence[0][0]:occurrence[-1][1]] = True
        first = None
        for occurrence in section:
            lo = occurrence[0][0]
            if (~inside[:lo]).sum() >= 4 and 12.0 <= lines[lo][0] <= latest:
                first = occurrence
                break
        cover = int(inside.sum())
        if first is None or cover < 4:
            continue
        if duration > 0 and lines[first[0][0]][0] > 0.6 * duration:
            cover = cover // 3
        if cover > best_cover:
            best, best_cover = first, cover
    if best is None:
        return None
    core = next((block for block in best if count[block[2]] >= CORE_REPEATS), best[0])
    return {"pick": lines[core[0]][0], "head": lines[best[0][0]][0],
            "end": lines[best[-1][1] - 1][0], "unbroken": len(best) == 1}


def lyric_chorus(timed, duration: float) -> float | None:
    found = lyric_section(timed, duration)
    return None if found is None else found["pick"]


# --- audio repetition ----------------------------------------------------------

CHROMA_WINDOW, CHROMA_HOP = 4096, 2000          # 4 chroma frames a second
CHROMA_FPS = RATE / CHROMA_HOP


def chroma(pcm: np.ndarray) -> np.ndarray:
    """Unit-length 12-bin chroma, 4 frames a second, smoothed over 2 s."""
    count = max(0, (len(pcm) - CHROMA_WINDOW) // CHROMA_HOP)
    if count < 16:
        return np.zeros((0, 12))
    window = np.hanning(CHROMA_WINDOW).astype(np.float32)
    freqs = np.fft.rfftfreq(CHROMA_WINDOW, 1.0 / RATE)
    keep = (freqs >= 80.0) & (freqs <= 2000.0)
    classes = np.rint(12 * np.log2(freqs[keep] / 440.0) + 69).astype(int) % 12
    weights = np.zeros((keep.sum(), 12), dtype=np.float32)
    weights[np.arange(keep.sum()), classes] = 1.0
    out = np.empty((count, 12), dtype=np.float32)
    for start in range(0, count, 256):          # bounded memory
        rows = np.arange(start, min(count, start + 256))
        index = np.arange(CHROMA_WINDOW)[None, :] + CHROMA_HOP * rows[:, None]
        magnitude = np.abs(np.fft.rfft(pcm[index] * window[None, :], axis=1))[:, keep]
        out[rows] = np.log1p(100.0 * magnitude) @ weights
    kernel = np.ones(8, dtype=np.float32) / 8.0
    out = np.stack([np.convolve(out[:, k], kernel, mode="same") for k in range(12)], axis=1)
    out -= out.mean(axis=1, keepdims=True)
    return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-6)


def repeated_segments(feature: np.ndarray, *, min_lag_s: float = 10.0, min_len_s: float = 8.0):
    """(start, end, lag) in seconds of segments that repeat ``lag`` seconds earlier.

    Time-lag form of the self-similarity matrix; each lag's similarity is
    measured against its neighbouring lags (RefraiD's noise removal), then
    runs that stay clearly above the rest for ``min_len_s`` become segments.
    """
    n = len(feature)
    min_lag, min_len = int(min_lag_s * CHROMA_FPS), int(min_len_s * CHROMA_FPS)
    if n < min_lag + min_len:
        return []
    similar = feature @ feature.T
    lag_t = np.full((n, n), np.nan, dtype=np.float32)        # [lag, t] = S[t, t - lag]
    for lag in range(1, n):
        lag_t[lag, lag:] = np.diagonal(similar, -lag)
    # Contrast against nearby lags (4 to 12 frames away on either side).
    filled = np.nan_to_num(lag_t, nan=0.0)
    valid = (~np.isnan(lag_t)).astype(np.float32)
    total, weight = np.cumsum(filled, axis=0), np.cumsum(valid, axis=0)

    def band(lo, hi):            # sum over lags [lag+lo, lag+hi]
        upper = np.clip(np.arange(n) + hi, 0, n - 1)
        lower = np.clip(np.arange(n) + lo - 1, -1, n - 1)
        t_sum = total[upper] - np.where(lower[:, None] >= 0, total[np.maximum(lower, 0)], 0.0)
        w_sum = weight[upper] - np.where(lower[:, None] >= 0, weight[np.maximum(lower, 0)], 0.0)
        return t_sum, w_sum

    above_sum, above_w = band(4, 12)
    below_sum, below_w = band(-12, -4)
    background = (above_sum + below_sum) / np.maximum(1.0, above_w + below_w)
    contrast = np.where(np.isnan(lag_t), np.nan, lag_t - background)
    # Average along time over the minimum segment length.
    kernel = np.ones(min_len, dtype=np.float32) / min_len
    values = contrast[min_lag:]
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return []
    threshold = float(np.percentile(finite, 90))
    edge_level = float(np.percentile(finite, 80))
    short = np.ones(8, dtype=np.float32) / 8.0                 # 2 s, for the edges
    segments = []
    for offset, row in enumerate(values):
        lag = min_lag + offset
        good = np.isfinite(row)
        if good.sum() < min_len:
            continue
        clean = np.nan_to_num(row, nan=-1.0)
        smooth = np.convolve(clean, kernel, mode="same")
        hot = (smooth > threshold) & good
        if not hot.any():
            continue
        local = np.convolve(clean, short, mode="same")
        edges = np.flatnonzero(np.diff(np.concatenate([[0], hot.astype(np.int8), [0]])))
        for lo, hi in zip(edges[::2], edges[1::2]):
            if hi - lo < min_len // 2:
                continue
            # The long average blurs the edges: walk out from the run's core
            # while the 2 s average stays high.
            lo_t, hi_t = lo, hi
            while lo_t - 1 >= max(lag, lo - min_len) and local[lo_t - 1] > edge_level:
                lo_t -= 1
            while hi_t < min(n, hi + min_len) and local[hi_t] > edge_level:
                hi_t += 1
            while lo_t < hi_t and local[lo_t] <= edge_level:
                lo_t += 1
            while hi_t > lo_t and local[hi_t - 1] <= edge_level:
                hi_t -= 1
            if hi_t - lo_t >= min_len:
                strength = float(np.nanmean(row[lo_t:hi_t]))
                segments.append((lo_t / CHROMA_FPS, hi_t / CHROMA_FPS, lag / CHROMA_FPS, strength))
    # Neighbouring lags describe the same repetition: keep the strongest.
    segments.sort(key=lambda item: -item[3] * (item[1] - item[0]))
    kept = []
    for seg in segments:
        if any(abs(seg[2] - k[2]) < 3.0 and seg[0] < k[1] and k[0] < seg[1] for k in kept):
            continue
        kept.append(seg)
        if len(kept) >= 40:
            break
    return [(a, b, lag) for a, b, lag, _s in kept]


def audio_chorus(pcm: np.ndarray, full: np.ndarray, duration: float) -> float | None:
    """The first occurrence of the most repeated, fullest section, or None."""
    segments = repeated_segments(chroma(pcm))
    if not segments:
        return None
    # Every occurrence start of every repeated segment, with its length.
    occurrences = []
    for start, end, lag in segments:
        occurrences.append((start - lag, end - start))
        occurrences.append((start, end - start))
    lo_s, hi_s = duration * SEARCH[0], duration * SEARCH[1]
    best, best_score = None, 0.0
    for moment, _length in occurrences:
        if not lo_s <= moment <= hi_s:
            continue
        support = sum(min(length, 30.0) for other, length in occurrences if abs(other - moment) <= 2.5)
        level = 0.0
        if full.size:
            i = int(moment / STEP)
            level = float(full[i: i + int(15 / STEP)].mean()) if i < full.size else 0.0
        score = support * (1.0 + 0.5 * max(-1.0, min(1.5, level))) * (1.0 + 0.4 * max(0.0, lift(full, moment)))
        if score > best_score * 1.15 or (score > best_score * 0.85 and best is not None and moment < best):
            best, best_score = moment, max(score, best_score)
    if best is None:
        return None
    # The repetition's edge is fuzzy; the arrangement's jump marks the downbeat.
    return refine_with_jump(full, best, before=4.0, after=4.0)


def refine_with_jump(full: np.ndarray, moment: float, *, before: float, after: float) -> float:
    if full.size < 16:
        return moment
    lo, hi = max(4, int((moment - before) / STEP)), min(full.size - 4, int((moment + after) / STEP))
    if hi <= lo:
        return moment
    jumps = [full[i: i + 4].mean() - full[i - 4: i].mean() for i in range(lo, hi + 1)]
    best = int(np.argmax(jumps))
    return (lo + best) * STEP if jumps[best] > 0.4 else moment


# --- picking ---------------------------------------------------------------------

def snap(moment: float, drums, reach: float = SNAP_S) -> float:
    """The nearest strong drum within ``reach`` seconds, else ``moment``."""
    strong = [t for t, strength in drums if strength >= 0.6 and abs(t - moment) <= reach]
    if not strong:
        strong = [t for t, _strength in drums if abs(t - moment) <= reach]
    return float(min(strong, key=lambda t: abs(t - moment))) if strong else float(moment)


def candidates(pcm: np.ndarray, drums, *, lyrics=(), netease: float | None = None,
               full: np.ndarray | None = None, audio: bool | None = None) -> dict:
    """Every source's moment (unsnapped), for picking and for evaluation.

    ``audio`` None computes the audio repetition only when it can matter
    (no lyric chorus); True always, False never.
    """
    duration = len(pcm) / RATE
    drum_times = np.array([t for t, _s in drums], dtype=float)
    if full is None:
        full = fullness(pcm, drum_times)
    out = {"duration": duration, "full": full, "netease": None, "lyrics": None, "audio": None,
           "section": None}
    if netease is not None and duration * 0.05 <= netease <= duration * SEARCH[1]:
        out["netease"] = float(netease)
    out["section"] = lyric_section(lyrics, duration) if lyrics else None
    out["lyrics"] = None if out["section"] is None else out["section"]["pick"]
    if audio or (audio is None and out["lyrics"] is None):
        out["audio"] = audio_chorus(pcm, full, duration)
    return out


def choose(found: dict, drums) -> tuple[float | None, str]:
    """The change moment and its source.

    With a lyric chorus, NetEase's time wins when it is at the lyric's
    chorus start, or between the occurrence's first line and that start (it
    has found the boundary inside a lead-in the lyric cannot split). Later
    than the lyric's start, NetEase usually marks the chorus's second half,
    so the lyric wins, unless the occurrence is one unbroken block and the
    arrangement lifts clearly more at NetEase's time. Without a lyric,
    NetEase alone, then the audio repetition, then the old fullness rule.
    """
    netease, section, audio = found.get("netease"), found.get("section"), found.get("audio")
    full = found.get("full", np.zeros(0))
    if section is not None:
        pick = section["pick"]
        if netease is not None:
            if section["head"] - AGREE_S <= netease <= pick + AGREE_S:
                return snap(netease, drums), "netease"
            if (section["unbroken"] and pick < netease <= section["end"]
                    and lift(full, netease, 8.0) > lift(full, pick, 8.0) + UNBROKEN_LIFT):
                return snap(netease, drums), "netease"
        return snap(pick, drums), "lyrics"
    if netease is not None:
        return snap(netease, drums), "netease"
    if audio is not None:
        return snap(audio, drums), "audio"
    fallback = energy_arrival(full)
    if fallback is None:
        return None, "none"
    return snap(fallback, drums, 1.0), "energy"


def pick(pcm: np.ndarray, drums, *, lyrics=(), netease: float | None = None,
         full: np.ndarray | None = None) -> tuple[float | None, str]:
    """(change moment, source) for one song."""
    return choose(candidates(pcm, drums, lyrics=lyrics, netease=netease, full=full), drums)

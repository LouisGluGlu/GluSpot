"""Sea-level lyric background: a vignette field, drum and syllable light,
and one colour change per song that flashes inward from the edges.

The rules are the ones settled in ``_demos/sea_dynamic_bg/海平面背景规则.md``
(PR #32). In short:

- Field: a radial vignette, bright heart and dark edges.
- Colours: the cover's most striking colour comes after the change. Its
  contrast comes before. Hues are snapped clean (every red reads crimson).
  Warm colours take black words and cool colours take white.
- Change point: the start of the first full chorus (``local.sea_climax``):
  NetEase's own chorus time when the lyric agrees, else the timed lyric's
  most repeated section, else the audio's most repeated section, snapped
  to a strong drum.
- Change: over 14 s, flashes that speed up from 0.8 s to 0.12 s apart each
  grow the new colour as a ball rising from the bottom edge, spreading up
  and to both sides. At the change point the new colour covers the screen.
- Drums and sung syllables share one light that rises from the bottom
  edge: a drum for 0.4 s, a syllable for as long as it is sung.

Audio comes from the owned player's local file. ``QAudioDecoder`` decodes it
on a worker thread and the analysis runs there too, so the UI thread only
paints. Songs without a local file (Apple Music) get the colours and the
field, but no light and no change.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QRectF, QObject, QPointF, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient

RATE = 8000
_log = logging.getLogger("GluSpot.sea")
_log.setLevel(logging.INFO)
if not _log.handlers:
    import sys as _sys
    if _sys.stderr is not None:
        _handler = logging.StreamHandler(_sys.stderr)
        _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
        _log.addHandler(_handler)
        _log.propagate = False

# --- audio source registry ---------------------------------------------------

_audio_source = None


def set_audio_source(getter) -> None:
    """``getter()`` returns (track_id, local_audio_path) for the owned player, or None."""
    global _audio_source
    _audio_source = getter


def current_audio_source():
    getter = _audio_source
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None


# --- decoding ------------------------------------------------------------------

def _to_8k_mono(chunks: list[np.ndarray], rate: int, channels: int) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    data = np.concatenate(chunks)
    if channels > 1:
        usable = len(data) // channels * channels
        data = data[:usable].reshape(-1, channels).mean(axis=1)
    if rate <= 0 or data.size == 0:
        return np.zeros(0, dtype=np.float32)
    # A short box filter keeps aliasing low enough for onset and pitch work.
    width = max(1, int(round(rate / RATE)))
    if width > 1:
        data = np.convolve(data, np.ones(width, dtype=np.float32) / width, mode="same")
    count = int(len(data) * RATE / rate)
    positions = np.arange(count) * (rate / RATE)
    return np.interp(positions, np.arange(len(data)), data).astype(np.float32)


class _DecodeThread(QThread):
    """Decode one file with QAudioDecoder inside this thread's event loop."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self.pcm: np.ndarray | None = None

    def run(self) -> None:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtMultimedia import QAudioDecoder

        decoder = QAudioDecoder()
        chunks: list[np.ndarray] = []
        shape = {"rate": 0, "channels": 0}
        loop = QEventLoop()

        def ready():
            buffer = decoder.read()
            if not buffer.isValid():
                return
            fmt = buffer.format()
            if fmt.sampleFormat().name != "Float":
                return
            shape["rate"], shape["channels"] = fmt.sampleRate(), fmt.channelCount()
            chunks.append(np.frombuffer(bytes(buffer.constData()), dtype=np.float32).copy())

        decoder.bufferReady.connect(ready)
        decoder.finished.connect(loop.quit)
        decoder.error.connect(lambda _code: loop.quit())
        QTimer.singleShot(120000, loop.quit)
        decoder.setSource(QUrl.fromLocalFile(self.path))
        decoder.start()
        loop.exec()
        decoder.stop()
        self.pcm = _to_8k_mono(chunks, shape["rate"], shape["channels"])


# --- analysis: melody, drums, syllables, arrival ------------------------------

FRAME, HOP = 2048, 400                  # 20 pitch frames a second
LOW_MIDI, HIGH_MIDI, HARMONICS = 43, 79, 5
PULSE_HOP, PULSE_WINDOW = 80, 512       # 100 onset frames a second


def _frames(pcm: np.ndarray, size: int, hop: int) -> np.ndarray:
    count = max(0, (len(pcm) - size) // hop)
    index = np.arange(size)[None, :] + hop * np.arange(count)[:, None]
    return pcm[index] * np.hanning(size).astype(np.float32)[None, :]


def _melody(pcm: np.ndarray):
    frames = _frames(pcm, FRAME, HOP)
    if len(frames) == 0:
        return np.zeros(0), np.zeros(0)
    spectrum = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    loud = np.sqrt((frames ** 2).mean(axis=1))
    midis = np.arange(LOW_MIDI, HIGH_MIDI + 1)
    f0 = 440.0 * 2.0 ** ((midis - 69) / 12.0)
    salience = np.zeros((len(frames), len(midis)), dtype=np.float32)
    for harmonic in range(1, HARMONICS + 1):
        bins = np.clip(np.rint(f0 * harmonic / (RATE / FRAME)).astype(int), 0, spectrum.shape[1] - 1)
        salience += (0.8 ** (harmonic - 1)) * spectrum[:, bins]
    best = salience.argmax(axis=1)
    peak = salience[np.arange(len(frames)), best]
    confidence = peak / (salience.mean(axis=1) + 1e-6)
    pitch = midis[best].astype(np.float64)
    audible = loud > np.percentile(loud, 35)
    pitch[(confidence < 2.2) | ~audible] = np.nan
    return pitch, loud


def _drums(pcm: np.ndarray, min_gap: float = 0.11) -> tuple[tuple[float, float], ...]:
    frames = _frames(pcm, PULSE_WINDOW, PULSE_HOP)
    if len(frames) < 20:
        return ()
    magnitude = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10.0)
    rise = np.maximum(0.0, np.diff(magnitude, axis=0))
    bins = np.fft.rfftfreq(PULSE_WINDOW, 1.0 / RATE)
    flux = np.concatenate([[0.0], rise[:, bins < 160.0].sum(axis=1) * 1.6
                           + rise[:, (bins > 1500.0) & (bins < 3800.0)].sum(axis=1)])
    span = 75
    padded = np.pad(flux, span // 2, mode="edge")
    local = np.median(np.lib.stride_tricks.sliding_window_view(padded, span)[:len(flux)], axis=1)
    spread = np.median(np.abs(flux - local)) + 1e-6
    score = (flux - local) / spread
    out, last = [], -1.0
    for i in range(2, len(score) - 2):
        value = score[i]
        if value < 5.0 or value < score[i - 2:i + 3].max():
            continue
        moment = i * PULSE_HOP / RATE
        if moment - last >= min_gap:
            out.append((moment, float(min(1.0, 0.55 + (value - 5.0) / 12.0))))
            last = moment
    return tuple(out)


def _syllables(pitch: np.ndarray, loud: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    if pitch.size == 0:
        return ()
    fps = RATE / HOP
    loud_ref = float(np.percentile(loud, 90)) + 1e-9
    out, start, anchor = [], None, 0.0
    for i in range(len(pitch) + 1):
        value = pitch[i] if i < len(pitch) else np.nan
        if start is not None and (not np.isfinite(value) or abs(value - anchor) > 1.5):
            length = (i - start) / fps
            if length >= 0.10:
                level = float(loud[start:i].mean() / loud_ref)
                out.append((start / fps, min(1.2, length), float(min(1.0, 0.30 + 0.55 * level))))
            start = None
        if start is None and np.isfinite(value):
            start, anchor = i, value
    return tuple(out)


# --- colours -------------------------------------------------------------------

PURE_HUES = (350, 30, 50, 140, 175, 205, 225, 270, 320)


def _luminance(color: QColor) -> float:
    def channel(value):
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hue_gap(a: QColor, b: QColor) -> float:
    if a.hsvHue() < 0 or b.hsvHue() < 0:
        return 0.0
    gap = abs(a.hsvHue() - b.hsvHue()) % 360
    return min(gap, 360 - gap)


def _vivid(color: QColor) -> float:
    return (color.hsvSaturationF() ** 1.2) * (0.35 + 0.65 * color.valueF())


def _purify(color: QColor) -> QColor:
    hue = color.hsvHue()
    if hue < 0:
        return QColor(color)
    if hue >= 335 or hue <= 20:
        hue = 350                      # every red reads as crimson, never orange
    else:
        nearest = min(PURE_HUES, key=lambda anchor: min(abs(anchor - hue), 360 - abs(anchor - hue)))
        if min(abs(nearest - hue), 360 - abs(nearest - hue)) <= 14:
            hue = nearest
    out = QColor()
    out.setHsvF((hue % 360) / 360.0, max(0.82, color.hsvSaturationF()), max(0.55, min(0.92, color.valueF())))
    return out


def _natural_ink(color: QColor) -> str:
    hue = color.hsvHue()
    warm = hue >= 0 and (hue >= 330 or hue <= 65) and color.hsvSaturationF() > 0.25
    return "black" if warm else ("white" if color.lightnessF() < 0.62 else "black")


def _tasteful_ink(color: QColor, want: str | None = None) -> tuple[QColor, str]:
    """The field colour adjusted for its ink, and the ink. ``want`` forces
    the ink; the colour is then deepened (white) or lightened (black)."""
    want = want or _natural_ink(color)
    target = QColor(0, 0, 0) if want == "black" else QColor(255, 255, 255)
    goal = 5.6 if want == "black" else 4.5
    adjusted = QColor(color)
    for _ in range(40):
        if _contrast(adjusted, target) >= goal:
            break
        h, s, v = max(0.0, adjusted.hsvHueF()), adjusted.hsvSaturationF(), adjusted.valueF()
        if want == "white":
            v -= 0.02
        elif v < 0.999:
            v += 0.02
        else:
            s -= 0.03
        adjusted = QColor()
        adjusted.setHsvF(h, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return adjusted, want


_ACCENTS: dict = {}


def cover_accent(image: QImage | None):
    """The most vivid colour on the cover, even a small one, as (colour, area share).

    The palette extractor drops small areas, so a little coloured logo on a
    white sleeve never reaches it. This scan keeps any vivid hue that covers
    at least 0.3% of the cover.
    """
    if image is None or image.isNull():
        return None
    key = image.cacheKey()
    if key in _ACCENTS:
        return _ACCENTS[key]
    small = image.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB888)
    raw = np.frombuffer(bytes(small.constBits()), dtype=np.uint8)
    pixels = raw.reshape(64, small.bytesPerLine())[:, :64 * 3].reshape(-1, 3).astype(np.float32) / 255.0
    high, low = pixels.max(axis=1), pixels.min(axis=1)
    sat = np.where(high > 0, (high - low) / np.maximum(high, 1e-6), 0.0)
    # Truly saturated pixels first (a red jacket beats skin tones); only a
    # cover without any falls back to softer colour.
    vivid = (sat >= 0.50) & (high >= 0.30)
    if vivid.mean() < 0.003:
        vivid = (sat >= 0.22) & (high >= 0.25)
    result = None
    if vivid.mean() >= 0.003:
        chosen = pixels[vivid]
        hues = np.array([QColor.fromRgbF(*map(float, rgb)).hsvHueF() for rgb in chosen[:2000]])
        bins = np.floor(np.clip(hues, 0.0, 0.9999) * 30).astype(int)
        counts = np.bincount(bins, minlength=30)
        best = int(np.argmax(counts + np.roll(counts, 1) + np.roll(counts, -1)))
        near = np.minimum(np.abs(bins - best), 30 - np.abs(bins - best)) <= 1
        pick = chosen[:2000][near]
        weights = (pick.max(axis=1) - pick.min(axis=1)) + 1e-3
        mean = (pick * weights[:, None]).sum(axis=0) / weights.sum()
        result = (QColor.fromRgbF(*map(float, mean)), float(vivid.mean() * near.mean()))
    if len(_ACCENTS) > 32:
        _ACCENTS.clear()
    _ACCENTS[key] = result
    return result


CHARCOAL = QColor("#161618")
PAPER = QColor("#f2f2f0")


MAIN_MIN_SHARE = 0.05        # a second real colour covers at least this much of the cover
DISTINCT_MIN = 0.16          # the change must read as a change (OKLab distance)


def _oklab(color: QColor) -> tuple[float, float, float]:
    def linear(value):
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = linear(color.red()), linear(color.green()), linear(color.blue())
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _distance(a: QColor, b: QColor) -> float:
    la, lb = _oklab(a), _oklab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def _enrich(color: QColor) -> QColor:
    """The cover's own hue, made clean enough for a field.

    Unlike ``_purify`` (still used for the broadcast face) the hue is kept:
    an orange sunset stays orange instead of becoming crimson.
    """
    out = QColor()
    hue = color.hsvHueF()
    if hue < 0:
        return QColor(color)
    out.setHsvF(hue, max(0.70, min(1.0, color.hsvSaturationF() * 1.12)),
                max(0.50, min(0.92, color.valueF())))
    return out


def _harmony(a: QColor, b: QColor) -> float:
    """How well two hues sit together, 0..1. Neighbours blend best; a split
    complement is lively; the exact opposite is allowed but not preferred;
    the 70-120 degree band tends to clash."""
    gap = _hue_gap(a, b)
    if gap < 12:
        return 0.55                      # a shade of the same colour
    if gap <= 60:
        return 1.0                       # analogous: vivid and blended
    if gap < 75:
        return 0.65
    if gap <= 120:
        return 0.20
    if gap <= 165:
        return 0.50                      # split complement
    return 0.40                          # complement


def _designed_partners(feature: QColor) -> list[QColor]:
    """Candidates built from the feature colour, each a deeper or lighter
    neighbour or counterpart so the pair is vivid and separate."""
    hue = feature.hsvHue()
    out = []
    for offset in (-40, -28, 28, 40, -150, 150, 180):
        for value in (0.42, 0.62, 0.86):
            colour = QColor()
            colour.setHsvF(((hue + offset) % 360) / 360.0, 0.80, value)
            out.append(colour)
    # A deep shade of the same colour (monochrome) for covers of one strong colour.
    deep = QColor()
    deep.setHsvF(max(0.0, feature.hsvHueF()), min(1.0, feature.hsvSaturationF() * 1.05), 0.36)
    out.append(deep)
    return out


def _pair_score(feature: QColor, partner: QColor, *, real: bool) -> float:
    distance = _distance(feature, partner)
    if distance < DISTINCT_MIN:
        return -1.0
    separation = min(1.0, distance / 0.35)
    vivid = _vivid(partner)
    # Muddy yellow-greens and dull browns never make a good field.
    hue = partner.hsvHue()
    muddy = 50 <= hue <= 95 and partner.valueF() < 0.6
    return (0.50 * _harmony(feature, partner) + 0.28 * vivid + 0.22 * separation
            + (0.12 if real else 0.0) - (0.4 if muddy else 0.0))


def pick_colors(colors, weights, image: QImage | None = None) -> tuple[QColor, str, QColor, str]:
    """(before, before_ink, after, after_ink). The cover's colour always comes last.

    Feature colour (after): the most vivid colour on the cover, however
    small, keeping its own hue.
    Partner (before): the best-scoring of the cover's other real colours
    (5% or more, coloured) and colours designed from the feature (deeper or
    lighter neighbours, split complements, a deep shade). The score values a
    pair that blends (neighbouring hues first), stays vivid and still differs
    enough for the change to show; strong contrast is not required. A real
    colour from the cover gets a small bonus, so a cover's own pairing wins
    when it is good.
    A cover with no colour at all goes from charcoal to paper white.
    Every song reads in both inks: the feature keeps its own ink and the
    partner takes the other one, so one side has black words and the other
    white. Partners that take that ink naturally are preferred.
    """
    pool = [(QColor(c), float(w)) for c, w in zip(colors, weights) if QColor(c).isValid()]
    accent = cover_accent(image)
    if accent is not None:
        pool.append(accent)
    coloured = [(c, w) for c, w in pool if c.hsvHue() >= 0 and c.hsvSaturationF() >= 0.15 and w >= 0.003]
    if not coloured:
        return QColor(CHARCOAL), "white", QColor(PAPER), "black"
    feature_raw, _feature_share = max(coloured, key=lambda item: (_vivid(item[0]), item[1]))
    feature = _enrich(feature_raw)
    candidates = [(_enrich(c), True) for c, w in coloured
                  if w >= MAIN_MIN_SHARE and c is not feature_raw and c.valueF() >= 0.30 and _vivid(c) >= 0.22]
    candidates += [(c, False) for c in _designed_partners(feature)]
    after, after_ink = _tasteful_ink(feature)
    other_ink = "white" if after_ink == "black" else "black"
    scored = [(_pair_score(feature, c, real=real), index, c) for index, (c, real) in enumerate(candidates)]
    scored = [(score + (0.30 if score > 0.0 and _natural_ink(c) == other_ink else 0.0), index, c)
              for score, index, c in scored]
    scored = [item for item in scored if item[0] > 0.0]
    if scored:
        partner = max(scored, key=lambda item: (item[0], -item[1]))[2]
    else:
        partner = QColor()
        partner.setHsvF(((feature.hsvHue() + 150) % 360) / 360.0, 0.80, 0.62)
    before, before_ink = _tasteful_ink(partner, other_ink)
    return before, before_ink, after, after_ink


def vignette(color: QColor, ink: str):
    """(centre, edge, glow position) for one field colour."""
    hue = color.hsvHue() if color.hsvHue() >= 0 else 0
    sat = color.hsvSaturationF()
    neutral = color.hsvHue() < 0 or sat < 0.12
    centre, edge = QColor(), QColor()
    if ink == "white":
        if neutral or sat < 0.5:
            # Greys and deep shades keep their own lightness; charcoal stays
            # darker than graphite instead of both becoming the same grey.
            value = color.valueF()
            centre.setHsv(hue, 0 if neutral else round(255 * sat * 0.62), round(255 * min(0.62, 0.22 + value * 0.75)))
            edge.setHsv(hue, 0 if neutral else round(255 * min(1.0, sat * 0.9)), round(255 * min(0.13, value * 0.35)))
            return centre, edge, (0.36, 0.46)
        centre.setHsv(hue, round(255 * sat * 0.62), round(255 * 0.60))
        edge.setHsv((hue + 20) % 360, round(255 * min(1.0, sat * 0.9)), round(255 * 0.13))
        return centre, edge, (0.36, 0.46)
    centre.setHsv(hue, 0 if neutral else round(255 * min(1.0, sat * 0.95)), round(255 * (0.93 if neutral else 0.78)))
    edge.setHsv(hue, 0 if neutral else round(255 * min(1.0, sat * 1.05)), round(255 * (0.62 if neutral else 0.42)))
    return centre, edge, (0.50, 0.50)


# --- plan ----------------------------------------------------------------------

# The whole change lasts 2.2 s and lands on the climax, the same growth the
# website's sea uses between songs (see change_curve).
CHANGE_LEAD_S = 0.9
COVER_S = 1.3
RAMP_S = 24.0


@dataclass
class SeaPlan:
    before: QColor
    before_ink: str
    after: QColor
    after_ink: str
    change_end: float | None = None
    drums: tuple = ()
    syllables: tuple = ()
    pushes: tuple = ()
    ready: bool = False
    word_switch: tuple[float, float] | None = None
    audio_seconds: float = 0.0
    analysed_at: float = 0.0
    attempts: int = 0
    solid: bool = False
    adopted: bool = False        # times borrowed from another recording, no file here
    change_source: str = ""      # netease / lyrics / audio / energy, see local.sea_climax
    climax: dict | None = None   # what the change was picked from, to pick again with a late lyric
    lyrics_seen: bool = False
    captured: bool = False       # no file here: change from lyric/NetEase, beats from live audio
    settled: bool = False        # a captured plan has had its chance to find a change
    created_at: float = 0.0

    @property
    def change_start(self) -> float | None:
        return None if self.change_end is None else self.change_end - COVER_S


# How much of the page the new colour covers over the change window, 0..1:
# CSS cubic-bezier(.6, 0, .3, 1), the website's sea transition. A held
# breath, one fast sweep, a short settle on the climax.
CHANGE_BEZIER = (0.6, 0.0, 0.3, 1.0)


def _bezier(a: float, b: float, u: float) -> float:
    return 3.0 * a * u * (1.0 - u) ** 2 + 3.0 * b * u * u * (1.0 - u) + u ** 3


def change_curve(x: float) -> float:
    x = max(0.0, min(1.0, float(x)))
    if x <= 0.0 or x >= 1.0:
        return x
    x1, y1, x2, y2 = CHANGE_BEZIER
    low, high = 0.0, 1.0
    for _ in range(30):
        mid = (low + high) * 0.5
        low, high = (mid, high) if _bezier(x1, x2, mid) < x else (low, mid)
    return _bezier(y1, y2, (low + high) * 0.5)


def push_schedule(start: float, end: float):
    begin = start - CHANGE_LEAD_S
    span = max(1e-6, end - begin)
    times, moment = [], begin
    # Gaps shrink geometrically to 0.12 s; a short change starts at a quarter
    # of its span instead of 0.8 s so it still has several steps.
    first = min(0.80, span * 0.25)
    while moment < end:
        times.append(moment)
        moment += first * (0.12 / first) ** ((moment - begin) / span)
    # Each push carries the coverage the curve gains until the next push,
    # so the summed pushes trace change_curve (each eases in over 0.18 s).
    edges = [(t - begin) / span for t in times] + [1.0]
    weights = [max(0.0, change_curve(b) - change_curve(a)) for a, b in zip(edges, edges[1:])]
    total = sum(weights) or 1.0
    return tuple((t, w / total) for t, w in zip(times, weights))


# The new colour grows as a circle centred below the bottom edge, as on the
# website: radial-gradient(circle at 50% 112%, #000 calc(grow - 55%),
# transparent grow); the page is fully turned exactly on the climax.
CHANGE_ORIGIN_Y = 1.12          # in heights from the top
CHANGE_FEATHER = 0.55           # soft edge, in farthest-corner distances
CHANGE_GROW = 1.55              # outer edge at full cover: the inner end of the edge
                                # just reaches the far corners on the climax


def change_geometry(width: float, height: float, covered: float):
    """(centre, radius, feather, full) of the growing colour ball.

    ``radius`` is the middle of the soft edge, ``full`` the farthest-corner
    distance the website's percentages refer to.
    """
    centre = QPointF(width * 0.5, height * CHANGE_ORIGIN_Y)
    full = math.hypot(width * 0.5, height * CHANGE_ORIGIN_Y)
    feather = full * CHANGE_FEATHER
    outer = full * CHANGE_GROW * max(0.0, min(1.0, covered))
    return centre, outer - feather * 0.5, feather, full


# The website's circle is round from the start.
CHANGE_START_STRETCH = 1.0


def change_stretch(covered: float) -> float:
    covered = max(0.0, min(1.0, float(covered)))
    return 1.0 + (CHANGE_START_STRETCH - 1.0) * (1.0 - covered) ** 1.5


def coverage_for_height(fraction_from_top: float, aspect: float = 16.0 / 9.0) -> float:
    """Coverage at which the ball's soft edge has passed a height on screen."""
    width, height = aspect, 1.0
    _centre, _radius, feather, full = change_geometry(width, height, 1.0)
    reach = (CHANGE_ORIGIN_Y - fraction_from_top) * height
    # The middle of the soft edge has passed that height.
    return max(0.0, min(1.0, (reach + feather * 0.5) / (full * CHANGE_GROW)))


# The words sit around the middle of the page (about 30%-60% from the top):
# their ink starts to change as the ball's edge reaches the lower lines and
# finishes as it clears the upper ones.
WORD_SWITCH_LEVELS = (coverage_for_height(0.62), coverage_for_height(0.28))


def change_progress(plan: "SeaPlan", t: float) -> float:
    """0..1 through the change window (build, sweep, settle), or -1 outside it."""
    end, start = plan.change_end, plan.change_start
    if end is None or start is None:
        return -1.0
    begin = start - CHANGE_LEAD_S
    if t < begin or t >= end:
        return -1.0
    return (t - begin) / max(1e-6, end - begin)


def word_switch_times(change_end: float) -> tuple[float, float]:
    """When the growing colour passes the lower and the upper lyric lines."""
    begin = change_end - COVER_S - CHANGE_LEAD_S
    span = change_end - begin
    times = []
    for level in WORD_SWITCH_LEVELS:
        low, high = 0.0, 1.0
        for _ in range(40):
            mid = (low + high) / 2.0
            low, high = (mid, high) if change_curve(mid) < level else (low, mid)
        times.append(begin + high * span)
    return times[0], times[1]


def coverage_at(t: float, pushes) -> float:
    covered = 0.0
    for moment, share in pushes:
        elapsed = t - moment
        if elapsed <= 0.0:
            break
        phase = min(1.0, elapsed / 0.18)
        covered += share * (1.0 - (1.0 - phase) ** 3)
    return min(1.0, covered)


def _gain(moment: float, change_end: float | None) -> float:
    if change_end is None:
        return 0.85
    ramp_from = change_end - RAMP_S
    if moment < ramp_from:
        return 0.70
    if moment < change_end:
        return 0.70 + 0.30 * ((moment - ramp_from) / RAMP_S) ** 1.5
    return 0.85


@dataclass(frozen=True)
class ClimaxHints:
    """What besides the audio says where the chorus is."""
    lyrics: tuple = ()               # (start seconds, text) timed lines
    netease: float | None = None     # NetEase's chorus start, seconds


def usable_lyrics(lyrics, audio_seconds: float) -> tuple:
    """The timed lines as (start, text), or () when they cannot be this
    recording's (a stale lyric from the previous song runs past its end)."""
    out = []
    for item in lyrics or ():
        try:
            out.append((float(item[0]), str(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(out) < 4 or (audio_seconds > 0 and max(start for start, _text in out) > audio_seconds + 5.0):
        return ()
    return tuple(out)


def analyse(pcm: np.ndarray, plan: SeaPlan, hints: ClimaxHints | None = None) -> SeaPlan:
    from local import sea_climax
    hints = hints or ClimaxHints()
    onsets = _drums(pcm)                    # every hit, for the arrangement density
    pitch, loud = _melody(pcm)
    syllables = _syllables(pitch, loud)
    lyrics = usable_lyrics(hints.lyrics, pcm.size / RATE)
    found = sea_climax.candidates(pcm, onsets, lyrics=lyrics, netease=hints.netease)
    change, source = sea_climax.choose(found, onsets)
    shown, last = [], -1.0                  # on screen, at most one hit per 0.28 s
    for moment, strength in onsets:
        if strength >= 0.38 and moment - last >= 0.28:
            shown.append((moment, strength))
            last = moment
    plan.climax = {key: found[key] for key in ("netease", "audio", "full", "duration")}
    plan.climax.update(onsets=onsets, drums=tuple(shown), syllables=syllables)
    plan.lyrics_seen = bool(lyrics)
    _apply_change(plan, change, source)
    plan.ready = True
    return plan


def _apply_change(plan: SeaPlan, change: float | None, source: str) -> None:
    """Place the change and scale the light around it (quieter before)."""
    raw = plan.climax or {}
    plan.change_end, plan.change_source = change, source
    plan.drums = tuple((moment, min(1.0, strength * _gain(moment, change)))
                       for moment, strength in raw.get("drums", ()))
    plan.syllables = tuple((a, b, min(1.0, s * _gain(a, change))) for a, b, s in raw.get("syllables", ()))
    if change is not None:
        _schedule_change(plan)
    else:
        plan.pushes, plan.word_switch = (), None


CAPTURED_SETTLE_S = 8.0          # how long a captured song may wait for its lyric before falling back


def settle_captured(plan: SeaPlan, lyrics, position: float | None = None, now: float | None = None) -> None:
    """Pick a captured song's change from its lyric and NetEase's chorus
    (no audio file to analyse). Moves the change only before it can show."""
    from local import sea_climax
    now = time.monotonic() if now is None else now
    raw = plan.climax or {}
    lines = usable_lyrics(lyrics, plan.audio_seconds)
    signature = (len(lines), lines[0][0] if lines else None, raw.get("netease"), raw.get("chorus_done", False))
    if signature != raw.get("signature"):
        raw["signature"] = signature
        duration = plan.audio_seconds or (lines[-1][0] + 20.0 if lines else 0.0)
        section = sea_climax.lyric_section(lines, duration) if lines else None
        found = dict(raw, section=section, lyrics=None if section is None else section["pick"],
                     audio=None, full=np.zeros(0))
        change, source = sea_climax.choose(found, ())
        if source == "energy":
            change, source = None, "none"
        old = plan.change_end
        guard = COVER_S + CHANGE_LEAD_S + 2.0
        movable = old is None or position is None or position < min(old, change if change is not None else old) - guard
        if movable and change != old:
            plan.change_end, plan.change_source = change, source
            if change is not None:
                _schedule_change(plan)
            else:
                plan.pushes, plan.word_switch = (), None
    chorus_done = raw.get("chorus_done", True)
    waited = now - plan.created_at >= CAPTURED_SETTLE_S
    plan.settled = bool(chorus_done and (plan.change_end is not None or waited))


class LiveBeats:
    """Drum hits from the live audio's low band, placed on the song's clock.

    A hit is a rise of the low band well above its recent level; hits are
    at least 0.28 s apart, like the analysed ones, and quieter before the
    change.
    """

    MIN_GAP = 0.28
    KEEP = 64

    def __init__(self):
        self.level = 0.0
        self.last = -1e9
        self.previous_position = None

    def reset(self):
        self.level, self.last, self.previous_position = 0.0, -1e9, None

    def feed(self, plan: SeaPlan, low: float, position: float) -> bool:
        low = max(0.0, float(low or 0.0))
        if self.previous_position is not None and position < self.previous_position - 1.0:
            # A seek back: hits ahead of the new position are gone.
            plan.drums = tuple(hit for hit in plan.drums if hit[0] <= position)
            self.last = -1e9
        self.previous_position = position
        level = self.level
        self.level = low if level <= 0.0 else level * 0.92 + low * 0.08
        if level <= 0.0 or low < 0.12 or low < level * 1.45 or position - self.last < self.MIN_GAP:
            return False
        self.last = position
        strength = min(1.0, 0.45 + (low - level) / max(level, 1e-3) * 0.35)
        hit = (position, min(1.0, strength * _gain(position, plan.change_end)))
        plan.drums = (plan.drums + (hit,))[-self.KEEP:]
        return True


def refine_with_lyrics(plan: SeaPlan, lyrics, position: float | None = None) -> bool:
    """Pick the change again once a lyric arrives after the analysis.

    Only before either change (old or new) has begun to show, so the colour
    never jumps back. True when the change moved.
    """
    from local import sea_climax
    if plan.climax is None or plan.lyrics_seen or not plan.ready or plan.adopted:
        return False
    lines = usable_lyrics(lyrics, plan.audio_seconds)
    if not lines:
        return False
    plan.lyrics_seen = True
    section = sea_climax.lyric_section(lines, plan.climax.get("duration", plan.audio_seconds))
    if section is None:
        return False
    found = dict(plan.climax, section=section, lyrics=section["pick"])
    change, source = sea_climax.choose(found, plan.climax.get("onsets", ()))
    old = plan.change_end
    if change is None or (old is not None and abs(change - old) < 0.75):
        return False
    guard = COVER_S + CHANGE_LEAD_S + 2.0
    if position is not None and position > min(change, old if old is not None else change) - guard:
        return False
    _apply_change(plan, change, source)
    _log.info("sea stage=climax result=moved source=%s change=%.1f", source, change)
    return True


class SeaAnalyzer(QObject):
    """One analysis per audio file, on a worker thread. Results are cached."""

    finished = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._plans: dict[str, SeaPlan] = {}
        self._hints: dict[str, tuple] = {}
        self._threads: dict[str, _DecodeThread] = {}
        self._lock = threading.Lock()

    def plan(self, path: str | None, colors, weights, image: QImage | None = None,
             duration: float = 0.0, hints: tuple | None = None, position: float | None = None) -> SeaPlan:
        """``hints`` is (track id, timed lyric) for the chorus search; the
        analysis reads the latest ones when it finishes."""
        before, before_ink, after, after_ink = pick_colors(colors, weights, image)
        if not path:
            return SeaPlan(before, before_ink, after, after_ink)
        key = path
        with self._lock:
            plan = self._plans.get(key)
            if hints is not None:
                self._hints[key] = hints
        if plan is not None:
            plan.before, plan.before_ink, plan.after, plan.after_ink = before, before_ink, after, after_ink
            if hints is not None and hints[1] and not plan.lyrics_seen and key not in self._threads:
                refine_with_lyrics(plan, hints[1], position)
            # The first song can be analysed while its file is still arriving.
            # A clearly short decode is tried again a few seconds later.
            if (plan.ready and not plan.adopted and duration > 0.0 and plan.audio_seconds < duration - 3.0
                    and plan.attempts < 6 and key not in self._threads
                    and time.monotonic() - plan.analysed_at > 4.0):
                _log.info("sea stage=decode result=partial audio_s=%.0f duration_s=%.0f retry=%d",
                          plan.audio_seconds, duration, plan.attempts)
                self._start(key, plan)
            return plan
        plan = SeaPlan(before, before_ink, after, after_ink)
        _log.info("sea stage=plan result=begin before=%s/%s after=%s/%s",
                  before.name(), before_ink, after.name(), after_ink)
        with self._lock:
            self._plans[key] = plan
            if len(self._plans) > 12:
                oldest = next(iter(self._plans))
                self._plans.pop(oldest)
                self._hints.pop(oldest, None)
        self._start(key, plan)
        return plan

    def captured_plan(self, key: str, colors, weights, image: QImage | None = None, duration: float = 0.0,
                      hints: tuple | None = None, position: float | None = None) -> SeaPlan:
        """A song with no audio file here (system capture, an Apple song
        without a twin): its colour change comes from the lyric and NetEase's
        chorus, its drum light from the live audio (see ``LiveBeats``).

        ``hints`` is (track id, timed lyric, source app). ``plan.settled``
        with no ``change_end`` means none could be found: the caller falls
        back to the solid field.
        """
        before, before_ink, after, after_ink = pick_colors(colors, weights, image)
        start = False
        with self._lock:
            plan = self._plans.get(key)
            if plan is None or not plan.captured:
                plan = SeaPlan(before, before_ink, after, after_ink, ready=True, captured=True,
                               created_at=time.monotonic())
                plan.audio_seconds = float(duration or 0.0)
                plan.climax = {"netease": None, "audio": None, "duration": plan.audio_seconds,
                               "onsets": (), "drums": (), "syllables": (), "chorus_done": True}
                self._plans[key] = plan
                if len(self._plans) > 12:
                    oldest = next(iter(self._plans))
                    self._plans.pop(oldest)
                    self._hints.pop(oldest, None)
                start = True
        plan.before, plan.before_ink, plan.after, plan.after_ink = before, before_ink, after, after_ink
        if duration and not plan.audio_seconds:
            plan.audio_seconds = plan.climax["duration"] = float(duration)
        track_id, lyrics, app = (tuple(hints) + (None, (), ""))[:3] if hints else ("", (), "")
        if start:
            from api.motion_cover import netease_song_id
            ident = netease_song_id(str(track_id or ""), str(app or ""))
            if ident:
                plan.climax["chorus_done"] = False

                def lookup(plan=plan, ident=ident):
                    try:
                        from api import netease_chorus
                        plan.climax["netease"] = netease_chorus.chorus_start(ident)
                    except Exception:
                        pass
                    plan.climax["chorus_done"] = True
                threading.Thread(target=lookup, name="GluSpotSeaChorus", daemon=True).start()
        settle_captured(plan, lyrics, position)
        return plan

    def adopt(self, key: str, data: dict) -> None:
        """Use an analysis made elsewhere (an Apple song's borrowed twin)."""
        plan = SeaPlan(QColor(), "white", QColor(), "white")
        change = data.get("change_end")
        plan.change_end = float(change) if isinstance(change, (int, float)) else None
        plan.drums = tuple((float(a), float(b)) for a, b in data.get("drums") or ())
        plan.syllables = tuple((float(a), float(b), float(c)) for a, b, c in data.get("syllables") or ())
        plan.pushes = tuple((float(a), float(b)) for a, b in data.get("pushes") or ())
        switch = data.get("word_switch")
        plan.word_switch = (float(switch[0]), float(switch[1])) if switch else None
        _schedule_change(plan)
        plan.audio_seconds = float(data.get("audio_seconds") or 0.0)
        plan.analysed_at = time.monotonic()
        plan.ready = plan.adopted = True
        with self._lock:
            self._plans[key] = plan
            if len(self._plans) > 12:
                self._plans.pop(next(iter(self._plans)))
        self.finished.emit(key)

    def has_plan(self, key: str) -> bool:
        with self._lock:
            return key in self._plans

    def _start(self, key: str, plan: SeaPlan) -> None:
        plan.attempts += 1
        worker = _AnalyseThread(key, plan, lambda key=key: self._latest_hints(key))
        worker.finished.connect(lambda key=key: self._done(key))
        self._threads[key] = worker
        worker.start(QThread.Priority.LowPriority)

    def _latest_hints(self, key: str) -> tuple | None:
        with self._lock:
            return self._hints.get(key)

    def _done(self, key: str) -> None:
        worker = self._threads.pop(key, None)
        if worker is not None:
            worker.wait(100)
            worker.deleteLater()
        self.finished.emit(key)

    def shutdown(self) -> None:
        for worker in list(self._threads.values()):
            worker.requestInterruption()
            worker.quit()
            worker.wait(2000)
        self._threads.clear()


class _AnalyseThread(_DecodeThread):
    def __init__(self, path: str, plan: SeaPlan, hints=lambda: None) -> None:
        super().__init__(path)
        self.plan = plan
        self.hints = hints

    def _climax_hints(self) -> ClimaxHints:
        """The latest lyric, and NetEase's chorus time for a NetEase song."""
        try:
            latest = self.hints()
        except Exception:
            latest = None
        if not latest:
            return ClimaxHints()
        track_id, lyrics = latest
        netease = None
        from api import netease_chorus
        ident = netease_chorus.song_id(track_id)
        if ident and not self.isInterruptionRequested():
            netease = netease_chorus.chorus_start(ident)
        return ClimaxHints(tuple(lyrics or ()), netease)

    def run(self) -> None:
        began = time.monotonic()
        super().run()
        seconds = 0.0 if self.pcm is None else self.pcm.size / RATE
        self.plan.audio_seconds = seconds
        self.plan.analysed_at = time.monotonic()
        if self.pcm is not None and self.pcm.size >= RATE * 30 and not self.isInterruptionRequested():
            try:
                analyse(self.pcm, self.plan, self._climax_hints())
                change = self.plan.change_end
                _log.info("sea stage=analysis result=ok audio_s=%.0f ms=%d change=%s source=%s drums=%d syllables=%d",
                          seconds, (time.monotonic() - began) * 1000,
                          "none" if change is None else f"{change:.1f}", self.plan.change_source or "none",
                          len(self.plan.drums), len(self.plan.syllables))
            except Exception as error:
                _log.info("sea stage=analysis result=error kind=%s", type(error).__name__)
                self.plan.ready = True
        else:
            _log.info("sea stage=decode result=short audio_s=%.1f", seconds)
            self.plan.ready = True
        self.pcm = None


# --- painting --------------------------------------------------------------------

_VIGNETTES: dict = {}


def _with_alpha(color: QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _vignette_image(width: int, height: int, color: QColor, ink: str) -> QImage:
    key = (width, height, QColor(color).rgb(), ink)
    cached = _VIGNETTES.get(key)
    if cached is None:
        if len(_VIGNETTES) > 8:
            _VIGNETTES.clear()
        centre_color, edge_color, (gx, gy) = vignette(color, ink)
        cached = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        cached.fill(edge_color)
        painter = QPainter(cached)
        painter.setPen(Qt.PenStyle.NoPen)
        centre = QPointF(width * gx, height * gy)
        gradient = QRadialGradient(centre, math.hypot(width, height) * 0.62)
        gradient.setColorAt(0.0, centre_color)
        gradient.setColorAt(0.55, _mix(centre_color, edge_color, 0.55))
        gradient.setColorAt(1.0, edge_color)
        painter.setBrush(gradient)
        painter.drawRect(0, 0, width, height)
        painter.end()
        _VIGNETTES[key] = cached
    return QImage(cached)


def _lifted(color: QColor) -> QColor:
    out = QColor()
    headroom = 1.0 - color.valueF()
    whiten = max(0.0, 0.32 - headroom) * 1.4
    out.setHsvF(max(0.0, color.hsvHueF()), max(0.0, color.hsvSaturationF() * (1.0 - whiten)),
                min(1.0, color.valueF() + 0.32))
    return out


def word_ink(plan: SeaPlan, t: float) -> str:
    """Which ink the words use at ``t``: the after ink once the cover passes them."""
    if plan.change_end is None or plan.word_switch is None:
        return plan.before_ink
    middle = (plan.word_switch[0] + plan.word_switch[1]) / 2.0
    return plan.after_ink if t >= middle else plan.before_ink


WORD_BLEND_MIN_S = 1.5


def word_ink_mix(plan: SeaPlan, t: float) -> float:
    """0 = before ink, 1 = after ink, eased across the cover reaching the words.

    The blend spans the push window over the lyric area and lasts at least
    1.5 s, so the words fade from one ink to the other instead of flipping.
    """
    if plan.change_end is None:
        return 0.0
    if plan.word_switch is None:
        return 0.0 if t < plan.change_end else 1.0
    # Same ink before and after still blends over the window: the unread
    # words carry the field's hue, which must fade with the colour, not snap.
    start, end = plan.word_switch
    end = max(end, start + WORD_BLEND_MIN_S)
    if t <= start:
        return 0.0
    if t >= end:
        return 1.0
    x = (t - start) / (end - start)
    return x * x * (3.0 - 2.0 * x)


def _schedule_change(plan: SeaPlan) -> None:
    """Pushes and word timing follow the current change curve and window,
    also for plans analysed (or stored) under an older timing."""
    if plan.change_end is None:
        return
    plan.pushes = push_schedule(plan.change_end - COVER_S, plan.change_end)
    plan.word_switch = word_switch_times(plan.change_end)


def render(width: int, height: int, plan: SeaPlan, t: float, playing: bool = True) -> QImage:
    """One frame of the field at playback time ``t``, ``width`` x ``height`` (small)."""
    width, height = max(2, int(width)), max(2, int(height))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    end = plan.change_end
    start = plan.change_start
    after_now = end is not None and t >= end
    painter.drawImage(0, 0, _vignette_image(width, height, plan.after if after_now else plan.before,
                                            plan.after_ink if after_now else plan.before_ink))
    far = math.hypot(width, height) * 0.5
    progress = change_progress(plan, t)
    if progress >= 0.0:
        # The colour grows continuously along the curve: no stepped pushes,
        # so nothing flickers while it spreads.
        covered = change_curve(progress)
        centre, radius, feather, full = change_geometry(width, height, covered)
        total = max(1.0, radius + feather * 0.5)
        if covered > 0.0005:
            # Full inside, then the website's linear fade across the edge.
            fresh = _vignette_image(width, height, plan.after, plan.after_ink)
            mask = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            mask.fill(Qt.GlobalColor.transparent)
            mp = QPainter(mask)
            mp.setPen(Qt.PenStyle.NoPen)
            cover = QRadialGradient(centre, total)
            for step in range(25):
                at = step / 24.0
                x = (at * total - (radius - feather * 0.5)) / feather
                x = max(0.0, min(1.0, x))
                cover.setColorAt(at, QColor(0, 0, 0, round(255 * (1.0 - x))))
            mp.setBrush(cover)
            stretch = change_stretch(covered)
            mp.translate(centre)
            mp.scale(stretch, 1.0)
            mp.translate(-centre)
            mp.drawRect(QRectF(centre.x() - width / (2.0 * stretch) - 1.0, 0.0,
                               width / stretch + 2.0, float(height)))
            mp.end()
            fp = QPainter(fresh)
            fp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            fp.drawImage(0, 0, mask)
            fp.end()
            painter.drawImage(0, 0, fresh)
        # A dark, steady band rides the spreading edge, strongest mid-sweep:
        # the change reads as a wave of shade sweeping the old colour away,
        # instead of a train of white flashes.
        level = math.sin(math.pi * min(1.0, progress / 0.97)) ** 2
        if level > 0.01:
            glow = _mix(plan.after, QColor(0, 0, 0), 0.62)
            band = far * 0.50
            reach = max(1.0, radius + feather * 0.5 + band * 2.0)
            ring = QRadialGradient(centre, reach)
            for step in range(25):
                at = step / 24.0
                offset = (at * reach - radius) / band
                ring.setColorAt(at, _with_alpha(glow, 0.55 * level * math.exp(-offset * offset)))
            painter.setBrush(ring)
            stretch = change_stretch(covered)
            painter.save()
            painter.translate(centre)
            painter.scale(stretch, 1.0)
            painter.translate(-centre)
            painter.drawRect(QRectF(centre.x() - width / (2.0 * stretch) - 1.0, 0.0,
                                    width / stretch + 2.0, float(height)))
            painter.restore()
    if playing:
        _paint_landing(painter, width, height, plan, t)
        _paint_lights(painter, width, height, plan, t)
    painter.end()
    return image


LANDING_S = 0.75       # the shade the new colour breaks through at the climax
LANDING_PEAK = 0.42


def landing_level(plan: SeaPlan, t: float) -> float:
    """0..1 bloom right after the change completes: a 60 ms rise, then a fade."""
    end = plan.change_end
    if end is None or plan.solid:
        return 0.0
    elapsed = t - end
    if elapsed < 0.0 or elapsed >= LANDING_S:
        return 0.0
    rise = min(1.0, elapsed / 0.06)
    fade = (1.0 - elapsed / LANDING_S) ** 2
    return rise * fade


def _paint_landing(painter: QPainter, width: int, height: int, plan: SeaPlan, t: float) -> None:
    level = landing_level(plan, t)
    if level <= 0.005:
        return
    # Shade from the edges inward, clear at the bottom centre where the new
    # colour rose: the colour seems to burst out of a darkening page.
    shade = _mix(plan.after, QColor(0, 0, 0), 0.85)
    centre = QPointF(width * 0.5, height * CHANGE_ORIGIN_Y)
    reach = math.hypot(width * 0.5, height * CHANGE_ORIGIN_Y) * 1.15
    bloom = QRadialGradient(centre, reach)
    bloom.setColorAt(0.0, _with_alpha(shade, 0.0))
    bloom.setColorAt(0.45, _with_alpha(shade, LANDING_PEAK * 0.55 * level))
    bloom.setColorAt(1.0, _with_alpha(shade, LANDING_PEAK * level))
    painter.save()
    painter.setBrush(bloom)
    painter.drawRect(QRectF(0.0, 0.0, float(width), float(height)))
    painter.restore()


DRUM_MIN = 0.70        # only hits with weight flash
DRUM_LIFE = 0.26       # a drum flash is short: it lands on the hit and is gone
VOICE_MIN = 0.78       # only strong, clearly sung notes
VOICE_MIN_LENGTH = 0.45
VOICE_SHARE = 0.40     # a sung note is a faint glow next to a drum
DRUM_HOLDS_VOICE = 0.30  # after a drum, notes stay dark this long


def _paint_lights(painter: QPainter, width: int, height: int, plan: SeaPlan, t: float) -> None:
    """One light source, rising from the bottom edge, for drums and sung notes alike.

    The light is white. Only during the colour change (the 14 s of pushes)
    does it take the new colour.
    """
    end, start = plan.change_end, plan.change_start
    changing = start is not None and end is not None and start - CHANGE_LEAD_S <= t < end
    light = _mix(plan.after, QColor(255, 255, 255), 0.55) if changing else QColor(255, 255, 255)
    level = light_level(plan, t)
    _paint_light_level(painter, width, height, light, level)


def light_level(plan: SeaPlan, t: float) -> float:
    """0..1 light for this moment. Drums come first.

    A drum flash rises in 20 ms and is gone in 0.26 s, so the light lands on
    the beat. A sung note only glows (at 40%) when no drum hit in the last
    0.3 s, and only long, strong notes count.
    """
    drum, last_drum = 0.0, None
    for moment, strength in _recent(plan.drums, t, DRUM_HOLDS_VOICE):
        if strength < DRUM_MIN:
            continue
        last_drum = moment
        elapsed = t - moment
        if elapsed < DRUM_LIFE:
            drum = max(drum, strength * min(1.0, elapsed / 0.02) * (1.0 - elapsed / DRUM_LIFE) ** 2.0)
    if last_drum is not None:
        return drum
    voice = 0.0
    for begin, length, strength in _recent_notes(plan.syllables, t):
        if strength < VOICE_MIN or length < VOICE_MIN_LENGTH:
            continue
        elapsed = t - begin
        if elapsed <= length:
            level = min(1.0, elapsed / 0.04) * (1.0 - 0.25 * elapsed / max(length, 1e-3))
        else:
            level = 0.75 * max(0.0, 1.0 - (elapsed - length) / 0.12)
        voice = max(voice, strength * level)
    return voice * VOICE_SHARE


def _paint_light_level(painter: QPainter, width: int, height: int, light: QColor, level: float) -> None:
    if level <= 0.01:
        return
    # The source sits just below the bottom edge, centred: a low, flat glow
    # over the lower third, not a wash over the whole page.
    # In a tall, narrow window the glow is kept low and wide: its height is
    # capped by the width, and it always spans more than the window, so a
    # flash never reads as a narrow column (or as the page shrinking).
    reach = min(height * (0.40 + 0.06 * level), width * 0.50)
    across = max(width * 0.42, min(width * 0.70, reach * 1.25))
    spread = across / max(1.0, reach)
    centre = QPointF(width * 0.5, height * 1.08)
    gradient = QRadialGradient(centre, reach)
    gradient.setColorAt(0.0, _with_alpha(light, 0.62 * level))
    gradient.setColorAt(0.40, _with_alpha(light, 0.26 * level))
    gradient.setColorAt(1.0, _with_alpha(light, 0.0))
    painter.setBrush(gradient)
    painter.save()
    painter.translate(centre)
    painter.scale(spread, 1.0)
    painter.translate(-centre)
    painter.drawEllipse(centre, reach, reach)
    painter.restore()


def _recent(hits, t: float, life: float):
    if not hits:
        return ()
    import bisect
    times = [moment for moment, _strength in hits]
    last = bisect.bisect_right(times, t)
    first = bisect.bisect_left(times, t - life)
    return hits[first:last]


def _recent_notes(notes, t: float):
    if not notes:
        return ()
    import bisect
    times = [begin for begin, _length, _strength in notes]
    last = bisect.bisect_right(times, t)
    first = bisect.bisect_left(times, t - 1.32)
    return tuple(note for note in notes[first:last] if t - note[0] <= note[1] + 0.12)


# --- shared with the island ----------------------------------------------------

_shared_analyzer = None
_island_palettes: dict = {}


def shared_analyzer() -> "SeaAnalyzer":
    """One analysis per song for the lyric page and the island alike."""
    global _shared_analyzer
    if _shared_analyzer is None:
        _shared_analyzer = SeaAnalyzer()
    return _shared_analyzer


ISLAND_CHANGE_S = 1.5


def island_change_window(plan: SeaPlan) -> tuple[float, float] | None:
    """When the island changes colour: 1.5 s, centred on the moment the
    page's rising colour crosses the lyrics (the words change ink then)."""
    if plan.change_end is None:
        return None
    if plan.word_switch is not None:
        middle = (plan.word_switch[0] + plan.word_switch[1]) / 2.0
    else:
        middle = plan.change_end - COVER_S / 2.0
    return middle - ISLAND_CHANGE_S / 2.0, middle + ISLAND_CHANGE_S / 2.0


def ease_in_out(x: float) -> float:
    """Slow start, quick middle, slow end (cubic)."""
    x = max(0.0, min(1.0, float(x)))
    return 4.0 * x * x * x if x < 0.5 else 1.0 - (-2.0 * x + 2.0) ** 3 / 2.0


def island_tint(track_id: str, position: float, duration: float, image: QImage | None):
    """(before, after, front) for the island's sea glow, or None.

    front is 0 before the change and 1 after it. The island changes in one
    smooth 1.5 s sweep (slow, quick, slow) while the page's colour crosses
    the lyrics, not in the page's 14 s of stepped pushes.
    """
    source = current_audio_source()
    path = source[1] if source and source[0] == str(track_id or "") and source[1] else None
    if not path or image is None or image.isNull():
        return None
    analyzer = shared_analyzer()
    with analyzer._lock:
        plan = analyzer._plans.get(path)
    if plan is None:
        key = image.cacheKey()
        palette = _island_palettes.get(key)
        if palette is None:
            from local.music_surface_palette import artwork_surface_palette
            palette = artwork_surface_palette(image, QColor(44, 44, 44), QColor(14, 14, 14))
            if len(_island_palettes) > 16:
                _island_palettes.clear()
            _island_palettes[key] = palette
        plan = analyzer.plan(path, palette.ambient_colors, palette.ambient_weights, image, duration)
    window = island_change_window(plan)
    if window is None or position < window[0]:
        return QColor(plan.before), QColor(plan.after), 0.0
    if position >= window[1]:
        return QColor(plan.before), QColor(plan.after), 1.0
    return QColor(plan.before), QColor(plan.after), change_curve((position - window[0]) / ISLAND_CHANGE_S)



def is_moving(plan: SeaPlan, t: float, playing: bool) -> bool:
    """True only while something on the field actually changes.

    Most of a song the sea field is still, and the page can keep its cheap
    partial repaints. It moves for a visible drum or sung-note light, during
    the colour change, and while the words fade between inks.
    """
    if not playing:
        return False
    end, start = plan.change_end, plan.change_start
    if end is not None and start is not None and start - CHANGE_LEAD_S <= t < end + LANDING_S:
        return True
    if plan.word_switch is not None:
        blend_start, blend_end = plan.word_switch
        if blend_start <= t <= max(blend_end, blend_start + WORD_BLEND_MIN_S) + 0.1:
            return True
    return light_level(plan, t) > 0.005


def field_key(plan: SeaPlan, t: float):
    """Identity of the field without its light, or None while it changes.

    The field (vignette and the spreading new colour) only moves inside the
    change window. Outside it the same image can be reused every frame and
    only the drum/syllable light is painted on top.
    """
    end, start = plan.change_end, plan.change_start
    if end is not None and start is not None and start - CHANGE_LEAD_S <= t < end:
        return None
    return still_key(plan, t)


def add_lights(image: QImage, plan: SeaPlan, t: float) -> None:
    """Paint the drum/syllable light onto a copy of the still field."""
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _paint_landing(painter, image.width(), image.height(), plan, t)
    _paint_lights(painter, image.width(), image.height(), plan, t)
    painter.end()


def still_key(plan: SeaPlan, t: float) -> tuple:
    """Identity of a still field: which colour is up and the plan's colours."""
    after = plan.change_end is not None and t >= plan.change_end
    return (id(plan), after, QColor(plan.before).rgb(), QColor(plan.after).rgb(),
            plan.before_ink, plan.after_ink, plan.change_end)



def solid_plan(colors, weights, image: QImage | None = None) -> SeaPlan:
    """The plain field: the cover's own colour, flat, with no light and no change.

    It is the "纯色背景" style's lyric page, and sea level's fallback for an
    instrumental track. The colour is the one sea level ends on.
    """
    _before, _before_ink, after, after_ink = pick_colors(colors, weights, image)
    plan = SeaPlan(QColor(after), after_ink, QColor(after), after_ink)
    plan.ready = True
    plan.solid = True
    return plan


def render_solid(width: int, height: int, plan: SeaPlan) -> QImage:
    image = QImage(max(2, int(width)), max(2, int(height)), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(plan.after))
    return image

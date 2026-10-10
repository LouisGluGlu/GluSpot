from __future__ import annotations

from dataclasses import dataclass
import math
import unicodedata

from PySide6.QtGui import QColor

from api.lyric_timeline import active_index_for_position


GLYPH_PULSE_LEAD = 0.0
GLYPH_PULSE_SPAN = 1.80
GLYPH_FLOAT_LIFT = 5.2
# Positional sampling targets for the ordinary rise: 16 bands at the original
# height, 8 at half height and 4 at quarter height, with 10 samples per band.
# The analytic wave below is continuous, so it can be sampled more finely by
# a fast display without quantizing motion or inventing extra media time.
READING_LIFT_FULL_BANDS = 16
READING_LIFT_SAMPLES_PER_BAND = 10
SPARSE_MIN_SECONDS_PER_UNIT = 1.5
SPARSE_MIN_SECONDS_PER_UNIT_SETTING = 0.8
SPARSE_MAX_SECONDS_PER_UNIT_SETTING = 2.0


def normalize_sparse_seconds_per_unit(value: object) -> float:
    """Keep persisted and runtime sparse-lyric thresholds in the supported range."""
    if isinstance(value, bool):
        return SPARSE_MIN_SECONDS_PER_UNIT
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return SPARSE_MIN_SECONDS_PER_UNIT
    if not math.isfinite(seconds):
        return SPARSE_MIN_SECONDS_PER_UNIT
    return max(SPARSE_MIN_SECONDS_PER_UNIT_SETTING, min(SPARSE_MAX_SECONDS_PER_UNIT_SETTING, seconds))

# Five spatial samples of one continuous, left-to-right sparse lyric wave.
# Level zero is the resting size with no *extra* shine; read text may already
# be white. The same level controls both temporary growth and halo brightness.
SPARSE_WAVE_KEYFRAMES = (
    (0, 0, 0, 0, 0),
    (1, 0, 0, 0, 0),
    (2, 1, 0, 0, 0),
    (2, 2, 1, 0, 0),
    (2, 2, 2, 1, 0),
    (1, 2, 2, 2, 1),
    (0, 1, 1, 1, 0),
    (0, 0, 0, 0, 0),
)


@dataclass(frozen=True)
class LyricEmphasisPlan:
    """Highest-priority treatment for an unusually sparse timed line."""

    mode: str = "none"
    unit_count: int = 0
    duration: float = 0.0
    seconds_per_unit: float = 0.0


@dataclass(frozen=True)
class LyricGlowPhase:
    """One frame of the sparse-line scatter glow.

    ``visible_fraction`` is the right-hand edge of the visible glow.  It
    travels left-to-right during the opening fifth, remains fully open for
    the middle three fifths, then travels right-to-left during the closing
    fifth.  ``intensity`` is kept separate so the moving edge never has to
    blur or recolor the lyric glyphs themselves.
    """

    stage: str
    intensity: float
    visible_fraction: float


@dataclass(frozen=True)
class SparseGlyphFrame:
    """A single glyph's media-clock-driven bloom and raised resting pose."""

    scale: float
    lift: float
    read_fraction: float
    glow: float
    highlighted: bool = False


def _is_cjk_character(character: str) -> bool:
    return (
        "\u2e80" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\u3040" <= character <= "\u30ff"
        or "\u31f0" <= character <= "\u31ff"
        or "\u1100" <= character <= "\u11ff"
        or "\u3130" <= character <= "\u318f"
        or "\uac00" <= character <= "\ud7af"
    )


def lyric_spoken_units(text: str) -> tuple[str, ...]:
    """Count Latin-like words and individual CJK characters as spoken units."""
    units: list[str] = []
    word: list[str] = []

    def flush_word():
        if word:
            units.append("".join(word))
            word.clear()

    for character in str(text or ""):
        if _is_cjk_character(character):
            flush_word()
            units.append(character)
        elif character.isalnum() or unicodedata.combining(character):
            word.append(character)
        elif character in {"'", "’", "-", "‐", "‑"} and word:
            word.append(character)
        else:
            flush_word()
    flush_word()
    return tuple(unit for unit in units if any(character.isalnum() for character in unit))


def lyric_emphasis_plan(
    text: str,
    start: float,
    next_start: float | None,
    *,
    min_seconds_per_unit: float = SPARSE_MIN_SECONDS_PER_UNIT,
) -> LyricEmphasisPlan:
    """Select the sparse-line effect from real adjacent LRC timestamps.

    A final line without a reliable next timestamp is deliberately ineligible.
    """
    units = lyric_spoken_units(text)
    if next_start is None or not units:
        return LyricEmphasisPlan(unit_count=len(units))
    try:
        duration = float(next_start) - float(start)
    except (TypeError, ValueError, OverflowError):
        return LyricEmphasisPlan(unit_count=len(units))
    if not math.isfinite(duration):
        return LyricEmphasisPlan(unit_count=len(units))
    duration = max(0.0, duration)
    seconds_per_unit = duration / len(units)
    threshold = normalize_sparse_seconds_per_unit(min_seconds_per_unit)
    if duration < 3.0 or seconds_per_unit < threshold:
        return LyricEmphasisPlan(
            unit_count=len(units), duration=duration, seconds_per_unit=seconds_per_unit,
        )
    alphabetic_word_language = all(
        not any(_is_cjk_character(character) for character in unit)
        and any(character.isalpha() for character in unit)
        for unit in units
    )
    return LyricEmphasisPlan(
        mode="character_lift" if alphabetic_word_language else "scatter_glow",
        unit_count=len(units),
        duration=duration,
        seconds_per_unit=seconds_per_unit,
    )


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def continuous_read_lift(progress: float, unit_index: int, unit_count: int) -> float:
    """Sample the ordinary trailing rise without changing the reading clock.

    The requested staircase poses describe an overlapping propagation,
    rather than discrete stops. Each unit therefore owns one continuous
    rise from its launch to its landing. The first two launch together;
    following units launch in order while their predecessors keep rising.
    Interpolating each pair of the old nine poses independently caused odd
    and even units to alternate between motion and a visible plateau.

    A view multiplies the returned 0..1 height by its existing lift setting.
    One-third poses describe the quarter-height example's four states; the
    continuous positions also cover all 16/8/4 logical height bands and the
    160/80/40 sampling targets. These are position samples, not a promise of
    display frames. Actual source timestamps still determine ``progress``.
    """
    try:
        count = max(1, int(unit_count))
        index = max(0, min(count - 1, int(unit_index)))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    try:
        progress = float(progress)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if math.isnan(progress):
        return 0.0
    progress = max(0.0, min(1.0, progress))
    if progress <= 0.0:
        return 0.0
    if progress >= 1.0:
        return 1.0
    phase = progress * (count + 2)
    launch = max(0, index - 1)
    landing = min(count + 2, index + 4)
    return _smoothstep((phase - launch) / (landing - launch))


def lyric_glow_phase(progress: float) -> LyricGlowPhase:
    """Return the requested 20% reveal, 60% hold, 20% fade envelope.

    For a five-second lyric this is exactly one second revealing from left to
    right, three seconds at full scattered brightness, and one second
    withdrawing from right to left.  Entry uses an ease-out curve so the
    effect becomes legible promptly; exit uses ease-in so it leaves without a
    late flash.
    """
    value = max(0.0, min(1.0, float(progress)))
    edge_fraction = 0.20
    if value <= 0.0:
        return LyricGlowPhase("reveal", 0.0, 0.0)
    if value < edge_fraction:
        local = value / edge_fraction
        return LyricGlowPhase(
            "reveal",
            1.0 - (1.0 - local) ** 3,
            _smoothstep(local),
        )
    if value <= 1.0 - edge_fraction:
        return LyricGlowPhase("hold", 1.0, 1.0)
    if value < 1.0:
        local = (value - (1.0 - edge_fraction)) / edge_fraction
        return LyricGlowPhase(
            "fade",
            1.0 - local * local,
            1.0 - _smoothstep(local),
        )
    return LyricGlowPhase("done", 0.0, 0.0)


def lyric_glow_intensity(progress: float) -> float:
    """Compatibility accessor for callers that only need the glow envelope."""
    return lyric_glow_phase(progress).intensity


def sparse_glyph_frame(
    progress: float, index: int, count: int, *, read_progress: float | None = None,
) -> SparseGlyphFrame:
    """Sample one shared growth/shine wave across the timed sparse lyric.

    For five graphemes the eight keyframes reproduce ``00000`` through
    ``01110`` and back to ``00000`` exactly. Other lengths sample the same
    spatial profile across the whole line. Reading and baseline lift advance
    independently and never reverse when the temporary wave recedes. Line-level
    LRC still supplies only an even visual interpolation, not phoneme timing.
    """
    count = max(1, int(count))
    index = max(0, min(count - 1, int(index)))
    progress = max(0.0, min(1.0, float(progress)))
    # The eight-stage growth/light wave keeps its original full-duration
    # clock. Only this effect's white front is ahead of it: every highlighted
    # glyph has been read by the first half of the group's elapsed time.
    # Genuine unit-timed groups pass their own elapsed fraction separately;
    # surrounding ordinary text retains its source-timed color front.
    read_progress = progress if read_progress is None else max(0.0, min(1.0, float(read_progress)))

    wave_position = progress * (len(SPARSE_WAVE_KEYFRAMES) - 1)
    left_frame = min(len(SPARSE_WAVE_KEYFRAMES) - 2, int(wave_position))
    blend = _smoothstep(wave_position - left_frame)
    spatial_position = 0.0 if count == 1 else 4.0 * index / (count - 1)
    left_sample = min(3, int(spatial_position))
    spatial_blend = spatial_position - left_sample
    leading = SPARSE_WAVE_KEYFRAMES[left_frame]
    trailing = SPARSE_WAVE_KEYFRAMES[left_frame + 1]
    leading_level = leading[left_sample] + (
        leading[left_sample + 1] - leading[left_sample]
    ) * spatial_blend
    trailing_level = trailing[left_sample] + (
        trailing[left_sample + 1] - trailing[left_sample]
    ) * spatial_blend
    wave_level = (
        leading_level * (1.0 - blend) + trailing_level * blend
    )
    shine = wave_level / 2.0

    # Sample the white front by glyph ordinal so it completes halfway through
    # the effect, including long words. It is independent of the eight-stage
    # scale/glow wave, whose tail still has the rest of the line to recede.
    front_distance = wave_position - spatial_position
    read = max(0.0, min(1.0, 2.0 * read_progress * count - index))
    lift = GLYPH_FLOAT_LIFT * _smoothstep((front_distance - 0.15) / 1.85)
    return SparseGlyphFrame(1.0 + 0.125 * shine, lift, read, shine, True)


@dataclass(frozen=True)
class MusicLyricFrame:
    active_index: int
    scroll_index: float
    reading_progress: float
    scroll_velocity: float


def glyph_reading_state(progress: float, index: int, count: int) -> tuple[float, float]:
    """Return a smooth read fade and a one-way per-glyph rise.

    Each glyph starts on the lower baseline and floats upward as the reading
    front reaches it.  It remains on the raised plateau instead of bouncing
    down again.  Both plateaus stay batchable, so the continuous interpolation
    does not turn every lyric frame into one draw call per glyph.
    """
    front = max(0.0, min(1.0, float(progress))) * max(
        1.0,
        max(1, count) - 1.0 + GLYPH_PULSE_SPAN,
    )
    phase = max(0.0, min(1.0, (front - index + GLYPH_PULSE_LEAD) / GLYPH_PULSE_SPAN))
    ease = 1.0 - (1.0 - phase) ** 3
    lift = GLYPH_FLOAT_LIFT * ease
    return 0.5 + 0.5 * ease, lift


@dataclass(frozen=True)
class WrappedLineSpringFrame:
    active_line: int
    offsets: tuple[float, ...]
    velocities: tuple[float, ...]


@dataclass(frozen=True)
class LogicalLineSpringFrame:
    positions: tuple[float, ...]
    velocities: tuple[float, ...]
    delays: tuple[float, ...]


class MotionSimulationClock:
    """Bounded visual time shared by independently running motion engines.

    Media time remains authoritative and is never slowed by this clock. A
    delayed visible frame accumulates a small amount of simulation debt; the
    next draws repay it without advancing an entire old transition at once.
    Hiding a surface is a caller-owned lifecycle event, not something inferred
    from a slow draw. Call ``reset`` when a surface deliberately re-anchors.
    """

    MAX_FRAME_SECONDS = 1.0 / 30.0
    MAX_DEBT_SECONDS = 0.12

    def __init__(self):
        self.last_at: float | None = None
        self.debt = 0.0
        self._healthy_interval = 1.0 / 60.0
        self._slower_interval: float | None = None
        self._slower_samples = 0

    def reset(self, now: float) -> None:
        self.last_at = float(now)
        self.debt = 0.0
        self._healthy_interval = 1.0 / 60.0
        self._slower_interval = None
        self._slower_samples = 0

    def advance(self, now: float) -> float:
        now = float(now)
        if self.last_at is None or now < self.last_at:
            self.reset(now)
            return 0.0
        elapsed = now - self.last_at
        if elapsed <= 0.0:
            return 0.0
        self.last_at = now
        delayed = elapsed > self._healthy_interval * 2.0
        if delayed and elapsed <= self.MAX_DEBT_SECONDS:
            # Cost adaptation can intentionally lower a surface's cadence.
            # Three similar observations are a new cadence, whereas one
            # isolated stall must not make the next transition jump ahead.
            if self._slower_interval is not None and abs(elapsed - self._slower_interval) <= self._slower_interval * 0.25:
                self._slower_samples += 1
            else:
                self._slower_interval, self._slower_samples = elapsed, 1
            if self._slower_samples >= 3:
                self._healthy_interval = min(self.MAX_FRAME_SECONDS, elapsed)
                delayed = False
        elif elapsed >= 1.0 / 480.0:
            self._slower_interval = None
            self._slower_samples = 0
        had_debt = self.debt > 1e-10
        self.debt = min(self.MAX_DEBT_SECONDS, self.debt + elapsed)
        if delayed:
            # A 170/240 Hz surface must not repay a stalled frame as a full
            # 30 Hz step. Retain its recent cadence on the first resumed draw.
            budget = min(self.MAX_FRAME_SECONDS, self._healthy_interval * 2.0)
        else:
            # Timer and paint may sample the same engine a fraction of a
            # millisecond apart. Such a sample still advances true time, but
            # cannot redefine the expected cadence of a 4 ms frame timer or
            # interrupt the repeated samples establishing a slower cadence.
            if elapsed >= 1.0 / 480.0:
                self._healthy_interval = max(1.0 / 240.0, min(self.MAX_FRAME_SECONDS, elapsed))
            # Subsequent healthy draws repay at most half an extra frame. The
            # recovery rate is independent of monitor refresh, and debt-free
            # calls retain their exact observed elapsed time.
            budget = min(self.MAX_FRAME_SECONDS, elapsed * 1.5) if had_debt else elapsed
        advanced = min(budget, self.debt)
        self.debt = max(0.0, self.debt - advanced)
        if self.debt < 1e-10:
            self.debt = 0.0
        return advanced


class LogicalLineSpringChain:
    """Independent, staggered position springs for the visible lyric rows.

    Targets are absolute y coordinates.  On a one-line advance every row keeps
    its old position and then catches its new target with a short per-row
    delay.  The following rows use a slightly firmer spring, so their gaps
    compress briefly before the chain relaxes.  Text is never scaled.

    Seeks and interrupted transitions retain the current physical state.
    Optional unique, hashable ``row_ids`` let a same-song layout replacement
    carry that state to matching rows; use disjoint identities for new songs.
    Initial content and explicit ``animate=False`` are the anchoring events.
    """

    STIFFNESS = 188.0
    DAMPING = 25.5
    FOLLOW_STIFFNESS_STEP = 13.0
    STAGGER_SECONDS = 0.028
    MAX_STAGGER_ROWS = 5
    MAX_VELOCITY = 920.0
    # Distant seeks retain a spring's duration by scaling its travel budget.
    # A fixed pixel/second cap would take many seconds to cross a long song.
    DISTANT_TRAVEL_RATE = 6.0

    def __init__(self):
        self.key: object | None = None
        self.positions: list[float] = []
        self.targets: list[float] = []
        self.velocities: list[float] = []
        self.delays: list[float] = []
        self.active_index = 0
        self.sequence_index = 0
        self.last_at: float | None = None
        self.visible_range: tuple[int, int] | None = None
        self.row_ids: tuple[object, ...] | None = None
        self._clock = MotionSimulationClock()
        self._travel_limits: list[float] = []
        self._coherent_travel = False

    def _reset(
        self,
        key: object,
        targets: tuple[float, ...],
        active_index: int,
        now: float,
        sequence_index: int,
        visible_range: tuple[int, int],
        row_ids: tuple[object, ...] | None = None,
    ):
        self.key = key
        self.positions = list(targets)
        self.targets = list(targets)
        self.velocities = [0.0] * len(targets)
        self.delays = [0.0] * len(targets)
        self.active_index = int(active_index)
        self.sequence_index = int(sequence_index)
        self.last_at = float(now)
        self.visible_range = visible_range
        self.row_ids = row_ids
        self._clock.reset(now)
        self._travel_limits = [self.MAX_VELOCITY] * len(targets)
        self._coherent_travel = False

    def viewport_pose(self, targets: tuple[float, ...] | list[float] | None = None) -> tuple[float, float]:
        """Predict unstepped rows from the ongoing viewport roll.

        The surface can bisect target coordinates shifted by this offset to
        find rows currently crossing its viewport. Searching the whole swept
        interval from the old to new focus would step every skipped lyric.
        """
        values = self.targets if targets is None else targets
        first, last = self.visible_range or (0, 0)
        indexes = range(first, min(last, len(values), len(self.positions)))
        offsets = sorted(self.positions[index] - float(values[index]) for index in indexes)
        velocities = sorted(self.velocities[index] for index in range(first, min(last, len(self.velocities))))
        if not offsets:
            return 0.0, 0.0
        middle = len(offsets) // 2
        return offsets[middle], velocities[len(velocities) // 2]

    @property
    def viewport_offset(self) -> float:
        return self.viewport_pose()[0]

    def position_for(self, index: int, target: float | None = None) -> float:
        """Sample a physical row, predicting hidden rows from the shared roll."""
        first, last = self.visible_range or (0, 0)
        if 0 <= index < len(self.positions) and first <= index < last:
            return self.positions[index]
        if 0 <= index < len(self.targets):
            return self.targets[index] + self.viewport_offset
        return (0.0 if target is None else float(target)) + self.viewport_offset

    def rescale(self, scale: float, now: float) -> None:
        """Inherit a snapshot's coordinate scale without replaying hidden time."""
        scale = float(scale)
        if not math.isfinite(scale) or scale <= 0.0:
            raise ValueError("motion coordinate scale must be finite and positive")
        self.positions = [value * scale for value in self.positions]
        self.targets = [value * scale for value in self.targets]
        self.velocities = [value * scale for value in self.velocities]
        self._travel_limits = [value * scale for value in self._travel_limits]
        self.last_at = float(now)
        self._clock.reset(now)

    def _remap(self, key: object, targets: tuple[float, ...], row_ids: tuple[object, ...]) -> bool:
        if self.row_ids is None:
            return False
        old = {identity: index for index, identity in enumerate(self.row_ids)}
        matching = [(index, old[identity]) for index, identity in enumerate(row_ids) if identity in old]
        if not matching:
            return False
        first, last = self.visible_range or (0, len(self.positions))
        visible_matches = [(new, previous) for new, previous in matching if first <= previous < last]
        references = visible_matches or matching
        offsets = sorted(self.positions[previous] - targets[new] for new, previous in references)
        speeds = sorted(self.velocities[previous] for _new, previous in references)
        offset, velocity = offsets[len(offsets) // 2], speeds[len(speeds) // 2]
        positions = [target + offset for target in targets]
        velocities = [velocity] * len(targets)
        previous_targets = list(targets)
        delays = [0.0] * len(targets)
        limits = [max(self.MAX_VELOCITY, abs(offset) * self.DISTANT_TRAVEL_RATE)] * len(targets)
        for index, previous in matching:
            positions[index] = self.positions[previous]
            velocities[index] = self.velocities[previous]
            previous_targets[index] = self.targets[previous]
            delays[index] = self.delays[previous]
            limits[index] = self._travel_limits[previous]
        self.key = key
        self.row_ids = row_ids
        self.positions, self.velocities = positions, velocities
        self.targets, self.delays, self._travel_limits = previous_targets, delays, limits
        self.visible_range = (
            min(index for index, _previous in visible_matches),
            max(index for index, _previous in visible_matches) + 1,
        ) if visible_matches else (0, 0)
        return True

    def advance(self, now: float) -> LogicalLineSpringFrame:
        """Run stored targets without requiring a paint or new media sample."""
        remaining = self._clock.advance(now)
        self.last_at = float(now)
        visible_start, visible_end = self.visible_range or (0, 0)
        while remaining > 0.0:
            step = min(remaining, 1.0 / 240.0)
            for index in range(visible_start, visible_end):
                delay = self.delays[index]
                if delay > 0.0:
                    self.delays[index] = max(0.0, delay - step)
                    continue
                position = self.positions[index]
                velocity = self.velocities[index]
                if abs(position - self.targets[index]) < 0.025 and abs(velocity) < 0.32:
                    self.positions[index] = self.targets[index]
                    self.velocities[index] = 0.0
                    continue
                follower_distance = 0 if self._coherent_travel else max(0, index - self.active_index)
                stiffness = self.STIFFNESS + min(4, follower_distance) * self.FOLLOW_STIFFNESS_STEP
                acceleration = stiffness * (self.targets[index] - position) - self.DAMPING * velocity
                limit = self._travel_limits[index]
                velocity = max(-limit, min(limit, velocity + acceleration * step))
                position += velocity * step
                if abs(position - self.targets[index]) < 0.025 and abs(velocity) < 0.32:
                    position, velocity = self.targets[index], 0.0
                self.positions[index], self.velocities[index] = position, velocity
            remaining -= step
        return LogicalLineSpringFrame(tuple(self.positions), tuple(self.velocities), tuple(self.delays))

    def frame(
        self,
        key: object,
        targets: tuple[float, ...] | list[float],
        active_index: int,
        now: float,
        *,
        animate: bool = True,
        seek: bool = False,
        sequence_index: int | None = None,
        visible_range: tuple[int, int] | None = None,
        row_ids: tuple[object, ...] | list[object] | None = None,
    ) -> LogicalLineSpringFrame:
        target_values = tuple(float(value) for value in targets)
        active_index = int(active_index)
        sequence_index = active_index if sequence_index is None else int(sequence_index)
        now = float(now)
        visible_start, visible_end = (0, len(target_values)) if visible_range is None else visible_range
        visible_start = max(0, min(len(target_values), int(visible_start)))
        visible_end = max(visible_start, min(len(target_values), int(visible_end)))
        current_range = (visible_start, visible_end)
        identities = None if row_ids is None else tuple(row_ids)
        if identities is not None and len(identities) != len(target_values):
            raise ValueError("row_ids must match target row count")
        layout_changed = key != self.key or len(target_values) != len(self.positions) or identities != self.row_ids
        if self.last_at is None or now < self.last_at or not animate:
            self._reset(key, target_values, active_index, now, sequence_index, current_range, identities)
            return LogicalLineSpringFrame(tuple(self.positions), tuple(self.velocities), tuple(self.delays))
        if layout_changed and not (identities is not None and self._remap(key, target_values, identities)):
            self._reset(key, target_values, active_index, now, sequence_index, current_range, identities)
            return LogicalLineSpringFrame(tuple(self.positions), tuple(self.velocities), tuple(self.delays))

        previous_active = self.active_index
        previous_sequence = self.sequence_index
        active_changed = active_index != previous_active
        offset, group_velocity = self.viewport_pose(target_values)
        previous_range = self.visible_range or (0, len(target_values))
        for index in range(visible_start, visible_end):
            if index < previous_range[0] or index >= previous_range[1]:
                # Rows crossing into view join the current roll even if the
                # destination skips hundreds of rows. Hidden rows need no
                # per-frame simulation to maintain the shared displacement.
                self.positions[index] = target_values[index] + offset
                self.velocities[index] = group_velocity
                self.delays[index] = 0.0
                self._travel_limits[index] = max(self.MAX_VELOCITY, abs(offset) * self.DISTANT_TRAVEL_RATE)
            if target_values[index] != self.targets[index]:
                self._travel_limits[index] = max(
                    self.MAX_VELOCITY, abs(target_values[index] - self.positions[index]) * self.DISTANT_TRAVEL_RATE,
                )
        self.visible_range = current_range
        self.targets = list(target_values)
        # Rows outside this viewport window do not consume spring steps.
        if seek:
            self._coherent_travel = True
            for index in range(visible_start, visible_end):
                self.delays[index] = 0.0
        if active_changed:
            distant = seek or abs(sequence_index - previous_sequence) > 1
            in_flight = any(abs(self.velocities[index]) > 0.32 for index in range(visible_start, visible_end))
            self._coherent_travel = distant or in_flight
            direction = 1 if active_index > previous_active else -1
            # Let the newly active line respond within one or two display
            # frames; the trailing lines still compress and catch up in order.
            lead_index = max(0, active_index - 1) if direction > 0 else active_index
            for index in range(visible_start, visible_end):
                distance = max(0, index - lead_index) if direction > 0 else max(0, lead_index - index)
                self.delays[index] = 0.0 if self._coherent_travel else min(self.MAX_STAGGER_ROWS, distance) * self.STAGGER_SECONDS
                # A small directional impulse lets followers catch the leader
                # and creates the spacing compression without deforming text.
                if distance > 0 and not self._coherent_travel:
                    impulse = min(160.0, 46.0 + distance * 18.0)
                    self.velocities[index] = -float(direction) * impulse
            self.active_index = active_index
            self.sequence_index = sequence_index
        else:
            self.sequence_index = sequence_index
        return self.advance(now)


class WrappedLineSpringChain:
    """A short, one-way spring chain for visual lines in one lyric sentence.

    Crossing a QTextLayout line boundary gives the leading visual line a
    downward compression impulse.  Each following line is coupled to its
    predecessor, so it follows later and rebounds instead of the entire
    sentence being scaled as one block.  Values are line-height fractions;
    painting decides the exact pixel amplitude.
    """

    SPRING = 220.0
    DAMPING = 22.0
    FOLLOW_SPRING = 460.0
    FOLLOW_DAMPING = 4.0
    IMPULSE = 5.2
    MAX_OFFSET = 0.20
    MAX_VELOCITY = 6.5

    def __init__(self):
        self.key: object | None = None
        self.signature: tuple[int, ...] = ()
        self.offsets: list[float] = []
        self.velocities: list[float] = []
        self.active_line = 0
        self.last_at: float | None = None
        self.last_impulse_at: float | None = None
        self._clock = MotionSimulationClock()

    @staticmethod
    def _line_for_progress(progress: float, signature: tuple[int, ...]) -> int:
        if not signature:
            return 0
        units = tuple(max(1, int(value)) for value in signature)
        front = max(0.0, min(1.0, float(progress))) * max(
            1.0,
            sum(units) - 1.0 + GLYPH_PULSE_SPAN,
        )
        consumed = 0.0
        for index, count in enumerate(units):
            if front < consumed + count or index == len(units) - 1:
                return index
            consumed += count
        return len(units) - 1

    def _reset(self, key: object, signature: tuple[int, ...], active_line: int, now: float):
        self.key = key
        self.signature = signature
        self.offsets = [0.0] * len(signature)
        self.velocities = [0.0] * len(signature)
        self.active_line = active_line
        self.last_at = float(now)
        self.last_impulse_at = None
        self._clock.reset(now)

    def frame(
        self,
        key: object,
        line_units: tuple[int, ...] | list[int],
        progress: float,
        now: float,
    ) -> WrappedLineSpringFrame:
        signature = tuple(max(1, int(value)) for value in line_units)
        active_line = self._line_for_progress(progress, signature)
        now = float(now)
        if (
            key != self.key
            or signature != self.signature
            or self.last_at is None
            or now < self.last_at
        ):
            self._reset(key, signature, active_line, now)
            return WrappedLineSpringFrame(active_line, tuple(self.offsets), tuple(self.velocities))

        self.last_at = now
        if active_line > self.active_line:
            for leader in range(self.active_line, active_line):
                if 0 <= leader < len(self.velocities):
                    self.velocities[leader] = min(
                        self.MAX_VELOCITY,
                        self.velocities[leader] + self.IMPULSE,
                    )
            self.last_impulse_at = now
        self.active_line = active_line

        remaining = self._clock.advance(now)
        while remaining > 0.0 and self.offsets:
            step = min(remaining, 1.0 / 240.0)
            accelerations: list[float] = []
            for index, (offset, velocity) in enumerate(zip(self.offsets, self.velocities)):
                acceleration = -self.SPRING * offset - self.DAMPING * velocity
                if index > 0:
                    acceleration += self.FOLLOW_SPRING * (self.offsets[index - 1] - offset)
                    acceleration += self.FOLLOW_DAMPING * (self.velocities[index - 1] - velocity)
                accelerations.append(acceleration)
            for index, acceleration in enumerate(accelerations):
                velocity = self.velocities[index] + acceleration * step
                offset = self.offsets[index] + velocity * step
                self.velocities[index] = max(-self.MAX_VELOCITY, min(self.MAX_VELOCITY, velocity))
                self.offsets[index] = max(-self.MAX_OFFSET, min(self.MAX_OFFSET, offset))
            remaining -= step

        if (
            self.last_impulse_at is not None
            and now - self.last_impulse_at >= 0.46
            and max((abs(value) for value in self.offsets), default=0.0) < 0.002
            and max((abs(value) for value in self.velocities), default=0.0) < 0.035
        ):
            self.offsets = [0.0] * len(signature)
            self.velocities = [0.0] * len(signature)
            self.last_impulse_at = None

        return WrappedLineSpringFrame(active_line, tuple(self.offsets), tuple(self.velocities))


def ambient_color(color: QColor) -> QColor:
    value = QColor(color)
    if value.red() > 235 and value.green() > 235 and value.blue() > 235:
        return QColor(90, 90, 90)
    if value.red() < 24 and value.green() < 24 and value.blue() < 24:
        return QColor(56, 56, 56)
    return value


def ambient_light_positions(seconds: float) -> tuple[tuple[float, float], ...]:
    t = float(seconds)
    return (
        (0.18 + 0.07 * math.sin(t * 0.19), 0.20 + 0.09 * math.cos(t * 0.16)),
        (0.77 + 0.09 * math.cos(t * 0.13), 0.34 + 0.08 * math.sin(t * 0.21)),
        (0.54 + 0.11 * math.sin(t * 0.11 + 1.7), 0.78 + 0.07 * math.cos(t * 0.17)),
    )


class MusicLyricMotion:
    """A continuous viewport clock; lyric timing remains owned by the provider."""

    FORWARD_BOUNDARY_MARGIN = 0.025
    FORWARD_CONFIRM_SECONDS = 0.018
    BACKWARD_BOUNDARY_MARGIN = 0.18
    SMALL_BACKSTEP_SECONDS = 0.65
    BACKWARD_CONFIRM_SECONDS = 0.28
    BACKWARD_SEEK_JUMP_SECONDS = 1.8
    # A single foreground/player sample can be wrong by several seconds. The
    # owner interpolates that sample on every paint, so elapsed paint frames
    # are not independent evidence that the playhead really jumped.
    CLOCK_DISCONTINUITY_SECONDS = 0.55
    CLOCK_SAMPLE_CONFIRM_SECONDS = 0.06
    CLOCK_CORRECTION_SECONDS = 0.12
    CLOCK_CORRECTION_MAX_RATE = 0.25

    def __init__(self):
        self.track_key = ""
        self.active_index = 0
        self.scroll_index = 0.0
        self.velocity = 0.0
        self.last_at: float | None = None
        self.last_position: float | None = None
        self.last_source_position: float | None = None
        self.source_generation: object | None = None
        self.presentation_offset = 0.0
        self.pending_forward_index: int | None = None
        self.pending_forward_at: float | None = None
        self.pending_backward_index: int | None = None
        self.pending_backward_at: float | None = None
        self.pending_clock_position: float | None = None
        self.pending_clock_sample: object | None = None
        self.pending_clock_at: float | None = None
        self.pending_clock_sample_position: float | None = None
        self.pending_clock_playing: bool | None = None
        self._clock = MotionSimulationClock()

    def _clear_pending_clock(self):
        self.pending_clock_position = None
        self.pending_clock_sample = None
        self.pending_clock_at = None
        self.pending_clock_sample_position = None
        self.pending_clock_playing = None

    def _stable_position(
        self,
        position: float,
        now: float,
        sample_id: object,
        *,
        playing: bool,
        explicit_seek: bool,
        independent_sample: bool,
        sample_position: float | None,
    ) -> tuple[float, bool]:
        previous = self.last_source_position
        last_at = self.last_at
        if previous is None or last_at is None or explicit_seek:
            self._clear_pending_clock()
            return position, explicit_seek
        elapsed = max(0.0, now - last_at)
        expected = previous + (elapsed if playing else 0.0)
        deviation = position - expected
        if (
            abs(deviation) <= self.CLOCK_DISCONTINUITY_SECONDS
            and position >= previous - 0.12
        ):
            self._clear_pending_clock()
            correction = 1.0 - math.exp(-elapsed / self.CLOCK_CORRECTION_SECONDS)
            if playing:
                # Correct the prediction at a bounded rate, including the
                # final few milliseconds of error. Snapping that small tail
                # to raw time made a backward correction wait until the player
                # caught up, visibly holding several healthy paint frames.
                # The error still converges to the source; this is not a new
                # independent playback clock or an accumulated time offset.
                maximum = elapsed * self.CLOCK_CORRECTION_MAX_RATE
                # A precise source just ahead of prediction may retain its
                # original small catch-up, so a valid row boundary is not held
                # behind another confirmation frame. The same rate bound
                # prevents even that catch-up from becoming a visual jump.
                adjustment = deviation if 0.0 <= deviation <= 0.03 else deviation * correction
                adjustment = max(-maximum, min(maximum, adjustment))
                return expected + adjustment, False
            # Paused observations retain the existing hold/explicit-seek
            # behavior and must not acquire elapsed playback time.
            if abs(deviation) <= 0.03:
                return max(previous, position), False
            return max(previous, expected + deviation * correction), False
        candidate_matches = (
            self.pending_clock_position is not None
            and self.pending_clock_at is not None
            and self.pending_clock_playing == playing
            and (
                sample_position is None or self.pending_clock_sample_position is None
                or math.isfinite(sample_position) == math.isfinite(self.pending_clock_sample_position)
            )
            and deviation * (self.pending_clock_position - expected) > 0.0
            and abs(position - self.pending_clock_position - (now - self.pending_clock_at if playing else 0.0))
                <= max(0.80, (now - self.pending_clock_at) * 0.50)
        )
        confirm_seconds = (
            self.CLOCK_SAMPLE_CONFIRM_SECONDS if independent_sample
            else self.BACKWARD_CONFIRM_SECONDS
        )
        if candidate_matches and (
            sample_id != self.pending_clock_sample
            and now - self.pending_clock_at >= confirm_seconds
        ):
            elapsed_sample = now - self.pending_clock_at
            raw_advance = (
                sample_position - self.pending_clock_sample_position
                if sample_position is not None and self.pending_clock_sample_position is not None
                else None
            )
            known_observation = (
                sample_position is None or math.isfinite(sample_position)
            ) and (
                self.pending_clock_sample_position is None
                or math.isfinite(self.pending_clock_sample_position)
            )
            advancing_observation = known_observation and (
                raw_advance is None or not playing or (
                    raw_advance >= max(0.005, elapsed_sample * 0.25)
                    and abs(raw_advance - elapsed_sample) <= max(0.80, elapsed_sample * 0.50)
                )
            )
            # A fresh event stamp with the same frozen/wrong Position is not
            # corroboration of a playing seek. Interpolated frames can keep
            # advancing while this original source observation stands still.
            # Already-paused/native seeks are explicitly accepted by the view.
            if advancing_observation:
                self._clear_pending_clock()
                return position, True
        if not candidate_matches:
            self.pending_clock_position = position
            self.pending_clock_sample = sample_id
            self.pending_clock_at = now
            self.pending_clock_sample_position = sample_position
            self.pending_clock_playing = playing
        return expected, False

    def _clear_pending_forward(self):
        self.pending_forward_index = None
        self.pending_forward_at = None

    def _clear_pending_backward(self):
        self.pending_backward_index = None
        self.pending_backward_at = None

    def _stable_active_index(
        self,
        timed_lyrics: list[tuple[float, str]],
        raw_index: int,
        position: float,
        now: float,
        *,
        allow_backward: bool,
        allow_jump: bool = False,
        authoritative_index: int | None,
    ) -> int:
        current = max(0, min(len(timed_lyrics) - 1, int(self.active_index)))
        if authoritative_index is not None:
            self._clear_pending_forward()
            self._clear_pending_backward()
            return max(0, min(len(timed_lyrics) - 1, int(authoritative_index)))
        if raw_index == current:
            self._clear_pending_forward()
            self._clear_pending_backward()
            return current
        if raw_index > current:
            self._clear_pending_backward()
            if raw_index - current > 1 and allow_jump:
                self._clear_pending_forward()
                return raw_index
            if raw_index - current > 1:
                # The position stabilizer normally prevents this path. Keep a
                # second guard here for direct callers that feed raw samples.
                if self.pending_forward_index != raw_index or self.pending_forward_at is None:
                    self.pending_forward_index = raw_index
                    self.pending_forward_at = now
                    return current
                if now - self.pending_forward_at < self.CLOCK_SAMPLE_CONFIRM_SECONDS:
                    return current
                self._clear_pending_forward()
                return raw_index
            boundary = float(timed_lyrics[raw_index][0])
            if position >= boundary + self.FORWARD_BOUNDARY_MARGIN:
                self._clear_pending_forward()
                return raw_index
            if self.pending_forward_index != raw_index or self.pending_forward_at is None:
                self.pending_forward_index = raw_index
                self.pending_forward_at = now
                return current
            if now - self.pending_forward_at >= self.FORWARD_CONFIRM_SECONDS:
                self._clear_pending_forward()
                return raw_index
            return current

        self._clear_pending_forward()
        if allow_backward:
            self._clear_pending_backward()
            return raw_index
        current_start = float(timed_lyrics[current][0])
        backstep = 0.0 if self.last_position is None else self.last_position - position
        boundary_jitter = (
            raw_index == current - 1
            and position >= current_start - self.BACKWARD_BOUNDARY_MARGIN
            and backstep < self.SMALL_BACKSTEP_SECONDS
        )
        if boundary_jitter:
            self._clear_pending_backward()
            return current
        # A single stale media sample just behind the current row is common
        # during player clock corrections. Confirm a small one-row reversal
        # across several frames; a real, larger seek remains immediate.
        if raw_index == current - 1 and backstep < self.BACKWARD_SEEK_JUMP_SECONDS:
            if self.pending_backward_index != raw_index or self.pending_backward_at is None:
                self.pending_backward_index = raw_index
                self.pending_backward_at = now
                return current
            if now - self.pending_backward_at < self.BACKWARD_CONFIRM_SECONDS:
                return current
        self._clear_pending_backward()
        return raw_index

    def frame(
        self,
        timed_lyrics: list[tuple[float, str]],
        position: float,
        now: float,
        track_key: str,
        fallback_index: int = 0,
        duration: float = 0.0,
        allow_backward: bool = False,
        authoritative_index: int | None = None,
        sample_id: object | None = None,
        playing: bool = True,
        explicit_seek: bool = False,
        position_ceiling: float | None = None,
        presentation_offset: float = 0.0,
        presentation_reanchor: bool = False,
        sample_position: float | None = None,
        source_generation: object | None = None,
    ) -> MusicLyricFrame:
        """Validate the player clock before applying a display-only offset.

        ``last_source_position`` owns source spike confirmation. ``last_position``
        remains the reading time consumed by word highlights and viewport layout.
        Changing output latency retargets that presentation without confirming or
        clearing a suspected source jump, and never resets the spring's pose.
        ``sample_position`` is the original, unprojected source observation;
        a non-finite value means observation unknown and cannot confirm jumps.
        Callers omitting it retain the legacy interpolated-clock contract.
        ``source_generation`` isolates evidence from different apps/adapters
        without resetting the current viewport position or velocity.
        """
        now = float(now)
        position = max(0.0, float(position))
        presentation_offset = float(presentation_offset)
        if not math.isfinite(presentation_offset):
            presentation_offset = 0.0
        presentation_changed = presentation_reanchor or not math.isclose(
            presentation_offset, self.presentation_offset, abs_tol=1e-7,
        )
        explicit_seek = explicit_seek or allow_backward or authoritative_index is not None
        resetting = track_key != self.track_key or self.last_at is None or now < self.last_at
        clock_reanchored = explicit_seek
        if source_generation != self.source_generation:
            self._clear_pending_clock()
            self._clear_pending_forward()
            self._clear_pending_backward()
        self.source_generation = source_generation
        if resetting:
            self._clear_pending_clock()
        else:
            position, clock_reanchored = self._stable_position(
                position, now, now if sample_id is None else sample_id,
                playing=playing, explicit_seek=explicit_seek,
                independent_sample=sample_id is not None,
                sample_position=None if sample_position is None else float(sample_position),
            )
        # A coarse lyric-row observation can only locate the playhead within
        # that row. Smoothing may extrapolate beyond its bounded input at low
        # paint rates, so apply the observation's limit after stabilization.
        if position_ceiling is not None:
            position = min(position, max(0.0, float(position_ceiling)))
        self.last_source_position = float(position)
        position = max(0.0, position + presentation_offset)
        raw_index = (active_index_for_position(timed_lyrics, position, lead_seconds=0.0)
                     if timed_lyrics else max(0, fallback_index))
        if timed_lyrics and not resetting:
            index = self._stable_active_index(
                timed_lyrics,
                raw_index,
                float(position),
                float(now),
                allow_backward=allow_backward or clock_reanchored or presentation_changed,
                allow_jump=explicit_seek or clock_reanchored or presentation_changed,
                authoritative_index=authoritative_index,
            )
        else:
            index = raw_index
            self._clear_pending_forward()
            self._clear_pending_backward()
        if (
            timed_lyrics and not resetting and not explicit_seek and not presentation_changed
            and index == self.active_index and self.last_position is not None
            and position < self.last_position
            and self.last_position - position < self.BACKWARD_SEEK_JUMP_SECONDS
        ):
            # A pair of old but independent player samples can be accepted as
            # a clock correction while still pointing at this same sentence.
            # Keep small corrections from un-reading ink. A large confirmed
            # player seek, verified local seek, or line change can re-anchor.
            position = self.last_position
        if resetting:
            self.track_key = track_key
            self.active_index = index
            self.scroll_index = float(index)
            self.velocity = 0.0
            self.last_at = now
            self._clock.reset(now)
        else:
            # Seek acceptance changes source time immediately. The viewport
            # independently retargets from its current position and velocity.
            # Fixed substeps plus bounded visual debt handle stalled paints.
            remaining = self._clock.advance(now)
            self.last_at = now
            while remaining > 0.0:
                step = min(remaining, 1.0 / 120.0)
                acceleration = 160.0 * (index - self.scroll_index) - 18.0 * self.velocity
                self.velocity += acceleration * step
                self.scroll_index += self.velocity * step
                remaining -= step
            if abs(self.scroll_index - index) < 0.001 and abs(self.velocity) < 0.01:
                self.scroll_index, self.velocity = float(index), 0.0

        self.active_index = index
        self.last_position = float(position)
        self.presentation_offset = presentation_offset

        progress = 0.0
        if timed_lyrics:
            start = float(timed_lyrics[index][0])
            end = (float(timed_lyrics[index + 1][0]) if index + 1 < len(timed_lyrics)
                   else max(start + 2.45, float(duration)))
            progress = max(0.0, min(1.0, (position - start) / max(0.35, end - start)))
        return MusicLyricFrame(index, self.scroll_index, progress, self.velocity)

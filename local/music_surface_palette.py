"""Shared artwork roles for the music page and solid-color music island.

These display roles are derived from the same cover without changing the
provider's existing two-color media contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from local.music_lyric_motion import ambient_color


@dataclass(frozen=True)
class MusicSurfacePalette:
    primary: QColor
    secondary: QColor
    characteristic: QColor
    ambient_colors: tuple[QColor, ...] = ()

    @property
    def uses_primary_fallback(self) -> bool:
        """No separate accent family exists, so use one background and pale ink.

        Saturating the dominant color changes its RGB values. Compare hue
        families rather than exact RGB equality to keep this fallback stable.
        """
        return _same_color_family(self.primary, self.characteristic)


def background_role_color(source: QColor) -> QColor:
    """Keep artwork roles dark enough for white live lyrics without hue drift."""
    source = QColor(source)
    if source.value() < 48 and _is_neutral(source):
        return QColor(56, 56, 56)
    color = ambient_color(source)
    hue, saturation, value, _alpha = color.getHsv()
    if hue < 0 or saturation < 35:
        level = max(48, min(76, value))
        return QColor(level, level, level)
    # Scale, rather than cap, brightness: capping two bright source colors
    # made the darkened-secondary fallback identical to the primary.
    return QColor.fromHsv(hue, saturation, max(68, min(138, round(value * 0.50 + 12))))


def relative_luminance(color: QColor) -> float:
    channels = []
    for value in QColor(color).getRgb()[:3]:
        component = value / 255.0
        channels.append(component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4)
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722


def _contrast_ratio(first: QColor, second: QColor) -> float:
    first_luminance = relative_luminance(first)
    second_luminance = relative_luminance(second)
    lighter, darker = max(first_luminance, second_luminance), min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _composited_color(foreground: QColor, background: QColor) -> QColor:
    alpha = QColor(foreground).alphaF()
    return QColor(*(
        round(front * alpha + back * (1.0 - alpha))
        for front, back in zip(QColor(foreground).getRgb()[:3], QColor(background).getRgb()[:3])
    ))


def future_lyric_color(
    characteristic: QColor,
    backdrop: QColor,
    *,
    use_light: bool,
    primary: QColor | None = None,
    require_light: bool = False,
) -> QColor:
    """Darken the real accent, or lighten the primary when no accent exists.

    Readability can require a brighter accent than the initial darkened
    color. Its hue is preserved, and a saturated accent is never replaced
    with a generic blue or a nearly-white neutral. A moving dark ambience
    can require light ink across its complete brightness envelope; in that
    case chroma may soften while the characteristic hue stays unchanged.
    """
    use_light = True if require_light else use_light
    fallback = primary is not None and _same_color_family(primary, characteristic)
    album = ambient_color(QColor(primary if fallback else characteristic))
    hue, saturation, value, _alpha = album.getHsv()
    grayscale = hue < 0 or saturation < 35
    if grayscale:
        levels = range(172, 237, 8) if use_light else range(100, 35, -8)
        preferred = [QColor(level, level, level, 242) for level in levels]
        alternate = [QColor(level, level, level, 242) for level in (
            range(100, 35, -8) if use_light else range(172, 237, 8)
        )]
    else:
        if fallback:
            # A single-hue cover needs the same hue lifted toward a pale tint;
            # forcing a heavily saturated accent here made it disappear into
            # its own background instead.
            starting_chroma = max(48, min(150, round(saturation * 0.63)))
            minimum_chroma = 42
            starting_light = max(172, min(226, round(value * 0.80 + 40)))
        else:
            starting_chroma = min(235, max(110, saturation))
            minimum_chroma = 42 if require_light else 110
            starting_light = max(112, min(202, round(value * 0.74)))
        starting_dark = max(90, min(170, round(value * 0.66)))
        chromas = list(range(starting_chroma, minimum_chroma - 1, -10))
        if not chromas or chromas[-1] != minimum_chroma:
            chromas.append(minimum_chroma)
        light_values = list(range(starting_light, 241, 8)) + [240]
        dark_values = list(range(starting_dark, 47, -8)) + [48]
        preferred_values, alternate_values = (
            (light_values, dark_values) if use_light else (dark_values, light_values)
        )
        preferred = [
            QColor.fromHsv(hue, chroma, brightness, 242)
            for chroma in chromas for brightness in preferred_values
        ]
        alternate = [
            QColor.fromHsv(hue, chroma, brightness, 242)
            for chroma in chromas for brightness in alternate_values
        ]
    tinted = None
    best_ratio = -1.0
    candidates = preferred if require_light else preferred + alternate
    for candidate in candidates:
        ratio = _contrast_ratio(_composited_color(candidate, backdrop), backdrop)
        if ratio > best_ratio:
            tinted, best_ratio = candidate, ratio
        if ratio >= 2.30:
            tinted = candidate
            break
    return QColor(tinted)


def _representative(group: dict[str, float]) -> QColor:
    count = max(1.0, group["count"])
    color = QColor(
        round(group["red"] / count),
        round(group["green"] / count),
        round(group["blue"] / count),
    )
    if color.value() < 46 and _is_neutral(color):
        return QColor(56, 56, 56)
    if color.value() < 46:
        hue, saturation, _value, _alpha = color.getHsv()
        return QColor.fromHsv(hue, saturation, 56)
    if color.red() > 235 and color.green() > 235 and color.blue() > 235:
        return QColor(90, 90, 90)
    return color


def _hue_distance(a: int, b: int) -> int:
    distance = abs(a - b) % 360
    return min(distance, 360 - distance)


def _is_neutral(color: QColor) -> bool:
    color = QColor(color)
    channels = color.getRgb()[:3]
    # Hue is unstable near black: a difference of only two RGB steps can
    # report high HSV saturation without a visible colored pigment.
    return color.hsvHue() < 0 or color.hsvSaturation() < 35 or max(channels) - min(channels) < 12


def _is_visible_accent(color: QColor) -> bool:
    """Reject almost-black color casts masquerading as vivid HSV colors.

    A deep blue shadow may report 80% saturation while contributing less
    visible color than a dark gray. Such shadows may belong to the dominant
    image mass, but must not become a brightened background edge or lyric hue.
    """
    return not _is_neutral(color) and relative_luminance(color) >= 0.018


def _same_color_family(first: QColor, second: QColor) -> bool:
    first, second = QColor(first), QColor(second)
    first_neutral, second_neutral = _is_neutral(first), _is_neutral(second)
    if first_neutral or second_neutral:
        return first_neutral and second_neutral
    return _hue_distance(first.hsvHue(), second.hsvHue()) < 42


def _vivid_primary(color: QColor) -> QColor:
    """Add chroma to the occupied source color without inventing another hue."""
    color = QColor(color)
    if _is_neutral(color):
        level = max(56, min(90, color.value()))
        return QColor(level, level, level)
    hue, saturation, value, _alpha = color.getHsv()
    saturation = min(240, round(saturation + (255 - saturation) * 0.22))
    return QColor.fromHsv(hue, saturation, max(82, value))


def _merge_hue_groups(
    ranked: list[tuple[tuple[str, int], dict[str, float]]],
) -> list[tuple[tuple[str, int], dict[str, float]]]:
    """Do not split one broad cover pigment at an arbitrary hue-bin boundary."""
    merged: list[tuple[tuple[str, int], dict[str, float]]] = []
    for key, source in ranked:
        if key[0] != "hue":
            merged.append((key, dict(source)))
            continue
        color = _representative(source)
        destination = next((
            group for existing_key, group in merged
            if existing_key[0] == "hue"
            and _hue_distance(_representative(group).hsvHue(), color.hsvHue()) <= 26
        ), None)
        if destination is None:
            merged.append((key, dict(source)))
        else:
            for field in destination:
                destination[field] += source[field]
    return sorted(merged, key=lambda pair: pair[1]["count"], reverse=True)


def _ambient_pigments(
    ranked: list[tuple[tuple[str, int], dict[str, float]]],
    opaque_count: float,
) -> tuple[QColor, ...]:
    """Keep source pigments for moving ambience independently of lyric roles.

    The role palette intentionally compresses black/white artwork to dark
    gray. Ambience retains the source's shadow/mid/light gray bands so they
    can move separately. Chromatic families use the same grouped sample and
    must have real area support; dim colored shadows are accepted only when
    they are the dominant pigment, matching the primary-role exception.
    """
    selected: list[QColor] = []
    support = max(3.0, opaque_count * 0.006)
    dominant_key = ranked[0][0]
    # Stable keys resolve equal-area bands without making their scan location
    # part of the animation palette order. Existing role ranking is untouched.
    ordered = sorted(ranked, key=lambda pair: (-pair[1]["count"], pair[0]))
    for key, group in ordered:
        if group["count"] < support:
            continue
        count = group["count"]
        color = QColor(*(
            round(group[channel] / count) for channel in ("red", "green", "blue")
        ))
        if key[0] != "hue":
            level = round(sum(color.getRgb()[:3]) / 3)
            color = QColor(level, level, level)
            if any(_is_neutral(existing) and abs(existing.value() - level) < 16 for existing in selected):
                continue
        else:
            if _is_neutral(color) or group["value"] / count < 0.10:
                continue
            if key != dominant_key and not _is_visible_accent(color):
                continue
            if any(_same_color_family(existing, color) for existing in selected):
                continue
        selected.append(color)
        if len(selected) == 6:
            break
    return tuple(selected)


def _fallback_ambient(primary: QColor, secondary: QColor) -> tuple[QColor, ...]:
    """Use only the supplied fallback pigments when artwork has no samples."""
    if primary == secondary:
        return (QColor(primary),)
    return (QColor(primary), QColor(secondary))


def artwork_surface_palette(
    image: QImage,
    primary_fallback: QColor,
    secondary_fallback: QColor,
) -> MusicSurfacePalette:
    """Read occupied primary/secondary pigments and a separately vivid accent.

    A secondary must visibly differ from the primary, rather than be another
    gray shadow or a neighboring shade of the same pigment. Accents may cover
    less than one percent of the cover; only unsupported pixel noise is
    discarded. A chromatic accent therefore survives a mostly gray cover.
    """
    primary_fallback = QColor(primary_fallback)
    secondary_fallback = QColor(secondary_fallback)
    if image.isNull():
        return MusicSurfacePalette(
            primary_fallback, secondary_fallback, primary_fallback,
            _fallback_ambient(primary_fallback, secondary_fallback),
        )

    sample = image.scaled(
        72, 72,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    ).convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = sample.constBits()
    row_bytes = sample.bytesPerLine()
    groups: dict[tuple[str, int], dict[str, float]] = {}
    opaque_count = 0.0
    brightness_sum = 0.0
    for y in range(sample.height()):
        row_start = y * row_bytes
        for x in range(sample.width()):
            offset = row_start + x * 4
            # pixelColor() allocates and converts through Qt once per sample.
            # RGBA8888 keeps the same QColor arithmetic below while reading
            # the small, detached sample buffer directly.
            color = QColor(
                pixels[offset], pixels[offset + 1],
                pixels[offset + 2], pixels[offset + 3],
            )
            if color.alpha() < 24:
                continue
            hue, saturation, value, alpha = color.getHsvF()
            opaque_count += alpha
            brightness_sum += value * alpha
            # A dark but chromatic pigment must keep its hue. Otherwise a
            # red/blue low-key photograph collapses into a dominant gray
            # shadow bucket even when nearly every dark pixel is colored.
            chroma = max(color.getRgb()[:3]) - min(color.getRgb()[:3])
            dark_pigment = value >= 0.06 and saturation >= 0.25 and chroma >= 12
            if value < 0.18 and not dark_pigment:
                key = ("shadow", 0)
            elif saturation < 0.10 or hue < 0 or (color.value() - min(color.getRgb()[:3])) < 12:
                key = ("light" if value > 0.82 else "neutral", 0)
            else:
                key = ("hue", int(((hue * 360.0) + 7.5) // 15.0) % 24)
            group = groups.setdefault(key, {
                "count": 0.0, "red": 0.0, "green": 0.0, "blue": 0.0,
                "saturation": 0.0, "value": 0.0,
            })
            group["count"] += alpha
            group["red"] += color.red() * alpha
            group["green"] += color.green() * alpha
            group["blue"] += color.blue() * alpha
            group["saturation"] += saturation * alpha
            group["value"] += value * alpha

    if not opaque_count:
        return MusicSurfacePalette(
            primary_fallback, secondary_fallback, primary_fallback,
            _fallback_ambient(primary_fallback, secondary_fallback),
        )
    ranked = _merge_hue_groups(sorted(groups.items(), key=lambda pair: pair[1]["count"], reverse=True))
    ambient_colors = _ambient_pigments(ranked, opaque_count)
    if not ambient_colors and opaque_count < 3.0:
        ambient_colors = _fallback_ambient(primary_fallback, secondary_fallback)
    chromatic = [
        (key, group) for key, group in ranked
        if key[0] == "hue" and group["count"] / opaque_count >= 0.006
        and group["value"] / group["count"] >= 0.10
        and not _is_neutral(_representative(group))
    ]
    if not chromatic:
        level = round(56 + 34 * brightness_sum / opaque_count)
        primary = QColor(level, level, level)
        return MusicSurfacePalette(
            primary, primary.darker(135), QColor(primary),
            ambient_colors or (QColor(primary),),
        )

    dominant_key, dominant_group = ranked[0]
    primary = _vivid_primary(_representative(dominant_group))

    def distinct_companion(key: tuple[str, int], group: dict[str, float]) -> bool:
        if key == dominant_key:
            return False
        color = _representative(group)
        if _same_color_family(primary, color):
            return False
        if key[0] == "hue":
            return _is_visible_accent(color)
        # Two neutral shade bands are one color family. A neutral band can
        # nevertheless be a real distinct secondary on a colorful cover.
        if not _is_neutral(primary):
            return abs(color.value() - primary.value()) >= 40
        return True

    companion = next(
        ((key, group) for key, group in ranked
         if group["count"] / opaque_count >= 0.006 and distinct_companion(key, group)),
        None,
    )
    secondary = _representative(companion[1]) if companion is not None else primary.darker(135)

    # A vivid detail is more important here than its raw area.  Ignore tiny
    # single-pixel noise, but do not require the accent to be a dominant band.
    vivid = [
        (key, group) for key, group in chromatic
        if not _same_color_family(primary, _representative(group))
        and _is_visible_accent(_representative(group))
    ]
    if vivid:
        def vividness(pair: tuple[tuple[str, int], dict[str, float]]) -> float:
            _key, group = pair
            color = _representative(group)
            return color.hsvSaturationF() * math.sqrt(color.valueF())

        most_vivid = max(vividness(pair) for pair in vivid)
        # Almost equal pastel pigments should favor real cover area rather
        # than a tiny skin/compression fringe. A genuinely vivid sparse accent
        # still wins because its chroma differs by substantially more than 4%.
        _accent_key, accent_group = max(
            (pair for pair in vivid if vividness(pair) >= most_vivid - 0.04),
            key=lambda pair: pair[1]["count"],
        )
        characteristic = _representative(accent_group)
        if _is_neutral(primary) and accent_group["count"] / opaque_count < 0.04:
            # On a mostly black cover, a tiny colored ornament can be much
            # more saturated than the album's substantial title/illustration.
            # Keep a lone 1% accent on genuinely monochrome art, but when a
            # real second pigment occupies the cover, use that pigment for
            # unread text instead of amplifying the ornament into neon ink.
            substantial = next((
                group for key, group in chromatic
                if group["count"] / opaque_count >= 0.08
                and not _same_color_family(characteristic, _representative(group))
            ), None)
            if substantial is not None:
                characteristic = _representative(substantial)
    else:
        characteristic = QColor(primary)
    return MusicSurfacePalette(
        primary, secondary, characteristic, ambient_colors or (QColor(primary),),
    )

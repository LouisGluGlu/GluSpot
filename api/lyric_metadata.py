"""Pure source metadata evidence shared by selection and presentation.

Classification is read-only; callers retain raw text and source clocks.
"""
from __future__ import annotations

import json
import math
import re


def is_structured_lyric_metadata(text: str) -> bool:
    """Recognize NetEase's structured header rows without treating JSON as vocals.

    These rows carry a millisecond marker and text fragments for credits or
    source annotations. They are neither LRC lines nor timed YRC words. Keep
    the schema narrow so ordinary sung braces and unrelated JSON remain text.
    """
    value = str(text or "").strip()
    if not (value.startswith("{") and value.endswith("}")):
        return False
    try:
        row = json.loads(value)
    except (ValueError, RecursionError):
        return False
    if not isinstance(row, dict) or set(row) != {"t", "c"}:
        return False
    timestamp = row["t"]
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or isinstance(timestamp, float) and not math.isfinite(timestamp)
    ):
        return False
    fragments = row["c"]
    return bool(fragments) and isinstance(fragments, list) and all(
        isinstance(fragment, dict)
        and set(fragment) == {"tx"}
        and isinstance(fragment["tx"], str)
        for fragment in fragments
    )


_CREDIT_LABEL = (
    r"(?:作词|填词|词曲|作曲|编曲|制作(?:人|团队|统筹)?|音乐制作人|联合制作人|配唱制作人|"
    r"监制|音乐总监|录音(?:师|工程师|室|棚)?|混音(?:师|工程师|室|助理)?|母带(?:制作|处理|工程师)?|"
    r"和声(?:编写|编配|设计)?|人声编辑|人声录音(?:工程师)?|音频编辑|音频工程师|音频制作|编程|弦乐(?:编写|编配|监制|录音)?|"
    r"吉他|贝斯|贝司|鼓|钢琴|键盘|管弦乐|乐器演奏|乐团|乐队|指挥|出品(?:人|公司)?|发行(?:公司)?|策划|统筹|企划|"
    r"演唱|原唱|翻译|译者|封面(?:设计|制作)?|版权|专辑|"
    r"album|lyrics?|lyricist|composer|composition|music(?:\s+director)?|arrangement|arranger|"
    r"(?:(?:executive|vocal|co)[ -]+)?producer|production|recording(?:\s+engineer)?|"
    r"mixing(?:\s+engineer)?|mix|mastering(?:\s+engineer)?|master|"
    r"guitars?|bass|drums?|keyboards?|piano|strings?|orchestra|conductor|programming|backing\s+vocals?|"
    r"translation|translator|copyright|op|sp)"
)

_CREDIT_LINE_RE = re.compile(
    rf"^\s*{_CREDIT_LABEL}(?:\s*[/＆&、+＋,，·・]\s*{_CREDIT_LABEL})*\s*[:：=]\s*\S",
    re.I,
)

# Match the entire bilingual field; 鼓 is not evidence for 鼓起勇气.
_BILINGUAL_CREDIT_LINE_RE = re.compile(
    rf"^\s*(?=[^:：=]*[\u3400-\u9fff]){_CREDIT_LABEL}"
    rf"(?:\s*[/＆&、+＋,，·・]\s*{_CREDIT_LABEL})*"
    r"\s*[A-Za-z][A-Za-z0-9 .&'’/＋+\-]{0,119}\s*[:：=]\s*\S",
    re.I,
)

_PARENTHETICAL_VOICE_RE = re.compile(r"^\s*(?:和声|合声|伴唱|合唱|backing\s+vocals?)\s*[:：]", re.I)

# Space/dash-separated role lists require an unmistakable production role.
_CREDIT_ROLE_LIST_RE = re.compile(
    r"^\s*(?:作词|填词|词曲|作曲|编曲|制作人|音乐制作人|联合制作人|配唱制作人|"
    r"监制|音乐总监|录音师|录音工程师|混音师|混音工程师|母带工程师|"
    r"和声编写|和声编配|人声编辑|音频编辑|封面设计|"
    r"lyricist|composer|arranger|(?:executive|vocal|co)[ -]+producer|"
    r"(?:recording|mixing|mastering)\s+engineer)"
    r"(?:\s+|\s*[—–-]\s*)\S",
    re.I,
)

_COPYRIGHT_NOTICE_RE = re.compile(
    r"^\s*(?:[©℗]\s*(?:\d{4}\b\s*)?\S|all\s+rights\s+reserved\b|"
    r"未经(?:著作权人|版权方|许可|授权).{0,24}(?:不得|禁止)|"
    r"本(?:歌曲|作品|录音).{0,16}(?:版权|著作权)(?:归|由|属))",
    re.I,
)

_CREDIT_BY_RE = re.compile(
    r"^\s*(?:lyrics?|music|written|composed|arranged|produced|mixed|mastered|"
    r"recorded|translated)\s+by\s*[:：]?\s*\S",
    re.I,
)

_LRC_METADATA_RE = re.compile(r"^\s*\[(?:ar|al|ti|by|offset|length)\s*:[^\]]*\]\s*$", re.I)


def is_explicit_lyric_credit(text: str) -> bool:
    value = str(text or "").strip()
    # Older cached/provider snapshots can still carry NetEase's JSON header
    # rows. They are metadata in every layout, regardless of intro cleanup.
    if is_structured_lyric_metadata(value):
        return True
    # An enclosing pair is commonly used for an entire credit, but an
    # ordinary sung parenthesis remains untouched when its content has no
    # explicit production evidence.
    for left, right in (("[", "]"), ("【", "】"), ("［", "］"), ("(", ")"), ("（", "）")):
        if value.startswith(left) and value.endswith(right):
            value = value[1:-1].strip()
            if left in ("(", "（") and _PARENTHETICAL_VOICE_RE.match(value):
                return False
            break
    return bool(
        _CREDIT_LINE_RE.match(value) or _BILINGUAL_CREDIT_LINE_RE.match(value) or _CREDIT_BY_RE.match(value)
        or _CREDIT_ROLE_LIST_RE.match(value) or _COPYRIGHT_NOTICE_RE.match(value)
        or _LRC_METADATA_RE.match(text)
    )

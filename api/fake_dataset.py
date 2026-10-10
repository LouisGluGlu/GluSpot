from __future__ import annotations

from api.models import AudioFrame, ColorToken, IslandVisualData, MessageData, PluginData, TrackData


FAKE_AUDIO_FRAMES = (
    AudioFrame(0.86, 0.42, 0.20),
    AudioFrame(0.58, 0.52, 0.34),
    AudioFrame(0.30, 0.64, 0.72),
    AudioFrame(0.42, 0.46, 0.38),
    AudioFrame(0.90, 0.50, 0.24),
    AudioFrame(0.62, 0.68, 0.44),
    AudioFrame(0.34, 0.78, 0.82),
    AudioFrame(0.38, 0.42, 0.46),
    AudioFrame(0.78, 0.44, 0.30),
    AudioFrame(0.48, 0.70, 0.58),
    AudioFrame(0.28, 0.62, 0.92),
    AudioFrame(0.40, 0.50, 0.42),
    AudioFrame(0.94, 0.56, 0.28),
    AudioFrame(0.66, 0.74, 0.48),
    AudioFrame(0.36, 0.84, 0.86),
    AudioFrame(0.44, 0.48, 0.50),
    AudioFrame(0.72, 0.38, 0.24),
    AudioFrame(0.50, 0.58, 0.36),
    AudioFrame(0.26, 0.70, 0.76),
    AudioFrame(0.36, 0.46, 0.44),
    AudioFrame(0.88, 0.52, 0.30),
    AudioFrame(0.60, 0.82, 0.54),
    AudioFrame(0.32, 0.76, 0.96),
    AudioFrame(0.46, 0.54, 0.50),
)


FAKE_TRACK = TrackData(
    title="Island Afterglow — Long Night Version",
    artist="CCfirst Prototype Orchestra",
    lyrics=(
        "如果云层是你眼中的潮汐",
        "我就把夜色折成一座岛",
        "让每一句歌词沿着光慢慢靠近",
        "风穿过 Finder 的边缘",
        "把音乐藏进玻璃的心跳",
        "下一秒我们就会一起醒来",
    ),
    album_color=ColorToken(72, 165, 255),
    secondary_color=ColorToken(186, 88, 255),
    audio_frames=FAKE_AUDIO_FRAMES,
)


FAKE_MESSAGES = (
    MessageData(
        app="微信",
        sender="林屿",
        text="V2 的消息折叠要像 iOS 背板一样收进主岛下方。",
        name_color=ColorToken(106, 255, 164),
        avatar_color=ColorToken(38, 176, 96),
    ),
    MessageData(
        app="QQ",
        sender="白昼",
        text="这次不要普通渐变，主岛必须是纯黑玻璃。",
        name_color=ColorToken(96, 186, 255),
        avatar_color=ColorToken(52, 118, 240),
    ),
    MessageData(
        app="钉钉",
        sender="产品组",
        text="17:30 前确认 Finder 融合的嵌入方向。",
        name_color=ColorToken(142, 176, 255),
        avatar_color=ColorToken(58, 106, 245),
    ),
    MessageData(
        app="微信",
        sender="林屿",
        text="音乐时消息别抢主岛，只在下面等宽出现。",
        name_color=ColorToken(106, 255, 164),
        avatar_color=ColorToken(38, 176, 96),
    ),
    MessageData(
        app="飞书",
        sender="晨星",
        text="插件岛要一行焦点式，不要上下两行表格。",
        name_color=ColorToken(255, 196, 100),
        avatar_color=ColorToken(232, 134, 42),
    ),
)


FAKE_PLUGINS = (
    PluginData(
        label="TIME",
        detail="14:32:18  UTC+8",
        summary="14:32:18",
        color=ColorToken(0, 210, 255),
        icon="◷",
    ),
    PluginData(
        label="SKY",
        detail="24°C  晴空朗朗，2h 后有雨",
        summary="24°C 晴",
        color=ColorToken(255, 214, 0),
        icon="☼",
    ),
    PluginData(
        label="NEXT",
        detail="设计评审  还有 28 分钟",
        summary="28m 后",
        color=ColorToken(181, 0, 255),
        icon="◇",
    ),
)


FAKE_VISUAL_DATA = IslandVisualData(
    track=FAKE_TRACK,
    messages=(),
    plugins=(),
)

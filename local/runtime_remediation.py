from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeRemediationAction:
    key: str
    area: str
    priority: int
    summary: str
    setting_keys: tuple[str, ...] = ()
    verify: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "area": self.area,
            "priority": int(self.priority),
            "summary": self.summary,
            "setting_keys": list(self.setting_keys),
            "verify": self.verify,
        }


def runtime_remediation_actions(
    report: Mapping[str, Any] | None,
) -> tuple[RuntimeRemediationAction, ...]:
    data = report if isinstance(report, Mapping) else {}
    status = str(data.get("status") or "")
    ready = bool(data.get("ready"))
    areas = _unique(
        str(data.get("dominant_area") or ""),
        str(data.get("bottleneck_group") or ""),
    )
    if status == "invalid":
        return (
            RuntimeRemediationAction(
                key="reexport_diagnostics",
                area="diagnostics_export",
                priority=10,
                summary="重新从 GluSpot 关于页导出完整运行诊断 JSON，再继续修复。",
                verify="python -m local.diagnostics_support <export.json> --guide-json",
            ),
        )
    if not ready:
        return (
            RuntimeRemediationAction(
                key="capture_runtime_window",
                area="runtime_evidence",
                priority=20,
                summary="保持音乐岛可见并播放 3-5 秒后重新导出诊断，先取得足够帧率、绘制、歌词和律动输入样本。",
                verify="诊断报告 status 应为 ready，trace_sample_count >= 3，trace_window_seconds >= 2.0。",
            ),
        )

    actions: list[RuntimeRemediationAction] = []
    if "runtime_contention" in areas:
        actions.extend(
            [
                RuntimeRemediationAction(
                    key="lower_reactive_intensity_for_contention",
                    area="runtime_contention",
                    priority=90,
                    summary="先把音乐律动强度降到 35% 左右，验证歌词跟进是否恢复；这是区分视觉预算压制和歌词时间线自身问题的最快开关。",
                    setting_keys=("v2_music_reactive_background_intensity",),
                    verify="重新导出诊断；dominant_area 不应继续为 runtime_contention，lyric_timeline sync_lag_lines 应回到 0 或 ±1。",
                ),
                RuntimeRemediationAction(
                    key="hold_lyric_fps_before_visual_fps",
                    area="runtime_contention",
                    priority=80,
                    summary="不要先降低歌词动画帧率；先让歌词帧率保持不低于视觉帧率，再观察 reactive_effects 是否仍占用过高预算。",
                    setting_keys=("v2_animation_fps", "v2_lyric_animation_fps"),
                    verify="重新导出诊断；visual_frame_diagnostics long_frame_count 应下降，lyric_timeline 不应继续落后。",
                ),
            ]
        )
    if "reactive_effects" in areas:
        actions.append(
            RuntimeRemediationAction(
                key="profile_reactive_renderer",
                area="reactive_effects",
                priority=70,
                summary="优先检查音乐律动渲染计划和背景绘制热点，尤其是海平面、整岛双色、双侧衍射的 side wave / interference / water band。",
                setting_keys=("v2_music_reactive_background_enabled", "v2_music_reactive_background_intensity"),
                verify="visual_paint_section_diagnostics 中 reactive_effects last_ms 应低于目标帧间隔的 40%。",
            )
        )
    if "music_wave_cadence" in areas:
        actions.append(
            RuntimeRemediationAction(
                key="separate_music_wave_cadence",
                area="music_wave_cadence",
                priority=75,
                summary="检查音乐波形更新是否又被歌词 FPS 或渲染 FPS 带着跑；wave cadence 应保持独立约 30fps，而不是贴着 60/90/120fps 渲染循环。",
                setting_keys=("v2_music_reactive_background_enabled", "v2_animation_fps", "v2_lyric_animation_fps"),
                verify="music_wave_cadence_diagnostics estimated_update_fps 应接近 30，cadence_limited 应为 true，wave_to_render_fps_ratio 应低于 0.85。",
            )
        )
    if "lyric_timeline" in areas:
        actions.append(
            RuntimeRemediationAction(
                key="verify_media_position_pipeline",
                area="lyric_timeline",
                priority=65,
                summary="检查媒体位置选择和歌词时间线，不要把 line-level LRC 强行解释成逐字 karaoke。",
                setting_keys=("v2_lyric_offset_seconds",),
                verify="lyric_timeline_diagnostics visible_lyric_index 与 position_active_index 应一致或只差 1 行。",
            )
        )
    if "frame_cadence" in areas:
        actions.append(
            RuntimeRemediationAction(
                key="reduce_visual_frame_budget",
                area="frame_cadence",
                priority=60,
                summary="如果整体帧调度仍异常，将视觉 FPS 暂降到 45 进行对照，而不是直接关闭歌词刷新。",
                setting_keys=("v2_animation_fps",),
                verify="visual_frame_diagnostics jitter_ms 和 long_frame_count 应下降。",
            )
        )
    if "reactive_input" in areas:
        actions.append(
            RuntimeRemediationAction(
                key="stabilize_reactive_input",
                area="reactive_input",
                priority=55,
                summary="先确认律动输入是 live/cache，而不是 playing_fallback；没有稳定输入时不要继续调背景绘制观感。",
                verify="music_reactive_input_diagnostics band_source 应为 live 或 cache，sampled_bands 应随音乐变化。",
            )
        )
    if actions:
        return tuple(sorted(actions, key=lambda item: (-item.priority, item.key)))
    return (
        RuntimeRemediationAction(
            key="keep_runtime_capture",
            area="runtime_evidence",
            priority=10,
            summary="当前证据没有指向明确瓶颈；保留同一曲目和背景设置再采集一轮运行诊断。",
            verify="比较两次导出的 dominant_area、bottleneck_group、frame 和 lyric 指标。",
        ),
    )


def _unique(*values: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)

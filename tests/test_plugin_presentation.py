import unittest
from datetime import datetime, timedelta, timezone

from local.plugin_presentation import (
    active_plugin_alerts,
    adjust_alarm_editor_field,
    alarm_editor_state,
    alarm_editor_state_from_row,
    alarm_frequency_units,
    alarm_picker_display_values,
    alarm_row_from_editor,
    alarm_recurrence_label,
    alarm_recurrence_value_label,
    alarm_row_matches_date,
    alarm_row_time_parts,
    alarm_unit_from_frequency,
    alarm_weekdays_from_value,
    capture_time_alarm_due_pending,
    coerce_alarm_rows,
    default_alarm_editor_state,
    days_in_month,
    enabled_plugin_ids,
    enabled_plugin_samples,
    format_timer_seconds,
    generic_plugin_alert_candidate,
    append_countdown_entry,
    handle_alarm_editor_action,
    handle_alarm_editor_key_input,
    handle_alarm_rows_action,
    handle_timer_action,
    normalize_alarm_editor_recurrence,
    pause_latest_countdown_entry,
    plugin_action_model,
    plugin_alert_display_value,
    plugin_alert_sort_key,
    plugin_alert_status_is_active,
    plugin_alert_status_priority,
    plugin_alert_text_parts,
    plugin_alert_width_units,
    plugin_compact_value,
    plugin_function_lines,
    plugin_index_for_id,
    plugin_panel_focus_model,
    plugin_panel_reminder_models,
    plugin_panel_sections,
    plugin_ring_style,
    plugin_sample_tuple,
    save_alarm_editor_rows,
    resume_latest_countdown_entry,
    strip_timer_prefix,
    taskbar_main_overlay_alert,
    time_alarm_alert_text,
    time_alarm_alert_text_from_parts,
    time_alarm_due_acknowledged_matches,
    time_alarm_side_display_mode,
    time_alarm_side_segment_text,
    time_alert_side_suppressed,
    time_alert_kind_from_plugin_id,
    time_compact_segments,
    time_countdown_active_model,
    time_countdown_entries,
    time_display_segment_text,
    time_plugin_alert_width_units,
    time_reminder_due_from_alarm,
    time_reminder_kind,
    time_reminder_display_text,
    time_side_alert_candidates,
    time_text_looks_like_countdown,
    yearly_alarm_max_day,
)


class PluginPresentationTests(unittest.TestCase):
    def test_plugin_panel_focus_model_clamps_and_names_fields(self):
        plugins = [
            ("TIME", "19:04", "Time", "blue", "T", "time", "normal", "", "", 50, 0.1),
            ("WEATHER", "24.6C", "Rain", "cyan", "W", "weather", "soon", "Alert", "Body", 30, 0.7),
        ]

        model = plugin_panel_focus_model(plugins, 9, action={"command": "weather-refresh"})

        self.assertIsNotNone(model)
        self.assertEqual(model["index"], 1)
        self.assertEqual(model["label"], "WEATHER")
        self.assertEqual(model["detail"], "24.6C")
        self.assertEqual(model["summary"], "Rain")
        self.assertEqual(model["plugin_id"], "weather")
        self.assertEqual(model["status"], "soon")
        self.assertEqual(model["alert_title"], "Alert")
        self.assertEqual(model["alert_body"], "Body")
        self.assertEqual(model["alert_priority"], 30)
        self.assertEqual(model["progress"], 0.7)
        self.assertEqual(model["action"], {"command": "weather-refresh"})

    def test_plugin_panel_focus_model_handles_empty_and_short_rows(self):
        self.assertIsNone(plugin_panel_focus_model([], 0))

        model = plugin_panel_focus_model([("TIME",)], -4)

        self.assertIsNotNone(model)
        self.assertEqual(model["index"], 0)
        self.assertEqual(model["label"], "TIME")
        self.assertEqual(model["status"], "normal")
        self.assertEqual(model["alert_priority"], 50)
        self.assertEqual(model["progress"], 0.0)

    def test_plugin_compact_value_strips_running_timer_prefix(self):
        value = plugin_compact_value("TIME", "12:05", "倒计时 4:52", "running")

        self.assertEqual(value, "4:52")

    def test_plugin_sample_tuple_normalizes_legacy_rows(self):
        self.assertEqual(
            plugin_sample_tuple(("TIME", "12:00", "Clock", "blue", "T")),
            ("TIME", "12:00", "Clock", "blue", "T", "", "normal", "", "", 50, 0.0),
        )
        self.assertEqual(
            plugin_sample_tuple(("TIME", "12:00", "Clock", "blue", "T", "time", "running")),
            ("TIME", "12:00", "Clock", "blue", "T", "time", "running", "", "", 50, 0.0),
        )
        self.assertEqual(
            plugin_sample_tuple(("WEATHER", "Rain", "Beijing", "cyan", "W", "weather", "rain", "Alert", "Body", 12)),
            ("WEATHER", "Rain", "Beijing", "cyan", "W", "weather", "rain", "Alert", "Body", 12, 0.0),
        )
        self.assertEqual(
            plugin_sample_tuple(("AGENDA", "Soon", "Meeting", "green", "A", "agenda", "soon", "Title", "Body", 8, 0.6)),
            ("AGENDA", "Soon", "Meeting", "green", "A", "agenda", "soon", "Title", "Body", 8, 0.6),
        )

    def test_enabled_plugin_ids_keeps_legacy_count_compatibility(self):
        self.assertEqual(enabled_plugin_ids(None, 2), ["time", "weather"])
        self.assertEqual(enabled_plugin_ids(None, 0), [])
        self.assertEqual(enabled_plugin_ids(None, 9), ["time", "weather", "agenda"])
        self.assertEqual(enabled_plugin_ids(None, -4), [])

    def test_enabled_plugin_ids_normalizes_explicit_set_to_known_order(self):
        self.assertEqual(
            enabled_plugin_ids({"AGENDA", "missing", "time"}, 3),
            ["time", "agenda"],
        )
        self.assertEqual(enabled_plugin_ids([], 3), [])

    def test_enabled_plugin_samples_filters_by_normalized_plugin_id(self):
        samples = [
            ("TIME", "12:00", "Clock", "blue", "T", "time", "normal"),
            ("WEATHER", "Rain", "Beijing", "cyan", "W", "weather", "rain"),
            ("AGENDA", "Soon", "Meeting", "green", "A", "agenda", "soon"),
            ("LEGACY", "Value", "Summary", "gray", "L"),
        ]

        self.assertEqual(
            enabled_plugin_samples(samples, ["agenda", "TIME"]),
            [samples[0], samples[2]],
        )
        self.assertEqual(enabled_plugin_samples(samples, []), [])

    def test_plugin_index_for_id_maps_virtual_time_ids_to_time_slot(self):
        samples = [
            ("TIME", "12:00", "Clock", "blue", "T", "time", "normal", "", "", 50, 0.0),
            ("WEATHER", "Rain", "Beijing", "cyan", "W", "weather", "rain", "Alert", "Body", 12, 0.0),
            ("AGENDA", "Soon", "Meeting", "green", "A", "agenda", "soon", "Title", "Body", 8, 0.6),
        ]

        self.assertEqual(plugin_index_for_id(samples, "weather"), 1)
        self.assertEqual(plugin_index_for_id(samples, "AGENDA"), 2)
        self.assertEqual(plugin_index_for_id(samples, "time:alarm"), 0)
        self.assertEqual(plugin_index_for_id(samples, "time:countdown"), 0)
        self.assertEqual(plugin_index_for_id(samples, ""), -1)
        self.assertEqual(plugin_index_for_id(samples, "missing"), -1)

    def test_time_alert_side_suppression_only_hides_expanded_plugin_panel(self):
        self.assertFalse(
            time_alert_side_suppressed(
                include_expanded=True,
                expanded=True,
                plugin_panel_open=True,
                main_kind_is_plugin=True,
            )
        )
        self.assertFalse(
            time_alert_side_suppressed(
                include_expanded=False,
                expanded=True,
                plugin_panel_open=True,
                main_kind_is_plugin=False,
            )
        )
        self.assertTrue(
            time_alert_side_suppressed(
                include_expanded=False,
                expanded=True,
                plugin_panel_open=True,
                main_kind_is_plugin=True,
            )
        )

    def test_time_alarm_side_display_mode_uses_setting_override_and_aliases(self):
        for value in ("time", "deadline", "due", "clock"):
            self.assertEqual(time_alarm_side_display_mode("note", value), "time")

        self.assertEqual(time_alarm_side_display_mode("time", "note"), "note")
        self.assertEqual(time_alarm_side_display_mode("time", ""), "time")
        self.assertEqual(time_alarm_side_display_mode("", None), "note")
        self.assertEqual(time_alarm_side_display_mode("unknown", None), "note")

    def test_timer_text_helpers_extract_value_and_cap_notes(self):
        self.assertEqual(strip_timer_prefix("Countdown 0:24"), "0:24")
        self.assertEqual(strip_timer_prefix("倒计时 4:52"), "4:52")

        capped = time_reminder_display_text("prepare materials and stretch the note across the island")

        self.assertLessEqual(len(capped), 18)
        self.assertTrue(capped.endswith("..."))

    def test_plugin_alert_display_value_uses_value_only_for_time_alerts(self):
        running = plugin_alert_display_value("TIME", "12:05:49", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")
        done = plugin_alert_display_value("TIME", "", "", "done")
        weather = plugin_alert_display_value("WEATHER", "26°C 晴", "北京", "normal")

        self.assertEqual(running, "0:24")
        self.assertEqual(done, "时间到")
        self.assertEqual(weather, "26°C 晴")

    def test_plugin_alert_status_rules_are_shared_for_sorting_and_activation(self):
        self.assertTrue(plugin_alert_status_is_active("due"))
        self.assertTrue(plugin_alert_status_is_active("loading"))
        self.assertFalse(plugin_alert_status_is_active("normal"))
        self.assertLess(plugin_alert_status_priority("due"), plugin_alert_status_priority("running"))
        self.assertEqual(plugin_alert_status_priority("normal"), 99)
        self.assertEqual(plugin_alert_sort_key(4, "alarm", 2), (4, 2, 2))

    def test_active_plugin_alert_rules_filter_sort_and_limit_candidates(self):
        idle = generic_plugin_alert_candidate(
            index=0,
            label="WEATHER",
            detail="24C",
            summary="Clear",
            color="cyan",
            icon="W",
            plugin_id="weather",
            status="normal",
        )
        weather = generic_plugin_alert_candidate(
            index=1,
            label="WEATHER",
            detail="Rain",
            summary="Beijing",
            color="cyan",
            icon="W",
            plugin_id="weather",
            status="rain",
            alert_priority=12,
        )
        agenda = generic_plugin_alert_candidate(
            index=2,
            label="AGENDA",
            detail="Meeting",
            summary="Soon",
            color="green",
            icon="A",
            plugin_id="agenda",
            status="soon",
            alert_title="Upcoming",
            alert_body="Meeting",
            alert_priority=8,
        )
        time = (
            3,
            plugin_alert_status_priority("alarm"),
            0,
            "TIME",
            "Standup",
            "闹钟",
            "yellow",
            "T",
            "time:alarm",
            "alarm",
            "闹钟",
            "Standup",
            3,
        )

        alerts = active_plugin_alerts([candidate for candidate in (weather, agenda, time) if candidate is not None], limit=2)

        self.assertIsNone(idle)
        self.assertEqual([alert[6] for alert in alerts], ["time:alarm", "agenda"])
        self.assertEqual(active_plugin_alerts([time], limit=-1), [])

    def test_taskbar_main_overlay_alert_selects_due_time_only_in_compact(self):
        weather = (1, "WEATHER", "Rain", "Beijing", "cyan", "W", "weather", "severe", "Alert", "Body", 12)
        running = (2, "TIME", "0:24", "Countdown 0:24", "yellow", "T", "time", "running", "Timer", "0:24", 30)
        due = (3, "TIME", "0:00", "Time up", "yellow", "T", "time", "done", "Timer", "Time up", 0)

        self.assertIs(taskbar_main_overlay_alert([weather, running, due], taskbar_compact_active=True), due)
        self.assertIsNone(taskbar_main_overlay_alert([due], taskbar_compact_active=False))
        self.assertIsNone(taskbar_main_overlay_alert([weather, running], taskbar_compact_active=True))

    def test_plugin_alert_width_units_are_pure_display_rules(self):
        self.assertEqual(plugin_alert_width_units("WEATHER", "Thunderstorm", "Beijing"), 166.0)
        self.assertEqual(plugin_alert_width_units("AGENDA", "x" * 80, "y" * 80), 172.0)
        self.assertEqual(time_plugin_alert_width_units("00:24", "Countdown 0:24"), 150.0)
        self.assertEqual(time_plugin_alert_width_units("12:34:56", ""), 156.0)
        self.assertEqual(time_plugin_alert_width_units("", "Gym"), 150.0)
        self.assertEqual(time_plugin_alert_width_units("", "alarm note that is too long"), 168.0)
        self.assertEqual(plugin_alert_width_units("TIME", "12:34:56", ""), 156.0)

    def test_time_reminder_kind_classifies_alarm_context(self):
        self.assertEqual(time_reminder_kind("alarm", ""), "alarm")
        self.assertEqual(time_reminder_kind("", "周会闹钟"), "alarm")
        self.assertEqual(time_reminder_kind("running", "倒计时 0:24"), "countdown")
        self.assertEqual(time_alert_kind_from_plugin_id("time:alarm"), "alarm")
        self.assertEqual(time_alert_kind_from_plugin_id("time"), "countdown")

    def test_time_alarm_alert_text_filters_countdown_noise(self):
        self.assertEqual(time_alarm_alert_text("提醒：下午茶时间很长"), "下午茶时间很长")
        self.assertEqual(time_alarm_alert_text("18:30 闹钟提醒：吃饭"), "吃饭")
        self.assertEqual(time_alarm_alert_text("Countdown 0:24"), "闹钟")
        self.assertEqual(time_alarm_alert_text(""), "闹钟")
        self.assertEqual(time_alarm_alert_text_from_parts("Countdown 0:24", "会议"), "会议")

    def test_time_text_looks_like_countdown_detects_numeric_and_keyword_forms(self):
        self.assertTrue(time_text_looks_like_countdown("0:24"))
        self.assertTrue(time_text_looks_like_countdown("计时结束"))
        self.assertTrue(time_text_looks_like_countdown("timer done"))
        self.assertFalse(time_text_looks_like_countdown("下午茶时间很长"))

    def test_alarm_due_pending_capture_rules_are_pure(self):
        alarm_model = {"status": "alarm", "summary": "07:30", "alert_body": "Standup"}
        captured = capture_time_alarm_due_pending(alarm_model, captured_at=42.5)

        self.assertTrue(captured["set_pending"])
        self.assertFalse(captured["clear_acknowledged"])
        self.assertEqual(captured["pending"]["time"], "07:30")
        self.assertEqual(captured["pending"]["label"], "Standup")
        self.assertEqual(captured["pending"]["key"], "07:30|Standup")
        self.assertEqual(captured["pending"]["captured_at"], 42.5)
        self.assertTrue(time_alarm_due_acknowledged_matches("07:30", "Standup", "07:30|Standup"))
        self.assertTrue(time_alarm_due_acknowledged_matches("", "Standup", "07:30|Standup"))

        acknowledged = capture_time_alarm_due_pending(alarm_model, acknowledged_key="07:30|Standup", captured_at=42.5)
        countdown_noise = capture_time_alarm_due_pending(
            {"status": "alarm", "summary": "Countdown 0:24", "alert_body": "Countdown 0:24"},
            captured_at=42.5,
        )
        with_existing = capture_time_alarm_due_pending(
            {"status": "normal"},
            existing_pending={"time": "07:30", "label": "Standup", "key": "07:30|Standup"},
            acknowledged_key="07:30|Standup",
        )
        without_existing = capture_time_alarm_due_pending({"status": "normal"}, acknowledged_key="07:30|Standup")

        self.assertFalse(acknowledged["set_pending"])
        self.assertEqual(acknowledged["pending"], {})
        self.assertFalse(countdown_noise["set_pending"])
        self.assertEqual(countdown_noise["pending"], {})
        self.assertFalse(with_existing["clear_acknowledged"])
        self.assertTrue(without_existing["clear_acknowledged"])

    def test_time_side_alert_candidate_rules_split_alarm_and_countdown(self):
        pending_and_countdown = time_side_alert_candidates(
            index=0,
            label="TIME",
            detail="10:11",
            summary="Countdown 4:59",
            color="yellow",
            icon="T",
            plugin_id="time",
            status="running",
            alert_title="Timer running",
            alert_body="Countdown 4:59",
            alert_priority=30,
            pending_alarm={"time": "07:30", "label": "Standup", "key": "07:30|Standup"},
            compact_segments=[
                {"kind": "time", "text": "10:11"},
                {"kind": "countdown", "text": "4:59", "status": "running"},
            ],
        )
        countdown_only = time_side_alert_candidates(
            index=0,
            label="TIME",
            detail="00:52",
            summary="Countdown 4:58",
            color="yellow",
            icon="T",
            plugin_id="time",
            status="alarm",
            alert_title="Alarm",
            alert_body="Countdown 4:58",
            alert_priority=3,
            compact_segments=[],
        )
        done_alarm = time_side_alert_candidates(
            index=0,
            label="TIME",
            detail="07:30",
            summary="05/29 Fri",
            color="yellow",
            icon="T",
            plugin_id="time",
            status="done",
            alert_title="Alarm",
            alert_body="Standup",
            alert_priority=3,
            pending_alarm={"time": "07:30", "label": "Old", "key": "07:30|Old"},
            compact_segments=[],
        )

        self.assertEqual([candidate[8] for candidate in pending_and_countdown], ["time:alarm", "time"])
        self.assertEqual([candidate[9] for candidate in pending_and_countdown], ["alarm", "running"])
        self.assertEqual([candidate[11] for candidate in pending_and_countdown], ["Standup", "4:59"])
        self.assertEqual([candidate[8] for candidate in countdown_only], ["time"])
        self.assertEqual(countdown_only[0][11], "4:58")
        self.assertEqual([candidate[8] for candidate in done_alarm], ["time:alarm"])
        self.assertEqual(done_alarm[0][11], "Standup")

    def test_time_alarm_side_segment_text_uses_configured_channel(self):
        due = datetime(2026, 6, 28, 7, 30, tzinfo=timezone.utc)

        self.assertEqual(time_alarm_side_segment_text({"text": "Standup", "due_at": due}, "time"), "07:30")
        self.assertEqual(time_alarm_side_segment_text({"text": "Standup", "due_at": due}, "note"), "Standup")

    def test_alarm_due_rules_parse_and_match_recurrence(self):
        monday = datetime(2026, 6, 29, 9, 0, tzinfo=timezone.utc)
        leap_day = datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc)

        self.assertEqual(alarm_row_time_parts({"time": " 09:30 "}), (9, 30))
        self.assertIsNone(alarm_row_time_parts({"time": "24:00"}))
        self.assertEqual(alarm_unit_from_frequency("每闰年"), "yearly")
        self.assertEqual(alarm_weekdays_from_value("周一三"), [1, 3])
        self.assertEqual(alarm_weekdays_from_value("周末"), [6, 7])
        self.assertEqual(days_in_month(2028, 2), 29)
        self.assertEqual(days_in_month(2027, 2), 28)
        self.assertTrue(alarm_row_matches_date({"frequency_unit": "weekly", "weekdays": [1, 3]}, monday))
        self.assertTrue(alarm_row_matches_date({"frequency_unit": "monthly", "month_day": 29}, leap_day))
        self.assertTrue(alarm_row_matches_date({"frequency_unit": "yearly", "year_month": 2, "year_day": 29}, leap_day))

    def test_time_reminder_due_from_alarm_finds_next_leap_day(self):
        now = datetime(2027, 3, 1, 8, 0, tzinfo=timezone.utc)

        due = time_reminder_due_from_alarm(
            {
                "time": "09:00",
                "enabled": True,
                "frequency_unit": "yearly",
                "year_month": 2,
                "year_day": 29,
            },
            now,
        )

        self.assertEqual(due, datetime(2028, 2, 29, 9, 0, tzinfo=timezone.utc))

    def test_alarm_recurrence_labels_and_normalization_are_pure_rules(self):
        source = {"frequency_unit": "yearly", "year_month": 14, "year_day": 31, "note": "keep"}

        normalized = normalize_alarm_editor_recurrence(source, boundary_year=2027)

        self.assertEqual(source["year_month"], 14)
        self.assertEqual(normalized["year_month"], 2)
        self.assertEqual(normalized["year_day"], 29)
        self.assertEqual(normalized["frequency"], "每闰年")
        self.assertEqual(alarm_recurrence_label({"frequency_unit": "monthly", "month_day": 31}), "每大月")
        self.assertEqual(alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": [1, 2, 3, 4, 5, 6, 7]}), "整周")
        self.assertEqual(alarm_recurrence_value_label(normalized), "2月29日")
        self.assertEqual(yearly_alarm_max_day(2, 2027), 29)

    def test_alarm_editor_adjustment_rules_return_state_and_animation_intent(self):
        source = {"hour": 23, "minute": 1, "frequency": "每周", "frequency_unit": "weekly", "year_month": 3, "year_day": 31}

        hour = adjust_alarm_editor_field(source, "hour", 2, boundary_year=2027)
        minute = adjust_alarm_editor_field(source, "minute", -2, boundary_year=2027)
        frequency = adjust_alarm_editor_field(source, "frequency", 1, boundary_year=2027)
        unit = adjust_alarm_editor_field(source, "frequency_unit", -1, boundary_year=2027)
        year_month = adjust_alarm_editor_field(source, "year_month", -1, boundary_year=2027)
        unknown = adjust_alarm_editor_field(source, "missing", 1, boundary_year=2027)

        self.assertEqual(source["hour"], 23)
        self.assertEqual(hour["editor"]["hour"], 1)
        self.assertEqual(minute["editor"]["minute"], 59)
        self.assertEqual(frequency["editor"]["frequency"], "每天")
        self.assertEqual(frequency["editor"]["frequency_unit"], "daily")
        self.assertEqual(unit["editor"]["frequency_unit"], "daily")
        self.assertEqual(unit["reflow"], {"from": "weekly", "to": "daily", "steps": -1})
        self.assertEqual(year_month["editor"]["year_month"], 2)
        self.assertLessEqual(year_month["editor"]["year_day"], 29)
        self.assertFalse(unknown["adjusted"])

    def test_alarm_picker_display_values_are_pure_rules(self):
        hour_values = alarm_picker_display_values({"hour": 15}, "hour", boundary_year=2027)
        frequency_values = alarm_picker_display_values({"frequency": "每周"}, "frequency", boundary_year=2027)
        unit_values = alarm_picker_display_values({"frequency_unit": "daily"}, "frequency_unit", boundary_year=2027)
        weekday_values = alarm_picker_display_values({"weekdays": [1, 2, 3, 4, 5, 6, 7]}, "weekdays", boundary_year=2027, offset=0.25)
        leap_values = alarm_picker_display_values({"year_month": 2, "year_day": 31}, "year_day", boundary_year=2027)

        center = len(hour_values) // 2
        self.assertEqual(alarm_frequency_units()[0], ("daily", "每天"))
        self.assertEqual([item["text"] for item in hour_values[center - 1 : center + 2]], ["14", "15", "16"])
        self.assertEqual([item["text"] for item in frequency_values[center - 1 : center + 2]], ["仅一次", "每周", "每天"])
        self.assertEqual(unit_values[center]["text"], "每天")
        self.assertEqual(weekday_values[center]["text"], "整周")
        self.assertTrue(all(float(item.get("offset", 0.0)) == 0.25 for item in weekday_values))
        self.assertEqual(leap_values[center]["text"], "29")

    def test_alarm_editor_key_input_rules_return_state_and_intents(self):
        editor = {"hour": 0, "minute": 0, "note": "", "frequency_unit": "monthly", "month_day": 1, "year_month": 1, "year_day": 1}

        hour_first = handle_alarm_editor_key_input(editor, "hour", "2", input_buffers={}, boundary_year=2027)
        hour_second = handle_alarm_editor_key_input(hour_first["editor"], "hour", "3", input_buffers=hour_first["input_buffers"], boundary_year=2027)
        day_first = handle_alarm_editor_key_input(editor, "month_day", "3", input_buffers={}, boundary_year=2027)
        day_second = handle_alarm_editor_key_input(day_first["editor"], "month_day", "1", input_buffers=day_first["input_buffers"], boundary_year=2027)
        note = handle_alarm_editor_key_input({"note": "早"}, "note", "课", input_buffers={}, boundary_year=2027)
        backspace = handle_alarm_editor_key_input(note["editor"], "note", "", "backspace", input_buffers={}, boundary_year=2027)
        over_limit = handle_alarm_editor_key_input({"note": "一二三四五六七八九十"}, "note", "余", input_buffers={}, boundary_year=2027)
        save = handle_alarm_editor_key_input(editor, "note", "", "enter", input_buffers={}, boundary_year=2027)

        self.assertEqual(hour_second["editor"]["hour"], 23)
        self.assertEqual(hour_second["input_buffers"]["hour"], "23")
        self.assertEqual(hour_second["spin_field"], "hour")
        self.assertEqual(day_second["editor"]["month_day"], 31)
        self.assertEqual(day_second["editor"]["frequency_unit"], "monthly")
        self.assertEqual(backspace["editor"]["note"], "早")
        self.assertEqual(over_limit["note_hint"], "最多10字")
        self.assertTrue(over_limit["handled"])
        self.assertTrue(save["save"])
        self.assertEqual(editor["hour"], 0)

    def test_alarm_editor_save_rows_are_pure_rules(self):
        rows = [{"time": "06:30", "label": "Old", "enabled": False, "frequency_unit": "daily"}]

        inserted = save_alarm_editor_rows({"hour": 7, "minute": 30, "note": "早课", "frequency_unit": "daily"}, rows, boundary_year=2027)
        updated = save_alarm_editor_rows(
            {"hour": 8, "minute": 5, "note": "改", "frequency_unit": "weekly", "weekdays": [2, 4], "edit_index": 0},
            rows,
            boundary_year=2027,
        )
        row = alarm_row_from_editor({"hour": 9, "minute": 1, "note": "", "frequency_unit": "daily"}, boundary_year=2027)

        self.assertEqual(inserted[0]["time"], "07:30")
        self.assertEqual(inserted[0]["label"], "早课")
        self.assertEqual(len(inserted), 2)
        self.assertEqual(rows[0]["time"], "06:30")
        self.assertEqual(updated[0]["time"], "08:05")
        self.assertEqual(updated[0]["enabled"], False)
        self.assertEqual(updated[0]["weekdays"], [2, 4])
        self.assertEqual(row["label"], "闹钟")

    def test_alarm_editor_state_rules_are_pure(self):
        now = datetime(2026, 6, 28, 10, 20, tzinfo=timezone.utc)

        default = default_alarm_editor_state(now)
        partial = alarm_editor_state({"frequency": "每天"}, now=now)
        from_row = alarm_editor_state_from_row(
            2,
            {"time": "06:45", "label": "Gym", "frequency": "workdays", "weekdays": "Mon-Fri"},
            now=now,
        )
        fallback = alarm_editor_state_from_row(1, {"time": "bad", "label": "Fallback"}, now=now)

        self.assertEqual(default["hour"], 10)
        self.assertEqual(default["minute"], 20)
        self.assertEqual(default["frequency_unit"], "weekly")
        self.assertEqual(partial["frequency_unit"], "daily")
        self.assertEqual(partial["weekdays"], [2, 4])
        self.assertEqual(from_row["hour"], 6)
        self.assertEqual(from_row["minute"], 45)
        self.assertEqual(from_row["note"], "Gym")
        self.assertEqual(from_row["frequency"], "workdays")
        self.assertEqual(from_row["weekdays"], "Mon-Fri")
        self.assertEqual(from_row["edit_index"], 2)
        self.assertEqual(fallback["hour"], 10)
        self.assertEqual(fallback["minute"], 20)

    def test_alarm_row_coercion_is_pure(self):
        rows = {
            "alarms": [
                {"time": "25:00", "label": "invalid"},
                {"time": "6:05", "note": "Morning", "frequency_unit": "weekly", "weekdays": []},
                {"time": "08:30", "label": "Leap", "frequency_unit": "yearly", "year_month": 2, "year_day": 31},
            ]
        }

        coerced = coerce_alarm_rows(rows, boundary_year=2027)

        self.assertEqual(len(coerced), 2)
        self.assertEqual(coerced[0]["time"], "06:05")
        self.assertEqual(coerced[0]["label"], "Morning")
        self.assertEqual(coerced[0]["weekdays"], [1])
        self.assertEqual(coerced[1]["time"], "08:30")
        self.assertEqual(coerced[1]["year_day"], 29)
        self.assertEqual(coerce_alarm_rows("bad", boundary_year=2027), [])

    def test_alarm_editor_action_rules_return_state_and_intents(self):
        editor = {
            "hour": 15,
            "minute": 22,
            "note": "",
            "frequency_unit": "weekly",
            "frequency": "每周",
            "weekdays": [2, 4],
            "editor_page": "main",
            "detail_mode": "",
        }

        focus = handle_alarm_editor_action(editor, "alarm-editor-focus-hour", boundary_year=2027)
        page = handle_alarm_editor_action(editor, "alarm-editor-page-note", boundary_year=2027)
        detail = handle_alarm_editor_action(editor, "alarm-editor-open-weekdays", boundary_year=2027)
        toggle = handle_alarm_editor_action(detail["editor"], "alarm-editor-toggle-weekday-6", boundary_year=2027)
        confirm = handle_alarm_editor_action(toggle["editor"], "alarm-editor-detail-confirm", boundary_year=2027)
        missing = handle_alarm_editor_action(editor, "alarm-editor-missing", boundary_year=2027)

        self.assertEqual(editor["editor_page"], "main")
        self.assertEqual(focus["focus"], "hour")
        self.assertTrue(focus["clear_input_buffers"])
        self.assertEqual(page["editor"]["editor_page"], "note")
        self.assertEqual(page["page_animation"], {"from": "main", "to": "note", "steps": 1})
        self.assertEqual(detail["editor"]["detail_mode"], "weekly")
        self.assertEqual(toggle["editor"]["weekdays"], [2, 4, 6])
        self.assertEqual(toggle["spin_field"], "weekday_6")
        self.assertEqual(confirm["editor"]["detail_mode"], "")
        self.assertEqual(confirm["editor"]["editor_page"], "main")
        self.assertEqual(confirm["focus"], "")
        self.assertFalse(missing["handled"])

    def test_alarm_rows_action_rules_toggle_and_delete(self):
        rows = [
            {"time": "06:30", "label": "Old", "enabled": False},
            {"time": "07:30", "label": "New", "enabled": True},
        ]

        toggled = handle_alarm_rows_action(rows, "alarm-toggle-0")
        deleted = handle_alarm_rows_action(rows, "alarm-delete-1")
        invalid_toggle = handle_alarm_rows_action(rows, "alarm-toggle-5")
        invalid_delete = handle_alarm_rows_action(rows, "alarm-delete-5")
        missing = handle_alarm_rows_action(rows, "alarm-edit-0")

        self.assertFalse(rows[0]["enabled"])
        self.assertTrue(toggled["changed"])
        self.assertTrue(toggled["rows"][0]["enabled"])
        self.assertTrue(toggled["clear_delete_mode"])
        self.assertTrue(toggled["request_update"])
        self.assertEqual([row["label"] for row in deleted["rows"]], ["Old"])
        self.assertTrue(deleted["changed"])
        self.assertFalse(invalid_toggle["changed"])
        self.assertFalse(invalid_toggle["request_update"])
        self.assertFalse(invalid_delete["changed"])
        self.assertTrue(invalid_delete["request_update"])
        self.assertFalse(missing["handled"])

    def test_time_countdown_active_model_and_entries_are_pure_rules(self):
        now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)

        paused = time_countdown_active_model({"status": "idle"}, paused_remaining=249, paused_progress=1.4)
        running = time_countdown_active_model(
            {"status": "running", "summary": "倒计时 4:59", "progress": 0.45},
            paused_remaining=0,
        )
        done = time_countdown_active_model({"status": "done", "summary": "Countdown 0:00"})
        entries = time_countdown_entries(
            [
                {"text": "14:59", "progress": 0.10, "status": "running"},
                {"started_at": "2026-06-28T09:55:00+00:00", "end_at": "2026-06-28T10:05:00+00:00", "status": "running"},
                {"text": "", "status": "paused", "progress": 0.5},
            ],
            {"active": False},
            now=now,
            paused_remaining=120,
        )

        self.assertEqual(format_timer_seconds(3661), "1:01:01")
        self.assertEqual(paused, {"active": True, "text": "4:09", "progress": 1.0, "status": "paused"})
        self.assertEqual(running["text"], "4:59")
        self.assertAlmostEqual(running["progress"], 0.45)
        self.assertEqual(done["text"], "完成")
        self.assertEqual([entry["status"] for entry in entries], ["paused", "running"])
        self.assertEqual(entries[0]["text"], "2:00")
        self.assertEqual(entries[1]["text"], "5:00")
        self.assertAlmostEqual(entries[1]["progress"], 0.5)

    def test_timer_entry_action_rules_are_pure(self):
        now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
        previous = [
            {"minutes": 5, "started_at": "old", "end_at": "old", "status": "running"},
            {"minutes": 10, "started_at": "old", "end_at": "old", "status": "running"},
        ]

        appended = append_countdown_entry(previous, 20, now, now + timedelta(minutes=20))
        paused = pause_latest_countdown_entry(appended, 125, 1.2)
        resumed = resume_latest_countdown_entry(paused, 125, now, now + timedelta(seconds=125))
        fallback_resumed = resume_latest_countdown_entry([], 125, now, now + timedelta(seconds=125))

        self.assertEqual([entry["minutes"] for entry in appended], [10, 20])
        self.assertEqual(previous[-1]["minutes"], 10)
        self.assertEqual(paused[-1]["text"], "2:05")
        self.assertEqual(paused[-1]["status"], "paused")
        self.assertNotIn("end_at", paused[-1])
        self.assertEqual(paused[-1]["progress"], 1.0)
        self.assertEqual(resumed[-1]["status"], "running")
        self.assertIn("end_at", resumed[-1])
        self.assertNotIn("text", resumed[-1])
        self.assertEqual(fallback_resumed[0]["minutes"], 3)

    def test_timer_action_rules_return_settings_entries_and_refresh_intents(self):
        now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
        entries = [{"minutes": 5, "started_at": "old", "end_at": "old", "status": "running"}]

        start = handle_timer_action("timer-start-999", now=now, entries=entries)
        pause = handle_timer_action(
            "timer-pause",
            now=now,
            entries=start["entries"],
            settings_remaining=240,
            settings_progress=0.25,
        )
        resume = handle_timer_action(
            "timer-resume",
            now=now,
            entries=pause["entries"],
            paused_remaining=240,
        )
        clear = handle_timer_action("timer-clear", now=now, entries=resume["entries"], paused_remaining=240)
        idle_pause = handle_timer_action("timer-pause", now=now, entries=[], settings_remaining=0)
        missing = handle_timer_action("timer-missing", now=now)

        self.assertTrue(start["handled"])
        self.assertEqual(start["entries"][-1]["minutes"], 180)
        self.assertTrue(start["clear_paused"])
        self.assertEqual(start["mode"], "dashboard")
        self.assertEqual(start["settings"]["v2_timer_started_at"], "2026-06-28T10:00:00+00:00")
        self.assertTrue(start["refresh"])
        self.assertEqual(pause["paused"], {"remaining": 240, "progress": 0.25})
        self.assertEqual(pause["settings"], {"v2_timer_started_at": "", "v2_timer_end_at": ""})
        self.assertEqual(pause["entries"][-1]["status"], "paused")
        self.assertTrue(resume["clear_paused"])
        self.assertEqual(resume["entries"][-1]["status"], "running")
        self.assertEqual(clear["entries"], [])
        self.assertTrue(clear["refresh"])
        self.assertFalse(idle_pause["refresh"])
        self.assertFalse(missing["handled"])

    def test_time_compact_segments_are_pure_display_rules(self):
        now = datetime(2026, 5, 28, 10, 11, 8, tzinfo=timezone.utc)

        segments = time_compact_segments(
            "10:11:08",
            "Countdown 4:59",
            "running",
            now=now,
            show_seconds=False,
            countdown_entries=[
                {
                    "text": "4:59",
                    "end_at": (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
                    "status": "running",
                }
            ],
            alarm_rows=[
                {"time": "10:13", "label": "Note", "enabled": True, "frequency": "daily"},
            ],
        )
        idle = time_compact_segments("05:07", "05/28 Thu", "idle", now=now, show_seconds=False, countdown_entries=[], alarm_rows=[])
        done = time_compact_segments("10:11", "", "done", now=now, countdown_entries=[], alarm_rows=[])

        self.assertEqual(time_display_segment_text("", now, show_seconds=True), "10:11:08")
        self.assertEqual(segments[0]["text"], "10:11")
        self.assertEqual([segment["kind"] for segment in segments[1:]], ["alarm", "countdown"])
        self.assertEqual(segments[1]["text"], "Note")
        self.assertEqual(idle, [{"kind": "time", "text": "05:07", "due_at": now}])
        self.assertEqual(done[1]["text"], "\u65f6\u95f4\u5230")

    def test_plugin_action_model_time_weather_agenda(self):
        time_action = plugin_action_model("TIME", "00:24", "倒计时 0:24", "running")
        weather_action = plugin_action_model("WEATHER", "26°C 晴", "北京 · 东南风", "normal")
        agenda_action = plugin_action_model("AGENDA", "4 分钟后", "准备上课", "soon")

        self.assertEqual(time_action["command"], "timer-clear")
        self.assertIn("清除", time_action["label"])
        self.assertEqual(weather_action["command"], "weather-refresh")
        self.assertIn("刷新", weather_action["label"])
        self.assertEqual(agenda_action["command"], "agenda-open")
        self.assertIn("日程", agenda_action["label"])

    def test_plugin_ring_style_marks_alert_states_as_animated(self):
        running = plugin_ring_style("running")
        severe = plugin_ring_style("severe")
        normal = plugin_ring_style("normal")

        self.assertEqual(running["width"], 3.8)
        self.assertEqual(running["track_width"], 3.2)
        self.assertEqual(severe["pulse"], 1.0)
        self.assertEqual(normal["width"], 2.2)
        self.assertEqual(normal["track_width"], 1.8)
        self.assertEqual(normal["pulse"], 0.0)

    def test_plugin_alert_text_prefers_provider_copy_and_running_summary(self):
        timer_title, timer_body = plugin_alert_text_parts("TIME", "20:00:05", "倒计时 0:24", "running")
        weather_title, weather_body = plugin_alert_text_parts("WEATHER", "北京", "天气刷新中", "loading", "天气刷新中", "北京")

        self.assertIn("计时", timer_title)
        self.assertEqual(timer_body, "倒计时 0:24")
        self.assertEqual(weather_title, "WEATHER · 天气刷新中")
        self.assertEqual(weather_body, "北京")

    def test_plugin_panel_reminder_models_use_alert_text_and_sample_progress(self):
        alerts = [
            (1, "WEATHER", "雷雨", "北京", "cyan", "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2),
            (0, "TIME", "00:24", "倒计时 0:24", "blue", "●", "time", "running", "计时进行中", "倒计时 0:24", 30),
        ]
        samples = [
            ("TIME", "00:24", "倒计时 0:24", "blue", "●", "time", "running", "计时进行中", "倒计时 0:24", 30, 1.4),
            ("WEATHER", "雷雨", "北京", "cyan", "☁", "weather", "severe", "严重天气提醒", "北京 · 雷雨", 2, 0.6),
        ]

        reminders = plugin_panel_reminder_models(alerts, samples)

        self.assertEqual([item["plugin_id"] for item in reminders], ["weather", "time"])
        self.assertIn("雷雨", reminders[0]["body"])
        self.assertEqual(reminders[0]["progress"], 0.6)
        self.assertEqual(reminders[1]["progress"], 1.0)

    def test_plugin_function_lines_include_runtime_and_alert_context(self):
        weather_title, weather_lines = plugin_function_lines("WEATHER", "26C Sunny", "Beijing wind humidity 40%", "normal")
        time_title, time_lines = plugin_function_lines("TIME", "00:24", "Countdown 0:24", "running", "Timer running", "Countdown 0:24")

        self.assertIn("天气", weather_title)
        self.assertIn("26C Sunny", weather_lines)
        self.assertNotIn("手动城市", " ".join(weather_lines))
        self.assertIn("时间", time_title)
        self.assertIn("Countdown 0:24", time_lines)

    def test_plugin_panel_sections_dispatch_time_weather_agenda_and_generic(self):
        time_sections = plugin_panel_sections(
            {
                "label": "TIME",
                "detail": "19:04:12",
                "summary": "05/27 Wed",
                "status": "idle",
                "alert_title": "",
                "alert_body": "",
            }
        )
        weather_sections = plugin_panel_sections(
            {
                "label": "WEATHER",
                "detail": "26°C 晴",
                "summary": "北京 · 东南风12km/h · 湿度 40%",
                "status": "normal",
                "alert_title": "",
                "alert_body": "",
            }
        )
        agenda_sections = plugin_panel_sections(
            {
                "label": "AGENDA",
                "detail": "28 分钟后",
                "summary": "准备上课",
                "status": "soon",
                "alert_title": "日程将近",
                "alert_body": "28 分钟后 · 准备上课",
            }
        )
        generic_sections = plugin_panel_sections({"label": "NOTE", "detail": "Ready", "summary": "Inbox", "status": "normal"})

        self.assertEqual([section["id"] for section in time_sections], ["clock", "countdown", "alarm"])
        self.assertIn("倒计时", time_sections[1]["title"])
        self.assertEqual([section["id"] for section in weather_sections], ["current", "metrics", "source"])
        self.assertIn("风", weather_sections[1]["body"])
        self.assertEqual([section["id"] for section in agenda_sections], ["next", "todo", "reminder"])
        self.assertIn("准备上课", agenda_sections[0]["body"])
        self.assertEqual([section["id"] for section in generic_sections], ["primary", "secondary", "state"])
        self.assertEqual(generic_sections[0]["title"], "NOTE")


if __name__ == "__main__":
    unittest.main()

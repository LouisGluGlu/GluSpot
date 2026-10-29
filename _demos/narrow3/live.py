"""Narrow view of the shared app runtime, using temporary session settings."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import faulthandler
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time


class _Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        if not self.log.closed:
            self.log.write(text)
        if self.stream is not None:
            self.stream.write(text)
        return len(text)

    def flush(self):
        if not self.log.closed:
            self.log.flush()
        if self.stream is not None:
            self.stream.flush()


@contextmanager
def session_log(root):
    """Retain ten launch logs beside the existing GluSpot launcher logs."""
    directory = root / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"NarrowLive-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns() % 1000000}.log"
    with path.open("x", encoding="utf-8", buffering=1) as log:
        for old in sorted(directory.glob("NarrowLive-*.log"),
                          key=lambda item: item.stat().st_mtime, reverse=True)[10:]:
            try:
                old.unlink()
            except OSError:
                pass
        with redirect_stdout(_Tee(sys.stdout, log)), redirect_stderr(_Tee(sys.stderr, log)):
            # Capture decisions use their own non-propagating stream logger.
            # Route it into this session even if a diagnostic imported it first.
            import api.capture.seek
            seek_streams = [(item, item.stream) for item in logging.getLogger("GluSpot.capture.seek").handlers
                            if isinstance(item, logging.StreamHandler)]
            for item, _stream in seek_streams:
                item.setStream(sys.stderr)
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
            logger = logging.getLogger()
            previous_level = logger.level
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            had_fault_handler = faulthandler.is_enabled()
            faulthandler.enable(file=log)
            try:
                print(f"NarrowLive log: {path}", flush=True)
                yield path
            except BaseException:
                logger.exception("NarrowLive exited with an exception")
                raise
            finally:
                faulthandler.disable()
                if had_fault_handler and sys.__stderr__ is not None:
                    faulthandler.enable(file=sys.__stderr__)
                logger.removeHandler(handler)
                logger.setLevel(previous_level)
                for item, stream in seek_streams:
                    item.setStream(stream)


def run_live(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grab", type=Path, help="Save idle/revealed frames only after real playback arrives")
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--trace", type=Path, help="Save playback, paint and visibility diagnostics on exit")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(root), str(Path(__file__).parent)]
    # Use the shared SettingsManager with an explicit temporary path; neither
    # its loader, migration nor delayed saver can touch the user's real file.
    with tempfile.TemporaryDirectory(prefix="gluspot-narrow-live-") as runtime:
        previous = {name: os.environ.get(name) for name in
                    ("GLUSPOT_LYRICS_CACHE_DIR", "GLUSPOT_ALBUM_ART_CACHE_DIR")}
        os.environ["GLUSPOT_LYRICS_CACHE_DIR"] = str(Path(runtime) / "lyrics")
        os.environ["GLUSPOT_ALBUM_ART_CACHE_DIR"] = str(Path(runtime) / "artwork")
        try:
            with session_log(root):
                return _run(args, settings_path=Path(runtime) / "settings.json")
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _run(args, *, settings_path):
    from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, qInstallMessageHandler
    from PySide6.QtGui import QCursor
    from local.app import bootstrap

    runtime = bootstrap(view="narrow", capture="attach_only", settings_path=settings_path)
    app, provider, owner, window = runtime.app, runtime.data_provider, runtime.window, runtime.view_window
    outcome = {"exit_code": 0}
    started = time.monotonic()
    evidence = []
    logger = logging.getLogger("GluSpot.NarrowLive")
    previous_qt_handler = qInstallMessageHandler(lambda kind, context, message: logger.warning("Qt: %s", message))

    class Trace(QObject):
        def __init__(self):
            super().__init__(window)
            self.paints = 0
            self.events = []
            self.samples = []
            self.last_logged = -1
            self.last_sample = 0.0
            self.backward_steps = 0
            self.visibility_retargets = 0
            self.last_target = window.visibility.footer.target

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.paints += 1
            elif event.type() in {QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange}:
                self.events.append({"at": round(time.monotonic() - started, 3), "type": event.type().name})
            return False

        def sample(self):
            now = time.monotonic()
            if now - self.last_sample < .09:
                return
            self.last_sample = now
            index, visual = window.lyric_focus()
            capture = provider.diagnostics().get("capture") or {}
            source = owner.media_position_source
            if source == "capture-model" and capture.get("position_route") == "cef":
                source = "cef-playprogress"
            sample = dict(at=round(now - started, 3), track=owner.media_track_id,
                          position=round(window.playback_position(), 4), index=index,
                          scroll=round(visual, 4), source=source,
                          owner_source=owner.media_position_source,
                          playing=owner.media_is_playing, paints=self.paints,
                          controls_target=window.visibility.footer.target)
            sample.update(window.diagnostic_snapshot())
            if self.samples:
                prior = self.samples[-1]
                if sample['track'] == prior['track'] and sample['index'] < prior['index']:
                    self.backward_steps += 1
            if sample['controls_target'] != self.last_target:
                self.visibility_retargets += 1
                self.last_target = sample['controls_target']
            self.samples.append(sample)
            second = int(now - started)
            if second != self.last_logged:
                self.last_logged = second
                logger.info("trace %s", json.dumps(sample, ensure_ascii=False))

        def report(self):
            paints = sorted(window.samples)
            elapsed = time.monotonic() - started
            return dict(duration_seconds=round(elapsed, 3), paints=self.paints,
                        paint_hz=round(self.paints / max(.001, elapsed), 2),
                        paint_p95_ms=round(paints[min(len(paints)-1, int(len(paints)*.95))], 3) if paints else None,
                        backward_index_steps=self.backward_steps,
                        controls_retargets=self.visibility_retargets,
                        visibility_events=self.events, samples=self.samples,
                        diagnostics=window.diagnostic_snapshot(),
                        capture=provider.netease_capture_health())

    trace = Trace()
    window.installEventFilter(trace)
    window.study_timer.timeout.connect(trace.sample)
    window.show()

    def save_frame(name):
        path = args.grab / name
        window.repaint()
        if not window.grab().save(str(path)):
            outcome["exit_code"] = 1
            app.quit()
            return
        evidence.append({
            "path": str(path), "track_id": owner.media_track_id,
            "title": owner.track_title, "artist": owner.artist,
            "position_seconds": owner._estimated_media_position(time.monotonic()),
            "position_source": owner.media_position_source,
            "position_sampled_at": owner.media_position_sampled_at,
            "timed_lyric_rows": len(owner.timed_lyrics),
            "controls_opacity": window.visibility.footer.value(time.monotonic()),
        })
        print("saved", path, flush=True)

    def reveal():
        from PySide6.QtTest import QTest
        QTest.mouseMove(window, QPoint(window.width() // 2, window.height() // 2))
        QTimer.singleShot(350, finish_grab)

    def finish_grab():
        save_frame("narrow_B_mouse_moved.png")
        report = {"source": "shared PlaybackSourceProvider", "platform": sys.platform,
                  "capture_relaunch_enabled": False,
                  "render": "native Qt window grab", "frames": evidence}
        (args.grab / "live-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.quit()

    def idle_grab():
        save_frame("narrow_A_idle.png")
        reveal()

    def await_playback():
        # A dependency/empty fallback is not a real playback screenshot.
        ready = (owner.media_is_playing and owner.media_duration_seconds > 0
                 and owner.media_source_app not in {"", "dependency", "system"}
                 and owner.media_position_source not in {"", "none", "capture-unavailable"}
                 and bool(window.source_lines()))
        if ready:
            wait.stop()
            QCursor.setPos(window.mapToGlobal(QPoint(-40, -40)))
            window._pointer_active_until = 0.0
            window.shelf = window.footer_hover = False
            QTimer.singleShot(1700, idle_grab)
        elif time.monotonic() - started >= 40:
            print("No real playback snapshot arrived; screenshots were not generated.", file=sys.stderr)
            outcome["exit_code"] = 2
            app.quit()

    wait = QTimer(window)
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        wait.timeout.connect(await_playback)
        wait.start(100)
    if args.seconds:
        QTimer.singleShot(round(args.seconds * 1000), app.quit)
    try:
        runtime.run()
    finally:
        window.study_timer.stop()
        try:
            report = trace.report()
            logger.info("summary %s", json.dumps({key: value for key, value in report.items() if key != 'samples'}, ensure_ascii=False))
            if getattr(args, "trace", None):
                args.trace.parent.mkdir(parents=True, exist_ok=True)
                args.trace.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            window.shutdown()
            window.close()
            owner._application_quitting = True
            owner.close()
            runtime.settings.save_now()
            qInstallMessageHandler(previous_qt_handler)
    return outcome["exit_code"]


if __name__ == "__main__":
    raise SystemExit(run_live())

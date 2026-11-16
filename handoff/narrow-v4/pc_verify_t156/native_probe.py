"""Run the exact committed shared probe; use this bundle's physical observer."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO = Path(r"E:\grok-gluspot\_wt_capture2")

if __name__ == "__main__":
    spec = spec_from_file_location("gluspot_t156_native_probe",
        REPO / "handoff/narrow-v4/pc_verify/native_probe.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    raise SystemExit(module.main(observer_script=Path(__file__).with_name("screen_observer.py")))

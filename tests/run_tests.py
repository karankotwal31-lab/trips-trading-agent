import importlib.util
from pathlib import Path
import traceback

path = Path(__file__).with_name("test_engine.py")
spec = importlib.util.spec_from_file_location("test_engine", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

tests = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
failed = []
for test in tests:
    try:
        test()
        print(f"PASS {test.__name__}")
    except Exception:
        failed.append(test.__name__)
        print(f"FAIL {test.__name__}")
        traceback.print_exc()
if failed:
    raise SystemExit(f"{len(failed)} tests failed: {failed}")
print(f"ALL PASS ({len(tests)} tests)")

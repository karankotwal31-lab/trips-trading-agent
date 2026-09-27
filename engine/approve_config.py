from __future__ import annotations
import json
from pathlib import Path
from config_guard import validate_config, fingerprint_config

HERE = Path(__file__).parent
cfg = validate_config(json.loads((HERE / "config.json").read_text()))
(HERE / "approved_config.sha256").write_text(fingerprint_config(cfg) + "\n")
print("Approved config fingerprint after human review and tests:", fingerprint_config(cfg))

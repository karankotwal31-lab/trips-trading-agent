from __future__ import annotations

import json
from pathlib import Path
from build_guard import MANIFEST_PATH, current_manifest

# This utility records an approval artifact; it does not replace independent review/tests.
manifest = current_manifest()
MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print("Approved Trip's executable build manifest:", manifest["manifest_hash"])

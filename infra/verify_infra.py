from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
m=json.loads((HERE/"approved_infra.json").read_text())
changed=[]
for rel,digest in m.get("files",{}).items():
    p=HERE.parent/rel
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
        changed.append(rel)
if changed:
    print(json.dumps({"ok":False,"changed":changed}),file=sys.stderr); raise SystemExit(2)
body={"schema_version":m["schema_version"],"files":m["files"]}
raw=json.dumps(body,sort_keys=True,separators=(",",":")).encode()
if hashlib.sha256(raw).hexdigest()!=m.get("manifest_hash"):
    raise SystemExit("infrastructure manifest hash invalid")
print(json.dumps({"ok":True,"manifest_hash":m["manifest_hash"]}))

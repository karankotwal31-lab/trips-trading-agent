from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# High-specificity secret signatures only; never print matching secret values.
SECRET_PATTERNS = {
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def run_security_diagnostics() -> dict:
    findings=[]
    scanned=0
    for base in (ROOT/"engine", ROOT/"tests", ROOT/"docs"):
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".py", ".json", ".md", ".html", ".css", ".js", ".yml", ".yaml", ".txt"}:
                continue
            if p.stat().st_size > 2_000_000:
                continue
            scanned += 1
            try:
                text=p.read_text(errors="ignore")
            except Exception:
                continue
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    findings.append({"type":name,"file":str(p.relative_to(ROOT)),"secret_value_exposed":False})
    env_presence={k: bool(os.getenv(k)) for k in ("ALPHA_VANTAGE_API_KEY","TWELVE_DATA_API_KEY")}
    return {
        "schema_version":1,
        "files_scanned":scanned,
        "secret_findings":findings,
        "passed":len(findings)==0,
        "provider_credentials_present":env_presence,
        "note":"Credential presence is reported as boolean only; values are never emitted."
    }


if __name__ == "__main__":
    print(json.dumps(run_security_diagnostics(), indent=2))

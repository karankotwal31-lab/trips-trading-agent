from __future__ import annotations

import argparse
from store import acknowledge_escalation

parser = argparse.ArgumentParser(description="Explicitly acknowledge one open Trip's escalation.")
parser.add_argument("escalation_id")
parser.add_argument("--note", default="Reviewed with human supervisor/user; no silent policy override.")
args = parser.parse_args()
item = acknowledge_escalation(args.escalation_id, args.note)
print(f"Acknowledged escalation {item['id']} at {item['acknowledged_at']}")

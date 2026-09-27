from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config_guard import validate_config
from diagnostics_engine import run_deep_diagnostics
from health_engine import run_health_check
from supervisor_bridge import build_supervisor_packet
from dashboard_export import export_dashboard_snapshot
from store import read_json, write_json

HERE=Path(__file__).resolve().parent


def _config():
    return validate_config(json.loads((HERE/"config.json").read_text()))


def _due_daily(now: datetime, cfg: dict, schedule_state: dict) -> bool:
    hh,mm=map(int,cfg["supervisor"]["report_local_time"].split(":"))
    due=now.replace(hour=hh,minute=mm,second=0,microsecond=0)
    return now >= due and schedule_state.get("last_daily_date") != now.date().isoformat()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--interval-seconds",type=int,default=None)
    args=ap.parse_args()
    cfg=_config()
    interval=max(60,args.interval_seconds or int(cfg["health"]["heartbeat_minutes"])*60)
    tz=ZoneInfo(cfg["supervisor"]["report_timezone"])
    schedule=read_json("guardian_schedule.json",{},strict=False) or {}
    last_hour=schedule.get("last_hour")
    while True:
        now=datetime.now(tz)
        heartbeat=run_health_check("heartbeat",apply_repairs=True,persist=True)
        # Refresh only the derived, read-only operator snapshot. Failure never mutates trading state.
        dashboard_export_status="OK"
        try:
            export_dashboard_snapshot()
        except Exception as exc:
            dashboard_export_status=f"FAILED:{type(exc).__name__}"
        print(json.dumps({"ts":now.isoformat(),"guardian":heartbeat["status"],"repairs":len(heartbeat["repairs"]),
                          "dashboard_export":dashboard_export_status}),flush=True)
        hour_key=now.strftime("%Y-%m-%dT%H")
        if hour_key!=last_hour:
            run_health_check("hourly",apply_repairs=True,persist=True)
            last_hour=hour_key
            schedule["last_hour"]=hour_key
            write_json("guardian_schedule.json",schedule)
        if _due_daily(now,cfg,schedule):
            run_deep_diagnostics()
            # build_supervisor_packet performs exactly one Evolution review for the daily packet.
            build_supervisor_packet()
            schedule["last_daily_date"]=now.date().isoformat()
            schedule["last_daily_completed_at"]=now.isoformat()
            write_json("guardian_schedule.json",schedule)
        time.sleep(interval)


if __name__=="__main__":
    main()

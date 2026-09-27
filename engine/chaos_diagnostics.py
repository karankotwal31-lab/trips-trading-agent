from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

from providers import Bar
from risk import position_size
from truth_guard import validate_bars
from supervisor_counsel import SupervisorCounselError, validate_counsel


def run_chaos(seed: int = 404, malformed_cases: int = 1000, sizing_cases: int = 20000, counsel_cases: int = 1000) -> dict:
    rng=random.Random(seed)
    invalid_sizes=0
    for _ in range(sizing_cases):
        equity=rng.uniform(100,1_000_000)
        entry=rng.uniform(1,5000)
        stop=entry-rng.uniform(0.01,max(0.02,entry*0.2))
        risk_pct=rng.uniform(0.0001,0.01)
        exposure_cap=rng.uniform(0.01,0.5)
        current=rng.uniform(0,equity*exposure_cap)
        q=position_size(equity,entry,stop,risk_pct,exposure_cap,current)
        if not isinstance(q,int) or q<0:
            invalid_sizes += 1

    false_accepts=0
    crashes=0
    start=datetime.now(timezone.utc)-timedelta(hours=70)
    for i in range(malformed_cases):
        bars=[]
        base=100+rng.random()*50
        for j in range(65):
            ts=(start+timedelta(hours=j)).isoformat()
            o=base; c=base*(1+rng.gauss(0,0.002)); hi=max(o,c)*1.002; lo=min(o,c)*0.998; v=1000
            bars.append(Bar(ts,o,hi,lo,c,v)); base=c
        b=bars[-1]
        mutation=i%6
        if mutation==0: bars[-1]=Bar(b.ts,float('nan'),b.high,b.low,b.close,b.volume)
        elif mutation==1: bars[-1]=Bar(b.ts,b.open,b.low*0.9,b.low,b.close,b.volume)
        elif mutation==2: bars[-1]=Bar(b.ts,-1,b.high,b.low,b.close,b.volume)
        elif mutation==3: bars[-1]=Bar(b.ts,b.open,b.high,b.low,b.close,-1)
        elif mutation==4: bars[-1]=Bar(bars[-2].ts,b.open,b.high,b.low,b.close,b.volume)
        else: bars[-1]=Bar("not-a-time",b.open,b.high,b.low,b.close,b.volume)
        try:
            verdict=validate_bars(source="chaos",source_family="chaos",source_kind="replay",symbol="SPY",interval="60min",
                                  bars=bars,max_age_minutes=240,min_bars=60,allow_synthetic_analysis=True)
            if verdict.trusted_for_trade:
                false_accepts += 1
        except Exception:
            crashes += 1
    unsafe_counsel_accepts=0
    counsel_crashes=0
    for i in range(counsel_cases):
        # Alternate between direct-order attempts and unverifiable current-market claims.
        if i % 2 == 0:
            counsel={"recommendation":"NO_CHANGE","packet_hashes":["known"],"verified_facts":[],"inference":[],
                     "uses_current_market_claims":False,"direct_order_instruction":"BUY NOW"}
        else:
            counsel={"recommendation":"REQUEST_MORE_EVIDENCE","packet_hashes":["known"],
                     "verified_facts":["price changed"],"inference":["trend"],
                     "uses_current_market_claims":True,"external_evidence":[],"direct_order_instruction":None}
        try:
            validate_counsel(counsel, known_packet_hashes={"known"})
            unsafe_counsel_accepts += 1
        except SupervisorCounselError:
            pass
        except Exception:
            counsel_crashes += 1

    return {
        "schema_version":1,
        "seed":seed,
        "position_size_cases":sizing_cases,
        "invalid_position_sizes":invalid_sizes,
        "malformed_ohlcv_cases":malformed_cases,
        "malformed_trade_eligible_accepts":false_accepts,
        "validator_crashes":crashes,
        "supervisor_counsel_adversarial_cases":counsel_cases,
        "unsafe_supervisor_counsel_accepts":unsafe_counsel_accepts,
        "supervisor_counsel_crashes":counsel_crashes,
        "passed":invalid_sizes==0 and false_accepts==0 and crashes==0 and unsafe_counsel_accepts==0 and counsel_crashes==0,
    }


if __name__ == "__main__":
    print(json.dumps(run_chaos(), indent=2))

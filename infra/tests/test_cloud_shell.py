from __future__ import annotations
import hashlib, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
INFRA=ROOT/"infra"
sys.path.insert(0,str(INFRA))
from cloud_state import CloudStateError, payload_sha256, validate_payload_size, MAX_RUNTIME_BYTES
from neon_state import NeonStateClient


def test_frozen_core_matches_v06_contract():
    for line in (INFRA/"core_v06.sha256").read_text().splitlines():
        digest, rel=line.split(maxsplit=1)
        p=ROOT/rel.strip()
        assert p.exists()
        assert hashlib.sha256(p.read_bytes()).hexdigest()==digest


def test_missing_neon_credentials_fail_closed():
    try:
        NeonStateClient(database_url="")
        raise AssertionError("missing Neon credential accepted")
    except CloudStateError:
        pass


def test_non_postgres_neon_url_rejected():
    try:
        NeonStateClient(database_url="https://example.test")
        raise AssertionError("non-PostgreSQL URL accepted")
    except CloudStateError:
        pass


def test_explicit_tls_disable_rejected():
    try:
        NeonStateClient(database_url="postgresql://u:p@db.example/trips?sslmode=disable")
        raise AssertionError("TLS-disabled database URL accepted")
    except CloudStateError:
        pass


def test_payload_hash_is_canonical():
    assert payload_sha256({"b":2,"a":1})==payload_sha256({"a":1,"b":2})


def test_infra_manifest_verifies():
    p=subprocess.run([sys.executable,str(INFRA/"verify_infra.py")],cwd=ROOT,capture_output=True,text=True)
    assert p.returncode==0,p.stderr


def test_neon_schema_is_least_privilege_by_design():
    sql=(INFRA/"neon_schema.sql").read_text().lower()
    assert "create role trips_runtime_exec nologin" in sql
    assert "revoke all on all tables in schema trips_cloud from public, trips_runtime_exec, trips_dashboard_exec" in sql
    assert "grant execute on function trips_cloud.commit_runtime" in sql
    assert "grant select" not in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql



def test_dashboard_capability_is_read_only_and_heartbeat_is_bounded():
    sql=(INFRA/"neon_schema.sql").read_text().lower()
    assert "create role trips_dashboard_exec nologin" in sql
    assert "grant execute on function trips_cloud.get_dashboard_snapshot() to trips_dashboard_exec" in sql
    assert "grant execute on function trips_cloud.get_latest_heartbeat() to trips_dashboard_exec" in sql
    assert "grant execute on function trips_cloud.commit_runtime" not in "\n".join(
        line for line in sql.splitlines() if "trips_dashboard_exec" in line
    )
    assert "heartbeat_detail_too_large" in sql
    assert "v_id - 10000" in sql

def test_cloud_cycle_requires_explicit_bootstrap():
    text=(INFRA/"cloud_cycle.py").read_text()
    assert "explicit bootstrap required" in text
    assert 'choices=["cycle", "bootstrap"]' in text


def test_cloud_commit_uses_compare_and_swap():
    sql=(INFRA/"neon_schema.sql").read_text().replace(" ","")
    assert "version=p_expected_version" in sql


def test_cloud_shell_does_not_modify_core_files():
    text=(INFRA/"cloud_cycle.py").read_text()
    assert "subprocess.run" in text
    assert "forge_agent.py" in text
    assert "write_text" not in text.split("def run_core_cycle",1)[1].split("def export_dashboard",1)[0]


def test_cloud_cycle_uses_neon_adapter_not_supabase_transport():
    text=(INFRA/"cloud_cycle.py").read_text()
    assert "NeonStateClient" in text
    assert "SupabaseStateClient" not in text
    state=(INFRA/"neon_state.py").read_text()
    assert "NEON_DATABASE_URL" in state
    assert "service_role" not in state



def test_dashboard_export_verifies_raw_signature_then_uses_canonical_neon_hash():
    text=(INFRA/"cloud_cycle.py").read_text()
    assert "hashlib.sha256(raw).hexdigest() != digest" in text
    assert "canonical_digest = payload_sha256(payload)" in text
    assert "return payload, canonical_digest" in text

def test_oversized_cloud_payload_fails_closed():
    try:
        validate_payload_size({"x":"a"*(MAX_RUNTIME_BYTES+1)})
        raise AssertionError("oversized cloud payload accepted")
    except CloudStateError:
        pass


def test_the_paper_cycle_workflow_is_retired():
    """Paper trading left the architecture, so its workflow is gone rather than merely renamed."""
    assert not (ROOT/".github/workflows/trips-cloud-paper.yml").exists()


def test_live_readiness_workflow_performs_only_non_mutating_validation():
    """The replacement carries the old health checks forward and adds no trading step.

    Retiring a workflow must not retire its verification coverage. The frozen-core hash check and
    the infrastructure manifest check are both still here - and no step may reach a brokerage
    account, because a live order submitted as a CI test is a real order.
    """
    text=(ROOT/".github/workflows/trips-live-readiness.yml").read_text()
    # The two health checks the retired workflow performed, preserved.
    assert "sha256sum -c infra/core_v06.sha256" in text
    assert "python infra/verify_infra.py" in text
    assert "requirements-cloud.txt" in text
    # Nothing that could touch a broker or its credentials.
    for forbidden in ("NEON_DATABASE_URL", "UPSTOX", "ALPACA", "TWELVE_DATA_API_KEY",
                      "ALPHA_VANTAGE_API_KEY", "cloud_cycle.py", "SUPABASE_SERVICE_ROLE_KEY"):
        assert forbidden not in text, f"{forbidden} must not appear in a non-trading workflow"
    # It re-runs the safety gate rather than a narrower subset of it.
    assert "scripts/verify_all.sh" in text


def test_neon_client_redacts_database_url_from_errors():
    class Boom:
        def __call__(self, _url):
            raise RuntimeError("secret should not be echoed")
    c=NeonStateClient(database_url="postgresql://u:TOPSECRET@db.example/trips?sslmode=require", connect=Boom())
    try:
        c.fetch_runtime()
        raise AssertionError("database failure not raised")
    except CloudStateError as exc:
        assert "TOPSECRET" not in str(exc)


def main():
    tests=[v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("PASS",t.__name__)
    print(f"ALL PASS ({len(tests)} cloud-shell tests)")

if __name__=="__main__": main()

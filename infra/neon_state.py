from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from cloud_state import CloudStateError, RemoteRuntime, payload_sha256, validate_payload_size


class NeonStateClient:
    """Least-privilege PostgreSQL state client for the Trip's cloud shell.

    The supplied login must have only USAGE on trips_cloud plus EXECUTE on the
    bounded SECURITY DEFINER functions. It must have no direct table privileges.
    """

    def __init__(self, database_url: Optional[str] = None, connect: Optional[Callable[..., Any]] = None):
        self.database_url = (database_url or os.getenv("NEON_DATABASE_URL", "")).strip()
        if not self.database_url:
            raise CloudStateError("Neon database credential is missing; cloud state must fail closed")
        if not (self.database_url.startswith("postgresql://") or self.database_url.startswith("postgres://")):
            raise CloudStateError("NEON_DATABASE_URL must be a PostgreSQL URL")
        # Neon production URLs are TLS URLs. Refuse explicit sslmode=disable.
        if "sslmode=disable" in self.database_url.lower():
            raise CloudStateError("NEON_DATABASE_URL may not disable TLS")
        self._connect = connect or self._psycopg_connect

    @staticmethod
    def _psycopg_connect(database_url: str):
        try:
            import psycopg  # type: ignore
        except Exception as exc:
            raise CloudStateError("psycopg is required for the Neon cloud shell") from exc
        try:
            return psycopg.connect(database_url, connect_timeout=20, application_name="trips-cloud-shell-v0.7")
        except Exception as exc:
            raise CloudStateError(f"Neon connection failed: {type(exc).__name__}") from exc

    def _call(self, sql: str, params: tuple = ()) -> Any:
        try:
            with self._connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    row = cur.fetchone()
                conn.commit()
        except CloudStateError:
            raise
        except Exception as exc:
            # Never echo DSNs, SQL parameters, or server credential material.
            raise CloudStateError(f"Neon database operation failed: {type(exc).__name__}") from exc
        if not row:
            return None
        value = row[0]
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    def fetch_runtime(self) -> Optional[RemoteRuntime]:
        result = self._call("select trips_cloud.get_runtime()")
        if result is None:
            return None
        if not isinstance(result, dict):
            raise CloudStateError("authoritative runtime query returned malformed data")
        payload = result.get("payload")
        digest = str(result.get("payload_sha256") or "")
        version = result.get("version")
        if not isinstance(payload, dict) or not isinstance(version, int) or version < 1:
            raise CloudStateError("remote runtime row is malformed")
        if payload_sha256(payload) != digest:
            raise CloudStateError("remote runtime payload checksum mismatch")
        return RemoteRuntime(version=version, payload=payload, payload_sha256=digest,
                             updated_at=result.get("updated_at"))

    def bootstrap_runtime(self, payload: Dict[str, Any]) -> RemoteRuntime:
        validate_payload_size(payload)
        digest = payload_sha256(payload)
        result = self._call("select trips_cloud.bootstrap_runtime(%s::jsonb,%s)",
                            (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False), digest))
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else "invalid_response"
            raise CloudStateError(f"remote runtime bootstrap rejected: {reason}")
        return RemoteRuntime(version=int(result["version"]), payload=payload, payload_sha256=digest,
                             updated_at=result.get("updated_at"))

    def commit_runtime(self, expected_version: int, payload: Dict[str, Any]) -> RemoteRuntime:
        if not isinstance(expected_version, int) or expected_version < 1:
            raise CloudStateError("expected_version must be a positive integer")
        validate_payload_size(payload)
        digest = payload_sha256(payload)
        result = self._call("select trips_cloud.commit_runtime(%s,%s::jsonb,%s)",
                            (expected_version,
                             json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False), digest))
        if not isinstance(result, dict) or not result.get("ok"):
            reason = result.get("reason") if isinstance(result, dict) else "invalid_response"
            raise CloudStateError(f"compare-and-swap runtime commit rejected: {reason}")
        return RemoteRuntime(version=int(result["version"]), payload=payload, payload_sha256=digest,
                             updated_at=result.get("updated_at"))

    def acquire_lease(self, owner: str, ttl_seconds: int = 900) -> dict:
        if not owner or len(owner) > 200:
            raise CloudStateError("lease owner is invalid")
        ttl_seconds = int(ttl_seconds)
        if ttl_seconds < 60 or ttl_seconds > 3600:
            raise CloudStateError("lease TTL outside safety bounds")
        result = self._call("select trips_cloud.acquire_lease(%s,%s)", (owner, ttl_seconds))
        if not isinstance(result, dict) or not result.get("ok"):
            raise CloudStateError("cloud cycle lease is already held or unavailable")
        return result

    def release_lease(self, owner: str) -> None:
        result = self._call("select trips_cloud.release_lease(%s)", (owner,))
        if not isinstance(result, dict) or not result.get("ok"):
            raise CloudStateError("cloud cycle lease release failed")

    def write_heartbeat(self, source: str, status: str, detail: dict) -> None:
        if status not in {"PASS", "DEGRADED", "HALT"}:
            raise CloudStateError("heartbeat status invalid")
        result = self._call("select trips_cloud.write_heartbeat(%s,%s,%s::jsonb)",
                            (source[:100], status,
                             json.dumps(detail, sort_keys=True, separators=(",", ":"), allow_nan=False)))
        if not isinstance(result, dict) or not result.get("ok"):
            raise CloudStateError("heartbeat write rejected")

    def publish_dashboard_snapshot(self, payload: Dict[str, Any], snapshot_sha256: str) -> None:
        validate_payload_size(payload)
        if payload_sha256(payload) != snapshot_sha256:
            raise CloudStateError("dashboard snapshot checksum mismatch before publish")
        result = self._call("select trips_cloud.publish_dashboard_snapshot(%s::jsonb,%s)",
                            (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
                             snapshot_sha256))
        if not isinstance(result, dict) or not result.get("ok"):
            raise CloudStateError("dashboard snapshot publish rejected")

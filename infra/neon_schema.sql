-- Trip's v0.7 Neon durable-state shell.
-- Infrastructure only: the frozen v0.6 trading core is not modified.
-- Execution role: trips_runtime_exec (NOLOGIN). A separate restricted LOGIN is granted this role after migration.

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname='trips_runtime_exec') then
    create role trips_runtime_exec nologin nosuperuser nocreatedb nocreaterole noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname='trips_dashboard_exec') then
    create role trips_dashboard_exec nologin nosuperuser nocreatedb nocreaterole noinherit;
  end if;
end
$$;

create schema if not exists trips_cloud;

revoke all on schema trips_cloud from public;
grant usage on schema trips_cloud to trips_runtime_exec, trips_dashboard_exec;

create table if not exists trips_cloud.runtime (
  id text primary key check (id = 'authoritative'),
  version bigint not null check (version >= 1),
  payload jsonb not null,
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz not null default now()
);

create table if not exists trips_cloud.cycle_lease (
  id text primary key check (id = 'cloud_cycle'),
  owner text not null check (length(owner) between 1 and 200),
  expires_at timestamptz not null,
  updated_at timestamptz not null default now()
);

create table if not exists trips_cloud.heartbeats (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  source text not null check (length(source) between 1 and 100),
  status text not null check (status in ('PASS','DEGRADED','HALT')),
  detail jsonb not null default '{}'::jsonb
);

create table if not exists trips_cloud.dashboard_snapshot (
  id text primary key check (id = 'current'),
  payload jsonb not null,
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  updated_at timestamptz not null default now()
);

-- No direct data access for the runtime login. All mutations/read paths go through bounded functions.
revoke all on all tables in schema trips_cloud from public, trips_runtime_exec, trips_dashboard_exec;
revoke all on all sequences in schema trips_cloud from public, trips_runtime_exec, trips_dashboard_exec;

create or replace function trips_cloud.get_runtime()
returns jsonb
language sql
security definer
set search_path = pg_catalog, trips_cloud
as $$
  select jsonb_build_object(
      'version', version,
      'payload', payload,
      'payload_sha256', payload_sha256,
      'updated_at', updated_at
    )
  from trips_cloud.runtime
  where id='authoritative'
  limit 1;
$$;

create or replace function trips_cloud.bootstrap_runtime(p_payload jsonb, p_sha256 text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_now timestamptz := clock_timestamp();
begin
  if p_sha256 !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_sha256');
  end if;
  if octet_length(convert_to(p_payload::text, 'UTF8')) > 15000000 then
    return jsonb_build_object('ok', false, 'reason', 'payload_too_large');
  end if;
  if exists(select 1 from trips_cloud.runtime where id='authoritative') then
    return jsonb_build_object('ok', false, 'reason', 'already_initialized');
  end if;
  insert into trips_cloud.runtime(id,version,payload,payload_sha256,updated_at)
  values('authoritative',1,p_payload,p_sha256,v_now);
  return jsonb_build_object('ok', true, 'version', 1, 'updated_at', v_now);
end;
$$;

create or replace function trips_cloud.commit_runtime(p_expected_version bigint, p_payload jsonb, p_sha256 text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_next bigint; v_now timestamptz := clock_timestamp();
begin
  if p_expected_version < 1 then
    return jsonb_build_object('ok', false, 'reason', 'invalid_expected_version');
  end if;
  if p_sha256 !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_sha256');
  end if;
  if octet_length(convert_to(p_payload::text, 'UTF8')) > 15000000 then
    return jsonb_build_object('ok', false, 'reason', 'payload_too_large');
  end if;
  update trips_cloud.runtime
     set version = version + 1,
         payload = p_payload,
         payload_sha256 = p_sha256,
         updated_at = v_now
   where id='authoritative' and version=p_expected_version
   returning version into v_next;
  if v_next is null then
    return jsonb_build_object('ok', false, 'reason', 'version_conflict_or_missing');
  end if;
  return jsonb_build_object('ok', true, 'version', v_next, 'updated_at', v_now);
end;
$$;

create or replace function trips_cloud.acquire_lease(p_owner text, p_ttl_seconds integer)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_now timestamptz := clock_timestamp(); v_exp timestamptz; v_owner text;
begin
  if p_owner is null or length(p_owner)=0 or length(p_owner)>200
     or p_ttl_seconds < 60 or p_ttl_seconds > 3600 then
    return jsonb_build_object('ok', false, 'reason', 'invalid_lease_request');
  end if;
  v_exp := v_now + make_interval(secs => p_ttl_seconds);
  insert into trips_cloud.cycle_lease(id,owner,expires_at,updated_at)
  values('cloud_cycle',p_owner,v_exp,v_now)
  on conflict(id) do update
     set owner=excluded.owner, expires_at=excluded.expires_at, updated_at=excluded.updated_at
   where trips_cloud.cycle_lease.expires_at <= v_now
      or trips_cloud.cycle_lease.owner = p_owner;
  select owner into v_owner from trips_cloud.cycle_lease where id='cloud_cycle';
  if v_owner is distinct from p_owner then
    return jsonb_build_object('ok', false, 'reason', 'lease_held');
  end if;
  return jsonb_build_object('ok', true, 'owner', p_owner, 'expires_at', v_exp);
end;
$$;

create or replace function trips_cloud.release_lease(p_owner text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_deleted integer;
begin
  delete from trips_cloud.cycle_lease where id='cloud_cycle' and owner=p_owner;
  get diagnostics v_deleted = row_count;
  if v_deleted <> 1 then
    return jsonb_build_object('ok', false, 'reason', 'lease_not_owned');
  end if;
  return jsonb_build_object('ok', true);
end;
$$;

create or replace function trips_cloud.write_heartbeat(p_source text, p_status text, p_detail jsonb)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_id bigint; v_now timestamptz := clock_timestamp();
begin
  if p_source is null or length(p_source)=0 or length(p_source)>100 then
    return jsonb_build_object('ok', false, 'reason', 'invalid_source');
  end if;
  if p_status not in ('PASS','DEGRADED','HALT') then
    return jsonb_build_object('ok', false, 'reason', 'invalid_status');
  end if;
  if octet_length(convert_to(coalesce(p_detail,'{}'::jsonb)::text, 'UTF8')) > 65536 then
    return jsonb_build_object('ok', false, 'reason', 'heartbeat_detail_too_large');
  end if;
  insert into trips_cloud.heartbeats(source,status,detail,created_at)
  values(p_source,p_status,coalesce(p_detail,'{}'::jsonb),v_now)
  returning id into v_id;
  delete from trips_cloud.heartbeats where id <= v_id - 10000;
  return jsonb_build_object('ok', true, 'id', v_id, 'created_at', v_now);
end;
$$;

create or replace function trips_cloud.publish_dashboard_snapshot(p_payload jsonb, p_sha256 text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, trips_cloud
as $$
declare v_now timestamptz := clock_timestamp();
begin
  if p_sha256 !~ '^[0-9a-f]{64}$' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_sha256');
  end if;
  if octet_length(convert_to(p_payload::text, 'UTF8')) > 15000000 then
    return jsonb_build_object('ok', false, 'reason', 'payload_too_large');
  end if;
  insert into trips_cloud.dashboard_snapshot(id,payload,payload_sha256,updated_at)
  values('current',p_payload,p_sha256,v_now)
  on conflict(id) do update
     set payload=excluded.payload,
         payload_sha256=excluded.payload_sha256,
         updated_at=excluded.updated_at;
  return jsonb_build_object('ok', true, 'updated_at', v_now);
end;
$$;

create or replace function trips_cloud.get_dashboard_snapshot()
returns jsonb
language sql
security definer
set search_path = pg_catalog, trips_cloud
as $$
  select jsonb_build_object(
      'payload', payload,
      'payload_sha256', payload_sha256,
      'updated_at', updated_at
    )
  from trips_cloud.dashboard_snapshot
  where id='current'
  limit 1;
$$;

create or replace function trips_cloud.get_latest_heartbeat()
returns jsonb
language sql
security definer
set search_path = pg_catalog, trips_cloud
as $$
  select to_jsonb(h)
  from (
    select id,created_at,source,status,detail
    from trips_cloud.heartbeats
    order by id desc
    limit 1
  ) h;
$$;

-- EXECUTE is granted only to the dedicated runtime login.
revoke all on function trips_cloud.get_runtime() from public;
revoke all on function trips_cloud.bootstrap_runtime(jsonb,text) from public;
revoke all on function trips_cloud.commit_runtime(bigint,jsonb,text) from public;
revoke all on function trips_cloud.acquire_lease(text,integer) from public;
revoke all on function trips_cloud.release_lease(text) from public;
revoke all on function trips_cloud.write_heartbeat(text,text,jsonb) from public;
revoke all on function trips_cloud.publish_dashboard_snapshot(jsonb,text) from public;
revoke all on function trips_cloud.get_dashboard_snapshot() from public;
revoke all on function trips_cloud.get_latest_heartbeat() from public;

grant execute on function trips_cloud.get_runtime() to trips_runtime_exec;
grant execute on function trips_cloud.bootstrap_runtime(jsonb,text) to trips_runtime_exec;
grant execute on function trips_cloud.commit_runtime(bigint,jsonb,text) to trips_runtime_exec;
grant execute on function trips_cloud.acquire_lease(text,integer) to trips_runtime_exec;
grant execute on function trips_cloud.release_lease(text) to trips_runtime_exec;
grant execute on function trips_cloud.write_heartbeat(text,text,jsonb) to trips_runtime_exec;
grant execute on function trips_cloud.publish_dashboard_snapshot(jsonb,text) to trips_runtime_exec;
grant execute on function trips_cloud.get_dashboard_snapshot() to trips_runtime_exec;
grant execute on function trips_cloud.get_latest_heartbeat() to trips_runtime_exec;

-- Read-only dashboard capability: snapshot + heartbeat only.
grant execute on function trips_cloud.get_dashboard_snapshot() to trips_dashboard_exec;
grant execute on function trips_cloud.get_latest_heartbeat() to trips_dashboard_exec;

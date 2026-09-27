-- Trip's v0.8 additive Student observability migration.
-- Authoritative Student state remains inside trips_cloud.runtime.payload.student,
-- committed atomically by the existing compare-and-swap runtime function.
create or replace function trips_cloud.get_student_summary()
returns jsonb
language sql
security definer
set search_path = pg_catalog, trips_cloud
as $$
  select case
    when payload ? 'student' then jsonb_build_object(
      'student_schema_version', payload->'student'->'student_schema_version',
      'authority', payload->'student'->'authority',
      'episodes', jsonb_array_length(coalesce(payload->'student'->'episodes','[]'::jsonb)),
      'lessons', jsonb_array_length(coalesce(payload->'student'->'lessons','[]'::jsonb)),
      'mistakes', jsonb_array_length(coalesce(payload->'student'->'mistakes','[]'::jsonb)),
      'experiments', jsonb_array_length(coalesce(payload->'student'->'experiments','[]'::jsonb)),
      'curriculum', jsonb_array_length(coalesce(payload->'student'->'curriculum','[]'::jsonb)),
      'last_event_hash', payload->'student'->'last_event_hash',
      'runtime_version', version,
      'updated_at', updated_at
    )
    else jsonb_build_object('status','STUDENT_NOT_INITIALIZED','runtime_version',version,'updated_at',updated_at)
  end
  from trips_cloud.runtime where id='authoritative' limit 1;
$$;
revoke all on function trips_cloud.get_student_summary() from public;
grant execute on function trips_cloud.get_student_summary() to trips_runtime_exec, trips_dashboard_exec;

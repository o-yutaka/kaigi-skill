-- kaigi v8 relay progress hardening.
-- Apply after relay/supabase/schema.sql on existing v8 deployments.

alter table public.kaigi_relay_requests
  add column if not exists stage text,
  add column if not exists progress jsonb not null default '{}'::jsonb,
  add column if not exists heartbeat_at timestamptz;

create or replace function public.kaigi_relay_heartbeat_v2(
  p_id uuid,
  p_claim_token uuid,
  p_worker_id text,
  p_lease_seconds integer default 120,
  p_run_id text default null,
  p_stage text default null,
  p_progress jsonb default '{}'::jsonb
) returns boolean
language plpgsql security definer set search_path = public as $$
begin
  if jsonb_typeof(coalesce(p_progress, '{}'::jsonb)) <> 'object' then
    raise exception 'progress must be object';
  end if;
  update public.kaigi_relay_requests
  set lease_until = now() + make_interval(secs => greatest(30, least(p_lease_seconds, 3600))),
      heartbeat_at = now(),
      run_id = coalesce(nullif(trim(p_run_id), ''), run_id),
      stage = coalesce(nullif(trim(p_stage), ''), stage),
      progress = coalesce(p_progress, '{}'::jsonb)
  where id = p_id
    and claim_token = p_claim_token
    and worker_id = p_worker_id
    and status in ('claimed','running');
  return found;
end;
$$;

revoke all on function public.kaigi_relay_heartbeat_v2(uuid,uuid,text,integer,text,text,jsonb)
  from public, anon, authenticated;
grant execute on function public.kaigi_relay_heartbeat_v2(uuid,uuid,text,integer,text,text,jsonb)
  to service_role;

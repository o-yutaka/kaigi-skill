-- Canonical kaigi v8 relay schema.
-- No pairing codes, worker tokens, or project-specific secrets belong here.

create table if not exists public.kaigi_relay_requests (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  status text not null default 'pending' check (status in ('pending','claimed','running','succeeded','failed','cancelled')),
  topic text not null check (length(trim(topic)) between 1 and 20000),
  options jsonb not null default '{}'::jsonb,
  requested_by text not null default 'chatgpt',
  request_sha256 text,
  claim_token uuid,
  claimed_at timestamptz,
  lease_until timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  worker_id text,
  run_id text,
  result_text text,
  packet_sha256 text,
  transcript_sha256 text,
  error text,
  constraint kaigi_relay_packet_sha check (packet_sha256 is null or packet_sha256 ~ '^[0-9a-f]{64}$'),
  constraint kaigi_relay_transcript_sha check (transcript_sha256 is null or transcript_sha256 ~ '^[0-9a-f]{64}$')
);
create index if not exists kaigi_relay_requests_pending_idx on public.kaigi_relay_requests(status, created_at)
where status in ('pending','claimed','running');
alter table public.kaigi_relay_requests enable row level security;

create table if not exists public.kaigi_relay_workers (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 120),
  key_sha256 text not null unique check (key_sha256 ~ '^[0-9a-f]{64}$'),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz
);
alter table public.kaigi_relay_workers enable row level security;
create index if not exists kaigi_relay_workers_enabled_idx on public.kaigi_relay_workers(enabled) where enabled;

create table if not exists public.kaigi_relay_pairings (
  code_sha256 text primary key check (code_sha256 ~ '^[0-9a-f]{64}$'),
  label text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  used_at timestamptz,
  check (expires_at > created_at)
);
alter table public.kaigi_relay_pairings enable row level security;
create index if not exists kaigi_relay_pairings_live_idx on public.kaigi_relay_pairings(expires_at) where used_at is null;

create or replace function public.kaigi_relay_touch_updated_at()
returns trigger language plpgsql set search_path = public as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
drop trigger if exists kaigi_relay_requests_touch on public.kaigi_relay_requests;
create trigger kaigi_relay_requests_touch before update on public.kaigi_relay_requests
for each row execute function public.kaigi_relay_touch_updated_at();

create or replace function public.kaigi_relay_claim(p_worker_id text, p_lease_seconds integer default 120)
returns public.kaigi_relay_requests
language plpgsql security definer set search_path = public as $$
declare r public.kaigi_relay_requests;
begin
  if p_worker_id is null or length(trim(p_worker_id)) = 0 then raise exception 'worker_id required'; end if;
  if p_lease_seconds < 30 or p_lease_seconds > 3600 then raise exception 'lease_seconds out of range'; end if;
  select * into r from public.kaigi_relay_requests
  where status = 'pending' or (status in ('claimed','running') and lease_until < now())
  order by created_at for update skip locked limit 1;
  if not found then return null; end if;
  update public.kaigi_relay_requests
  set status='claimed', claim_token=gen_random_uuid(), claimed_at=now(),
      lease_until=now()+make_interval(secs=>p_lease_seconds), worker_id=p_worker_id, error=null
  where id=r.id returning * into r;
  return r;
end;
$$;

create or replace function public.kaigi_relay_start(p_id uuid, p_claim_token uuid, p_worker_id text, p_lease_seconds integer default 120)
returns public.kaigi_relay_requests
language plpgsql security definer set search_path = public as $$
declare r public.kaigi_relay_requests;
begin
  update public.kaigi_relay_requests set status='running', started_at=coalesce(started_at,now()),
    lease_until=now()+make_interval(secs=>greatest(30,least(p_lease_seconds,3600)))
  where id=p_id and claim_token=p_claim_token and worker_id=p_worker_id and status in ('claimed','running')
  returning * into r;
  if not found then raise exception 'claim mismatch'; end if;
  return r;
end;
$$;

create or replace function public.kaigi_relay_heartbeat(p_id uuid, p_claim_token uuid, p_worker_id text, p_lease_seconds integer default 120)
returns boolean language plpgsql security definer set search_path = public as $$
begin
  update public.kaigi_relay_requests set lease_until=now()+make_interval(secs=>greatest(30,least(p_lease_seconds,3600)))
  where id=p_id and claim_token=p_claim_token and worker_id=p_worker_id and status in ('claimed','running');
  return found;
end;
$$;

create or replace function public.kaigi_relay_complete(
  p_id uuid, p_claim_token uuid, p_worker_id text, p_run_id text, p_result_text text,
  p_packet_sha256 text, p_transcript_sha256 text
) returns public.kaigi_relay_requests
language plpgsql security definer set search_path = public as $$
declare r public.kaigi_relay_requests;
begin
  if p_packet_sha256 !~ '^[0-9a-f]{64}$' or p_transcript_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid proof hash';
  end if;
  update public.kaigi_relay_requests set status='succeeded', completed_at=now(), lease_until=null,
    run_id=p_run_id, result_text=p_result_text, packet_sha256=p_packet_sha256,
    transcript_sha256=p_transcript_sha256, error=null
  where id=p_id and claim_token=p_claim_token and worker_id=p_worker_id and status in ('claimed','running')
  returning * into r;
  if not found then raise exception 'claim mismatch'; end if;
  return r;
end;
$$;

create or replace function public.kaigi_relay_fail(p_id uuid, p_claim_token uuid, p_worker_id text, p_error text)
returns public.kaigi_relay_requests
language plpgsql security definer set search_path = public as $$
declare r public.kaigi_relay_requests;
begin
  update public.kaigi_relay_requests set status='failed', completed_at=now(), lease_until=null,
    error=left(coalesce(p_error,'unknown error'),8000)
  where id=p_id and claim_token=p_claim_token and worker_id=p_worker_id and status in ('claimed','running')
  returning * into r;
  if not found then raise exception 'claim mismatch'; end if;
  return r;
end;
$$;

create or replace function public.kaigi_relay_enqueue(
  p_topic text, p_options jsonb default '{}'::jsonb, p_requested_by text default 'chatgpt'
) returns public.kaigi_relay_requests
language plpgsql security definer set search_path = public as $$
declare r public.kaigi_relay_requests; bad_key text;
begin
  if p_topic is null or length(trim(p_topic)) < 1 or length(trim(p_topic)) > 20000 then
    raise exception 'topic length out of range';
  end if;
  if jsonb_typeof(coalesce(p_options,'{}'::jsonb)) <> 'object' then raise exception 'options must be object'; end if;
  select key into bad_key from jsonb_object_keys(coalesce(p_options,'{}'::jsonb)) as key
  where key not in ('need','prefer','free_only','allow_cloud','max_agents','min_agents','round_timeout','quorum','best_effort_capabilities','no_launch') limit 1;
  if bad_key is not null then raise exception 'option not allowed: %', bad_key; end if;
  insert into public.kaigi_relay_requests(topic,options,requested_by)
  values(trim(p_topic),coalesce(p_options,'{}'::jsonb),coalesce(nullif(trim(p_requested_by),''),'chatgpt'))
  returning * into r;
  return r;
end;
$$;

revoke all on table public.kaigi_relay_requests from public, anon, authenticated;
revoke all on table public.kaigi_relay_workers from public, anon, authenticated;
revoke all on table public.kaigi_relay_pairings from public, anon, authenticated;
grant select, insert, update, delete on table public.kaigi_relay_requests to service_role;
grant select, insert, update, delete on table public.kaigi_relay_workers to service_role;
grant select, insert, update, delete on table public.kaigi_relay_pairings to service_role;

revoke all on function public.kaigi_relay_touch_updated_at() from public, anon, authenticated;
revoke all on function public.kaigi_relay_claim(text,integer) from public, anon, authenticated;
revoke all on function public.kaigi_relay_start(uuid,uuid,text,integer) from public, anon, authenticated;
revoke all on function public.kaigi_relay_heartbeat(uuid,uuid,text,integer) from public, anon, authenticated;
revoke all on function public.kaigi_relay_complete(uuid,uuid,text,text,text,text,text) from public, anon, authenticated;
revoke all on function public.kaigi_relay_fail(uuid,uuid,text,text) from public, anon, authenticated;
revoke all on function public.kaigi_relay_enqueue(text,jsonb,text) from public, anon, authenticated;
grant execute on function public.kaigi_relay_touch_updated_at() to service_role;
grant execute on function public.kaigi_relay_claim(text,integer) to service_role;
grant execute on function public.kaigi_relay_start(uuid,uuid,text,integer) to service_role;
grant execute on function public.kaigi_relay_heartbeat(uuid,uuid,text,integer) to service_role;
grant execute on function public.kaigi_relay_complete(uuid,uuid,text,text,text,text,text) to service_role;
grant execute on function public.kaigi_relay_fail(uuid,uuid,text,text) to service_role;
grant execute on function public.kaigi_relay_enqueue(text,jsonb,text) to service_role;

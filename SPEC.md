# kaigi v8 specification

## Goal

`capability-aware cast → provider-neutral Council → durable recovery → Decision proof` を維持しつつ、ChatGPT/cloud control sideからlocal kaigiへ安全にadvisory meeting requestを渡せるoutbound relayを提供する。

## Invariants

1. required providers = `[]`。provider名をauto priorityへ使わない。
2. cloud API agentは暗黙利用しない。
3. meeting output / Decision packet / relay result はAuthority/Executionではない。
4. relayはlocal agentchattrへのinbound portを要求しない。
5. relayは任意shell command / arbitrary kaigi subcommandを受理しない。
6. full Council transcriptはrelay resultまたはprogress heartbeatとしてcloudへ送らない。
7. terminal stateを未観測のままsuccess扱いしない。
8. relay `running` はstage telemetryを持ち、同一stageの無期限待機を許可しない。
9. agentchattr authの自動取得はloopback HTTPだけに限定し、取得tokenをdiskへ永続化しない。
10. kaigi controlはregistered-agent identityをmint/impersonateしない。human/session transportとagent Bearer transportを分離する。

## Public UX

```text
kaigi "TOPIC"                 capability-aware Council
kaigi cast TOPIC              side-effect zero cast preview
kaigi caps ...                capability registry
kaigi recover [RUN_ID]        durable Council recovery
kaigi verify [RUN_ID] --live  proof verification
kaigi relay pair ...          worker provisioning
kaigi relay start|stop        background outbound worker
kaigi relay status            local + remote + active stage status
kaigi relay once              process one request
kaigi relay config            local remote-execution policy
```

## Capability / Council / proof

v7 contracts remain canonical. Topic-inferred capabilities are soft; `--need` is hard. Selection is capability coverage → cost → online → speed → observed response reliability. Provider identity is routing only。

Council is ROUND1 independent fan-out → ROUND2 dissent/review → FINAL synthesis. FINAL reply observation is required for complete.

Decision packet schema remains `kaigi.decision_packet.v1`; capability plan and evidence transcript are bound by canonical SHA-256. Handoff remains advisory with `execution_authorized=false`.

## Local agentchattr auth and transport identity

Current agentchattr creates a fresh random browser session token on each server start and injects it into the loopback index page. Separately, each registered agent receives a per-agent Bearer token from `/api/register`. These credentials represent different identities and are not interchangeable.

A detached kaigi relay daemon cannot assume that the interactive shell's auth environment is available. kaigi resolves local auth in this order:

1. explicit `KAIGI_BEARER_TOKEN` / `AGENTCHATTR_AGENT_TOKEN`;
2. explicit `KAIGI_TOKEN` / `AGENTCHATTR_TOKEN`;
3. live session token discovered from the loopback agentchattr index page;
4. legacy server-log token fallback.

Live discovery accepts only loopback HTTP hosts (`127.0.0.1`, `localhost`, `::1`) and only the exact upstream `token_hex(32)` shape. It never scrapes a remote/non-loopback endpoint for credentials. The discovered token remains in process memory only and is fetched live, so an agentchattr restart/token rotation does not require persisting a new token into relay config.

Transport contract:

- human/control session token -> AgentChattr WebSocket `/ws?token=...` message path;
- explicit registered-agent Bearer -> REST `/api/send`;
- kaigi control never calls `/api/register` and never allocates an agent slot;
- session WebSocket sending is loopback-only;
- older AgentChattr session-REST behavior may be attempted for compatibility, but switching to WebSocket occurs only after the explicit current Bearer-only `/api/send` error is observed;
- unrelated 401/403 errors remain terminal/fail-closed.

This preserves `Control != Agent Identity`: the Council orchestrator can post the same human/control message that the browser would post without impersonating Codex, Claude, a local model, or any other participant.

## Relay topology

```text
cloud control side
  -> service-role enqueue
  -> kaigi_relay_requests
  <- local worker HTTPS polling/claim
  -> local kaigi Council
  -> local packet verify
  -> result_text + run_id + packet_sha256 + transcript_sha256
```

The PC initiates every network connection. No local HTTP listener is introduced by relay.

## Relay database

Canonical base schema: `relay/supabase/schema.sql`.
Follow-up idempotent migrations: `relay/supabase/migrations/` in lexical order.

Tables use RLS with no public policies; direct anon/authenticated access is denied. Edge Function uses service role internally. RPC execution is revoked from `public`, `anon`, and `authenticated`, granted only to `service_role`.

`kaigi_relay_claim` atomically chooses oldest pending or expired leased work with `FOR UPDATE SKIP LOCKED`, assigns `claim_token`, `worker_id`, and `lease_until`.

States: `pending -> claimed -> running -> succeeded|failed`; expired claimed/running work can be reclaimed.

Progress hardening adds `stage`, `progress`, `heartbeat_at` and `kaigi_relay_heartbeat_v2`. The progress object is metadata-only and must never contain Council transcript text or credentials.

## Pairing/auth

No shared root relay key exists.

Provisioning uses a high-entropy single-use pairing code. Server stores only `code_sha256`; on successful pair the Edge Function creates a random 32-byte worker token, stores only `key_sha256`, and returns plaintext token once.

Worker stores the token in `~/.config/kaigi/relay.json` (or `KAIGI_RELAY_CONFIG`) mode 0600. Every normal Edge request requires `x-kaigi-worker-token`; server derives worker ID from the matched token and ignores client-supplied worker identity.

The relay worker token, local agentchattr browser session token, and any registered-agent Bearer token are separate credentials with separate failure domains. The worker token may persist locally; an auto-discovered browser session token must not.

## Remote option allowlist

Only these request options are accepted by enqueue and local worker:

- `need`
- `prefer`
- `free_only`
- `allow_cloud`
- `max_agents`
- `min_agents`
- `round_timeout`
- `quorum`
- `best_effort_capabilities`
- `no_launch`

No `agents`, `synth`, command, cwd, file path, env injection, shell, or generic argv surface is allowed.

`allow_cloud=true` requires a second local gate: `allow_remote_cloud=true`, set only through local `kaigi relay config --allow-remote-cloud`. Default is deny.

Remote numeric bounds: agents/quorum max 8; `round_timeout` 5..900s. Worker execution timeout is locally bounded.

## Idempotency/recovery

Each remote request is bound to local run via `relay_request_id` and `relay_request_sha256`.

If a bound run is incomplete on reclaim, worker invokes `recover RUN_ID` rather than creating a new Council.

After verified local completion, worker writes `kaigi.relay_receipt.v1` mode 0600 before remote `complete`. If completion transport fails and the request is reclaimed, matching receipt is replayed without starting another Council.

Lease heartbeat runs during long local execution.

## Progress / hang contract

Relay revision `8.1-progress-watchdog` reports only privacy-preserving progress metadata through authenticated heartbeat:

- request ID / local run ID
- run state and stage
- ROUND1 / ROUND2 observed reply counts
- participant count
- synthesizer identity

It does not transmit Council message text, packet body, local paths, API keys, or worker token.

Worker has three independent execution bounds:

1. preparation watchdog — a local run must become identifiable within a bounded preparation window;
2. stage watchdog — a Council stage must advance within `round_timeout` plus bounded processing margin;
3. overall watchdog — local configured max execution remains a hard upper bound, further capped by the derived Council budget.

A watchdog breach terminates the local Council child and reports failure; it is never converted to success. `kaigi relay stop` terminates the relay process tree so an active Council child cannot remain orphaned after daemon stop. Claimed-request processing exceptions are reported as terminal `failed` so lease expiry cannot turn a deterministic failure into an unbounded reclaim loop.

## Data boundary

Relay queue necessarily stores topic/options. During execution it may additionally store metadata-only progress. On success it stores only final result text, run ID, packet SHA-256, and transcript SHA-256. The full local evidence transcript and Decision packet stay on the PC unless separately exported by the user.

## Supabase Edge Function

Canonical source: `relay/supabase/functions/kaigi-relay/index.ts`.

Current relay protocol is `2`, adding stage/progress heartbeat while remaining compatible with v8 workers that send heartbeat without progress fields.

`verify_jwt=false` is intentional because the endpoint uses custom single-use pairing and per-device worker-token auth. No unauthenticated action exists except `pair`, which requires an unused, unexpired high-entropy code.

## Verification gate

CI minimum:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py kaigi_policy.py kaigi_capabilities.py kaigi_auth.py kaigi_transport.py kaigi_relay.py kaigi_relay_v81.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
```

Regression/invariants must prove:

- loopback index session discovery succeeds for current upstream token shape
- non-loopback auth discovery performs no network credential fetch
- explicit auth environment overrides auto-discovery
- live index auth outranks stale legacy-log fallback
- discovered/session credentials are never synthesized into registered-agent Bearer authorization
- current AgentChattr session-auth `/api/send` rejection falls back to human/control WebSocket and produces a durable message
- explicit registered-agent Bearer remains on REST `/api/send`
- invalid explicit session auth does not silently fall back or get replaced
- control transport never calls `/api/register`
- pair stores local worker token at mode 0600
- public `kaigi relay once` performs claim -> real Council -> proof packet -> verified hashes -> complete
- receipt replay sends completion without a second ROUND1
- remote cloud request is denied by default local policy
- progress migration and protocol-2 heartbeat source are present and contain no active secret value
- relay shim routes through `8.1-progress-watchdog`
- preparation/stage watchdogs and process-tree termination are present
- claimed processing exceptions become terminal failure instead of reclaim loops
- legacy provider-neutral/capability/proof/recovery regressions remain green
- canonical install does not create provider-specific directories

## Deployment source of truth

Repository contains reproducible cloud schema/function/migration source but never active pairing codes, worker tokens, Supabase service-role keys, provider API keys, agentchattr session tokens, or registered-agent bearer tokens.

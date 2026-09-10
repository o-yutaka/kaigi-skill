# kaigi v8 specification

## Goal

`capability-aware cast → provider-neutral Council → durable recovery → Decision proof` を維持しつつ、ChatGPT/cloud control sideからlocal kaigiへ安全にadvisory meeting requestを渡せるoutbound relayを提供する。

## Invariants

1. required providers = `[]`。provider名をauto priorityへ使わない。
2. cloud API agentは暗黙利用しない。
3. meeting output / Decision packet / relay result はAuthority/Executionではない。
4. relayはlocal agentchattrへのinbound portを要求しない。
5. relayは任意shell command / arbitrary kaigi subcommandを受理しない。
6. full Council transcriptはrelay resultとしてcloudへ送らない。
7. terminal stateを未観測のままsuccess扱いしない。

## Public UX

```text
kaigi "TOPIC"                 capability-aware Council
kaigi cast TOPIC              side-effect zero cast preview
kaigi caps ...                capability registry
kaigi recover [RUN_ID]        durable Council recovery
kaigi verify [RUN_ID] --live  proof verification
kaigi relay pair ...          worker provisioning
kaigi relay start|stop        background outbound worker
kaigi relay status            local + remote status
kaigi relay once              process one request
kaigi relay config            local remote-execution policy
```

## Capability / Council / proof

v7 contracts remain canonical. Topic-inferred capabilities are soft; `--need` is hard. Selection is capability coverage → cost → online → speed → observed response reliability. Provider identity is routing only.

Council is ROUND1 independent fan-out → ROUND2 dissent/review → FINAL synthesis. FINAL reply observation is required for complete.

Decision packet schema remains `kaigi.decision_packet.v1`; capability plan and evidence transcript are bound by canonical SHA-256. Handoff remains advisory with `execution_authorized=false`.

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

Canonical schema: `relay/supabase/schema.sql`.

Tables use RLS with no public policies; direct anon/authenticated access is denied. Edge Function uses service role internally. RPC execution is revoked from `public`, `anon`, and `authenticated`, granted only to `service_role`.

`kaigi_relay_claim` atomically chooses oldest pending or expired leased work with `FOR UPDATE SKIP LOCKED`, assigns `claim_token`, `worker_id`, and `lease_until`.

States: `pending -> claimed -> running -> succeeded|failed`; expired claimed/running work can be reclaimed.

## Pairing/auth

No shared root relay key exists.

Provisioning uses a high-entropy single-use pairing code. Server stores only `code_sha256`; on successful pair the Edge Function creates a random 32-byte worker token, stores only `key_sha256`, and returns plaintext token once.

Worker stores the token in `~/.config/kaigi/relay.json` (or `KAIGI_RELAY_CONFIG`) mode 0600. Every normal Edge request requires `x-kaigi-worker-token`; server derives worker ID from the matched token and ignores client-supplied worker identity.

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

## Data boundary

Relay queue necessarily stores topic/options. On success it stores only final result text, run ID, packet SHA-256, and transcript SHA-256. The full local evidence transcript and Decision packet stay on the PC unless separately exported by the user.

## Supabase Edge Function

Canonical source: `relay/supabase/functions/kaigi-relay/index.ts`.

`verify_jwt=false` is intentional because the endpoint uses custom single-use pairing and per-device worker-token auth. No unauthenticated action exists except `pair`, which requires an unused, unexpired high-entropy code.

## Verification gate

CI minimum:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py kaigi_policy.py kaigi_capabilities.py kaigi_relay.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
```

Relay regression must prove:

- pair stores local worker token at mode 0600
- public `kaigi relay once` performs claim -> real Council -> proof packet -> verified hashes -> complete
- receipt replay sends completion without a second ROUND1
- remote cloud request is denied by default local policy
- legacy provider-neutral/capability/proof/recovery regressions remain green
- canonical install does not create provider-specific directories

## Deployment source of truth

Repository contains reproducible cloud schema/function source but never active pairing codes, worker tokens, Supabase service-role keys, or provider API keys.

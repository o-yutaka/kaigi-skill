import { createClient } from "jsr:@supabase/supabase-js@2";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function sha256Hex(value: string): Promise<string> {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function randomToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function cleanStage(value: unknown): string | null {
  const stage = String(value || "").trim().slice(0, 120);
  return stage || null;
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const url = Deno.env.get("SUPABASE_URL");
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !serviceKey) return json({ error: "server_config" }, 500);
  const supabase = createClient(url, serviceKey, { auth: { persistSession: false } });

  let body: Record<string, unknown>;
  try { body = await req.json(); } catch { return json({ error: "invalid_json" }, 400); }
  const action = String(body.action || "");

  if (action === "pair") {
    const code = String(body.code || "");
    const workerName = String(body.worker_name || "kaigi-worker").trim().slice(0, 120);
    if (!code || !workerName) return json({ error: "pair_fields_required" }, 400);
    const codeHash = await sha256Hex(code);
    const now = new Date().toISOString();
    const { data: pairing, error: pairError } = await supabase
      .from("kaigi_relay_pairings")
      .update({ used_at: now })
      .eq("code_sha256", codeHash)
      .is("used_at", null)
      .gt("expires_at", now)
      .select("code_sha256")
      .maybeSingle();
    if (pairError) return json({ error: "pair_backend", detail: pairError.message }, 500);
    if (!pairing) return json({ error: "pair_code_invalid_or_expired" }, 401);

    const token = randomToken();
    const tokenHash = await sha256Hex(token);
    const { data: worker, error: workerError } = await supabase
      .from("kaigi_relay_workers")
      .insert({ name: workerName, key_sha256: tokenHash, enabled: true, last_seen_at: now })
      .select("id,name")
      .single();
    if (workerError || !worker) return json({ error: "worker_create_failed", detail: workerError?.message || "unknown" }, 500);
    return json({ ok: true, worker_id: worker.id, worker_name: worker.name, token, protocol: 2 });
  }

  const workerToken = req.headers.get("x-kaigi-worker-token") || "";
  if (!workerToken || workerToken.length > 256) return json({ error: "unauthorized" }, 401);
  const tokenHash = await sha256Hex(workerToken);
  const { data: worker, error: authError } = await supabase
    .from("kaigi_relay_workers")
    .select("id,name,enabled")
    .eq("key_sha256", tokenHash)
    .eq("enabled", true)
    .maybeSingle();
  if (authError) return json({ error: "auth_backend" }, 500);
  if (!worker) return json({ error: "unauthorized" }, 401);
  const workerId = String(worker.id);
  await supabase.from("kaigi_relay_workers").update({ last_seen_at: new Date().toISOString() }).eq("id", worker.id);

  const lease = Math.max(30, Math.min(3600, Number(body.lease_seconds || 120)));
  if (action === "ping") return json({ ok: true, service: "kaigi-relay", protocol: 2, auth: "worker", worker_id: workerId });

  const call = async (fn: string, args: Record<string, unknown>) => {
    const { data, error } = await supabase.rpc(fn, args);
    if (error) return { error };
    return { data };
  };

  if (action === "claim") {
    const out = await call("kaigi_relay_claim", { p_worker_id: workerId, p_lease_seconds: lease });
    if (out.error) return json({ error: "claim_failed", detail: out.error.message }, 409);
    return json({ ok: true, request: out.data || null });
  }

  const id = String(body.id || "");
  const claimToken = String(body.claim_token || "");
  if (!id || !claimToken) return json({ error: "claim_fields_required" }, 400);

  if (action === "start") {
    const out = await call("kaigi_relay_start", { p_id: id, p_claim_token: claimToken, p_worker_id: workerId, p_lease_seconds: lease });
    if (out.error) return json({ error: "start_failed", detail: out.error.message }, 409);
    return json({ ok: true, request: out.data });
  }
  if (action === "heartbeat") {
    const rawProgress = body.progress;
    const progress = rawProgress && typeof rawProgress === "object" && !Array.isArray(rawProgress) ? rawProgress : {};
    const out = await call("kaigi_relay_heartbeat_v2", {
      p_id: id,
      p_claim_token: claimToken,
      p_worker_id: workerId,
      p_lease_seconds: lease,
      p_run_id: String(body.run_id || "") || null,
      p_stage: cleanStage(body.stage),
      p_progress: progress,
    });
    if (out.error || out.data !== true) return json({ error: "heartbeat_failed", detail: out.error?.message || "claim mismatch" }, 409);
    return json({ ok: true, protocol: 2 });
  }
  if (action === "complete") {
    const resultText = String(body.result_text || "");
    if (resultText.length > 200000) return json({ error: "result_too_large" }, 413);
    const out = await call("kaigi_relay_complete", {
      p_id: id,
      p_claim_token: claimToken,
      p_worker_id: workerId,
      p_run_id: String(body.run_id || ""),
      p_result_text: resultText,
      p_packet_sha256: String(body.packet_sha256 || ""),
      p_transcript_sha256: String(body.transcript_sha256 || ""),
    });
    if (out.error) return json({ error: "complete_failed", detail: out.error.message }, 409);
    return json({ ok: true, request: out.data });
  }
  if (action === "fail") {
    const out = await call("kaigi_relay_fail", {
      p_id: id,
      p_claim_token: claimToken,
      p_worker_id: workerId,
      p_error: String(body.error || "unknown error"),
    });
    if (out.error) return json({ error: "fail_failed", detail: out.error.message }, 409);
    return json({ ok: true, request: out.data });
  }

  return json({ error: "unknown_action" }, 400);
});

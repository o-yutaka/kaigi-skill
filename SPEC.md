# kaigi v7 specification

## Goal

agentchattrを `capability-aware safe selection → wake/launch → parallel independent analysis → dissent → synthesis → durable run → recovery → proof packet → advisory handoff` まで一貫して扱う。control planeはprovider-neutralとし、provider-specific integrationをoptional adapterへ隔離する。

## Public UX

- `kaigi "TOPIC"` — capability-aware safe-auto Council。complete後proof packetを自動生成。
- `kaigi cast TOPIC` — launch/send/run creationなしでcastのみ計算。
- `kaigi caps` — effective agent capability/profile一覧。
- `kaigi caps set NAME CAPS...` — local capability registry更新。
- `kaigi caps infer TOPIC` — topic heuristicによるsoft requirement確認。
- `kaigi @agent TEXT` — direct message shortcut。
- `kaigi decide TOPIC` — safe-autoの明示形。
- `kaigi reconcile/resume/recover` —未完了Council回復。
- `kaigi packet/verify/handoff/audit` — proof surface。

## Provider-neutral invariant

- required providers = `[]`
- agent/provider名をsafe-auto priorityやdefault synthesizer priorityへ使用しない
- identity名はrouting/disambiguationにのみ使用可能
- local API / CLI / cloud API / online-unclassifiedの分類はruntime/config factとして使用可能
- cloud APIは暗黙利用しない
- provider-specific skill/runtime integrationはoptional adapter

## Capability registry

Default path: `~/.config/kaigi/capabilities.json`
Override: `KAIGI_CONFIG_DIR`, `KAIGI_CAPABILITY_REGISTRY`
Schema: `kaigi.capability_registry.v1`

例:

```json
{
  "schema": "kaigi.capability_registry.v1",
  "agents": {
    "local-a": {
      "capabilities": ["coding", "research", "deep-reasoning"],
      "cost": "local",
      "speed": "fast",
      "context_tokens": 24576,
      "enabled": true
    }
  }
}
```

`capabilities`は明示metadata。agent名/model名からcoding/research等を推測しない。

Effective profileは:

1. runtime-derived generic facts (`general`, local APIなら`local-free`, CLIなら`cli`)
2. agent configの`capabilities/cost/speed/context_tokens`（存在する場合）
3. kaigi registry entry（最優先）

をmergeする。

Cost classes: `free`, `local`, `low`, `unknown`, `metered`。
Speed classes: `fast`, `normal`, `unknown`, `deep`。

## Topic inference

v7はtopic textに対する決定論的keyword rulesで、`coding`, `research`, `red-team`, `vision`, `long-context`, `deep-reasoning`, `fast` をsoft preferenceとして推定できる。

これはcapability証明ではなく要求推定である。agent capability自体をtopic/nameから推測してはならない。

`--no-cap-infer`で無効化可能。

## Hard / soft semantics

- `--need CAP[,CAP...]` = hard requirement。selected cast全体でcoverできなければnon-zero。
- `--prefer CAP[,CAP...]` = soft preference。
- topic inferred capabilities = soft preference。
- `--best-effort-capabilities` = hard requirement未充足でもユーザーが明示的に続行を許可。
- `--free-only` = effective costが`free`または`local`の候補のみ。

Explicit `--agents`でも`--need`が指定されていれば、そのcastのdeclared capability coverageを検証する。

## Capability-aware selection

まずprovider-neutral safe-auto policyでeligible candidate集合を作る。その後v7 selectorがgreedy coverageを行う。

Selection score order:

1. uncovered hard capability gain
2. uncovered inferred/preferred capability gain
3. cost rank
4. online vs offline
5. speed rank
6. observed ROUND1 response reliability
7. provider-neutral base order / deterministic name tie-break

Response reliabilityは過去Council run ledgerから `ROUND1 replies / invitations` を算出する。観測2件未満はneutral扱い。これはavailability/reliabilityであり、answer quality scoreではない。

選択後、最大7 agentまではCouncil role slotsへのcapability affinity合計が最大になる順序を探索する。7超はgreedy role fit。role preference:

- planner → deep-reasoning/research/long-context
- red-team → red-team
- implementer → coding
- evidence → research/long-context
- ux → vision
- long-horizon → long-context/deep-reasoning

既存coreのrole protocol自体は変更せず、cast orderで適合させる。

## Capability plan ledger

Capability-aware Councilのrun ledgerに `capability_plan` (`kaigi.capability_plan.v1`) をsnapshotする。

最低フィールド:

- selection_policy
- required
- preferred
- inferred
- free_only
- best_effort
- selected
- coverage
- missing_required
- effective profiles for selected agents
- capability registry SHA256

profile snapshotにはcapabilities/cost/speed/context_tokens/runtime classification/online flag/observed reliabilityを含められる。API secret/tokenは含めない。

## Safe-auto base contract

provider-neutral eligibility:

- local API = implicit candidate, auto-wake可
- non-API command agent = CLI candidate, offlineならupstream normal `wrapper.py` launch可
- cloud API = default excluded; `--allow-cloud`またはexplicit `--agents`時のみ
- online-unclassified = default excluded; `--allow-unknown`時のみ
- user/system/bot = excluded
- `--dry-run` = launch/wake/send/run creation zero

## Council contract

ROUND1=independent fan-out、ROUND2=dissent/review fan-out、FINAL=synthesis。FINAL replyを実観測した場合だけcomplete。

outbound markerは`RUN=<run_id>`。run ledgerはparticipants, roles, synth, outbound IDs, replies, finalを保持する。

## Synthesizer

Provider-neutral policyのbalanced synthesizerを維持する。

Priority:

1. explicit `--synth`
2. `KAIGI_SYNTH_AGENT`
3. balanced history policy（current participantのうち最近successful FINAL担当していないagent）

provider名による優先順位は禁止。

## Run persistence / recovery

`KAIGI_STATE_DIR` default `~/.local/state/kaigi`。

- `runs/<run_id>.json`
- `latest.json`
- atomic write
- `reconcile` outbound zero
- `resume` existing ROUND2/FINAL markerを再送しない
- `recover` reconcile→resume→completeならpacket生成

## Decision packet

Path: `packets/<run_id>.json`
Schema: `kaigi.decision_packet.v1`

既存v6 proof contractを維持する。Capability-aware runでは`capability_plan`もpacketへ含め、`packet_sha256`のcanonical hash対象とする。

`transcript_sha256`はevidence transcriptのみを束縛。`packet_sha256`は`packet_sha256`自身を除いたpacket全体を束縛するためcapability selection provenanceも含む。

## Verify

`kaigi verify RUN`:

1. schema
2. packet_sha256
3. transcript_sha256
4. run ledger final_text
5. ledger packet pointer

`--live`は同evidence message IDsをagentchattrから再取得してlive transcript hashまで照合する。

## Handoff

Schema `kaigi.handoff.v1`。Decision packet local verify後に生成する。capability planがpacketに存在すればhandoffにもcopyし、handoff SHA256に束縛する。

Authority invariant:

- `classification="advisory"`
- `execution_authorized=false`
- `requires_separate_authority=true`

## Agent launch / cloud governance

CLI agentはupstream normal `wrapper.py AGENT`。API agentはupstream `wrapper_api.py`。skip-permissions/bypass/yolo launcherを自動利用しない。

Cloud agentを暗黙wake/参加させない。secret値はregistry/packet/runへ保存しない。

## Install

Required repository runtime files:

- `kaigi`
- `kaigi_core.py`
- `kaigi_ops.py`
- `kaigi_v6.py`
- `kaigi_policy.py`
- `kaigi_capabilities.py`
- `SKILL.md`

Canonical skill targetのみmandatory。provider/tool固有skill locationsはoptional adapter。

## Verification gate

GitHub CI最低条件:

```bash
python -m py_compile kaigi kaigi_core.py kaigi_ops.py kaigi_v6.py kaigi_policy.py kaigi_capabilities.py
python -m unittest discover -v tests -p 'test_*.py'
bash -n install.sh
```

Regression coverage:

- existing core/v5/v6 regressions
- provider-neutral no-named-provider E2E
- v7 public version
- capability registry round-trip
- topic soft inference
- hard capability fail-closed
- explicit best-effort escape hatch
- cost preference for equivalent capability
- cast dry surface
- bare topic + hard capability → Council → capability_plan → proof packet → verify PASS
- no mandatory provider-specific skill directory
- optional adapter install

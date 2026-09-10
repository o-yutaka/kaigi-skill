---
name: kaigi
description: provider-neutralなマルチAI会議を操作する。capability-aware cast、Council、proof/recovery、またはChatGPT/cloud側からlocal kaigiへadvisory会議を依頼するoutbound relayが必要な時に使う。
---
# kaigi

`kaigi` はagentchattr上のprovider-neutral meeting control plane。特定providerを必須・優先にしない。

## 通常

```bash
kaigi "議題"
kaigi cast "議題"
kaigi result
```

bare topicはcapability-aware Council。個別agentへだけ送る時は `kaigi @agent-a TEXT`。

## Capability

能力はagent名から推測しない。registry / agent config / runtime factだけを使う。

```bash
kaigi caps
kaigi caps set local-a coding research --cost local --speed fast
kaigi decide "議題" --need coding,red-team --free-only
```

Topic inferenceはsoft。`--need`はhardで未充足ならfail-closed。`--best-effort-capabilities`は明示妥協時のみ。

Cast priority: required coverage → soft coverage → cost → online → speed → observed response reliability → deterministic tie-break。response reliabilityは可用性であり品質scoreではない。

## Provider-neutral invariant

- required providers = none
- provider名によるauto priority = none
- synthesizer default = balanced
- cloud API = 暗黙利用しない
- provider-specific integration = optional adapter

`kaigi policy --json` で確認する。

## Council / proof

ROUND1=独立fan-out、ROUND2=dissent/review、FINAL=synthesis。FINAL replyを実観測した場合だけcomplete。

```bash
kaigi verify [RUN_ID] --live
kaigi reconcile [RUN_ID]
kaigi resume [RUN_ID]
kaigi recover [RUN_ID]
kaigi handoff [RUN_ID]
```

Decision packetはmessage IDs / transcript hash / capability plan / final decisionを束縛する。handoffは常にadvisory、`execution_authorized=false`。

## v8 outbound relay

Cloud/ChatGPT側からlocal kaigiを使う時もlocalhostを公開しない。PC側workerがSupabase relayを外向きpollする。

```bash
kaigi relay pair --url https://PROJECT.supabase.co/functions/v1/kaigi-relay
kaigi relay start
kaigi relay status
```

Pair codeはsingle-use。端末tokenはlocal 0600 fileだけに保持し、serverはSHA-256 digestのみ保存する。

Relayが実行できるのはallowlistされたadvisory Council optionsのみ。任意shell/subcommand、remote agent固定、remote synth固定は許可しない。remote cloud利用もlocal policy既定deny。

```bash
kaigi relay config --allow-remote-cloud
kaigi relay config --deny-remote-cloud
```

Relay結果はlocal Decision packet verify後だけ成功返送する。cloudへ返すのはfinal decision / run ID / packet SHA256 / transcript SHA256。full Council transcriptはlocalに残す。

同一relay requestはlocal run IDへ束縛する。返送失敗後はlocal receiptを再送しCouncilを二重起動しない。途中runはrecover経路を使う。

### Relay progress / watchdog

`8.1-progress-watchdog` hardeningでは、authenticated heartbeatへ `run_id`, `stage/state`, ROUND1/ROUND2 reply件数, participant件数, synthだけを送る。Council本文やDecision packet本文はprogressへ出さない。

`kaigi relay status` はactive request/run/stage/reply件数を表示する。preparation・各Council stage・全体実行には独立watchdogを置き、進行停止はsuccess扱いせず明示エラーへ落とす。

`kaigi relay stop` はrelay daemonだけでなく、そのdaemon配下の実行中Council process treeも停止対象にする。再claim時は同じ `relay_request_id` に束縛されたrunをrecoverし、可能な限り二重Councilを避ける。

## 成功判定

server/API応答、online status、agent reply、FINAL reply、packet hash、relay completionなど実観測したものだけ成功扱いする。`running`/heartbeatだけをCouncil完了とみなさない。未観測をPASSと報告しない。

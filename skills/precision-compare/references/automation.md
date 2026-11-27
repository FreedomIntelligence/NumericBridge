# Durable two-side execution

Use these commands from a pinned NumericBridge checkout (or, for the core tools,
from the installed Compare `scripts` directory). The caller must confirm access,
budget, and whether detached execution is permitted. These tools do not grant
access, schedule devices, install software, or submit contributions.

## Bind and run

1. Build a plan with `python -B -m tools.workflow init` or describe the case DAG.
   Keep A and B independent until comparison. Model preparation (including hash
   verification), native preflight, capture, repeat, and comparison are separate
   stages when they are independently resumable.
2. Resolve environment-owned launch details using the authorized hosts. Put
   trusted case adapter overrides in `bindings.json`, keyed by stage ID. Each
   override supplies the literal `argv` or `remote_profile`, declared `inputs`,
   `outputs`, and task-owned checks as needed. Do not mark a template runnable
   with unresolved commands. Bind and check without connecting to a host:

   ```sh
   python -B -m tools.workflow bind --plan template.json --bindings bindings.json --output plan.json
   python -B -m tools.workflow status --plan plan.json --workspace PRIVATE_WORKSPACE
   ```

3. With explicit authorization for background execution, start one bounded
   controller from a persistent POSIX host. The caller may disconnect. The
   controller uses the same workspace and leaves a final JSON report when its
   budget ends or the plan stops. For queue-managed hosts, use the queue's allowed
   execution mechanism and do not start a detached process on a login node.

   ```sh
   python -B -m tools.workflow_controller start --plan plan.json --workspace PRIVATE_WORKSPACE --seconds 7200 --jobs 2
   ```

   Run `workflow watch --plan plan.json --workspace PRIVATE_WORKSPACE
   --watch-seconds 7200 --jobs 2` instead when a caller can supervise the process.
   `run` advances one pass; `status` launches and probes nothing. Keep the plan,
   workspace and controller report in the case checkpoint. If the **controller
   host** reboots or its process is killed, restart the same controller with its
   recorded paths in the authorized supervisor. A browser disconnect is not a
   host reboot. No daemon, queue access, or unbounded retry is implied.

### Remote stage contract

Use `remote_profile: "profiles/A-download.json"` in place of stage `argv` and
`execution`, with `outputs: {"receipt":"remote.json"}`, and set the stage's
controller `timeout` long enough for the SSH status round trip (at least 40 s
recommended). A profile is trusted local configuration, not peer-provided data:

- `schema_version: 1`, authorized `host`, absolute worker `python`, absolute
  private `state_dir` (one stable directory per logical stage; its parent must
  exist), absolute pinned NumericBridge `checkout` and its `worker_revision`
  (Git SHA for SSH). Do not replace the stage root to bypass a reservation.
- Literal `argv` with an absolute executable and optional `{run}` in arguments.
  It must run in the foreground and produce its readiness JSON only after all
  native work in that stage has settled. Do not point this at another detached
  launcher. E.g. a download exits successfully only after all shard hashes
  match the fixed manifest, then writes `{run}/ready.json`.
- `timeout_seconds` bounds the native process. `readiness` binds a path relative
  to `{run}` and exact JSON values, e.g.
  `{"path":"ready.json","equals":{"ready":true,"verified_shards":16}}`.
  `inputs` optionally pins absolute remote files by SHA-256. Keep raw evidence
  under the worker's private root; the controller receives only a receipt.
- Optional `max_attempts` (up to 4) and allowlisted `retry_exit_codes` permit
  retries after a verified retryable terminal exit. The download adapter must
  reuse verified shards and continue only the missing bytes/files. Unknown
  outcomes, connectivity loss, an unverified PID, or an SSH exit alone do not
  justify a fresh launch or a grant change.

A live or unreachable reservation produces `observe_remote`, and the bounded
controller reconciles that reservation, advancing independent work meanwhile.
A terminal receipt plus output checks unlocks dependents. An unknown reservation
stops without refreshing it; do not run a changed plan, point at another
workspace or delete existing receipts to bypass reconciliation. An old case's
bare PID/launcher record predates this protocol: verify/adopt its evidence with
the original adapter before creating a new remote stage.

## Native vLLM worker

The worker-side `python -B -m tools.native_vllm --profile NATIVE.json` checks
an isolated interpreter, authorized run space, a fixed model manifest and
candidate caches, package imports and hardware. To launch the case's **trusted
collector**, add `--execute --run-dir {run}`; use it as the foreground command
of a remote stage or as a local adapter. Required profile settings:

- `hardware`: `910c` (`device_mode: "logic-id"`) or `b200` (explicit `index` or
  `uuid`); `approved_devices` from the grant and `devices` selected from it.
  Ascend cross-checks Chip Logic ID, chip and physical inventory and probes the
  visible runtime count. B200 checks the ordered CUDA UUIDs against the exact
  selected physical GPUs. Busy/ambiguous inventory is a blocker, not permission
  to select a different device. The host's queue allocation is still required.
- Absolute `work_root` (private, 0700), `model_candidates` restricted to allowed
  cache roots, `model_files` mapping all required files to SHA-256, plus optional
  `min_free_bytes`, `vllm_version` and `extra_imports`. A failed cache match is
  for the authorized preparation stage to fix; this entrypoint installs and
  downloads nothing. Reverify remote resources in their own execution context.
- `argv` starts with `{python}` and can use `{model}` and `{run}`. Isolated
  rank/visibility variables and a short private node-local IPC/cache path are
  set before the import probe and the collector. A successful environment check
  (`environment_checked`) is not a readiness receipt. The native collector must
  verify the backend, dtype, device binding, coverage, and capture semantics.

## Private inference exchange

[Inference](inference.md#distribution-comparison) defines the comparison;
`python -B -m tools.minimal_exchange --help` defines the local wire-object
operations. The request grant records the source/destination and permissible
fields; authenticate the per-run grant digest and sender query digests using the
already authorized channel. Do not use a received file as its own authority.

Peers receive only addressed inventories of top-100 token IDs. The authorized
reviewer receives separate inventories, scores for the exact union and tail
mass, and the preselected target scores. The tool only reads or writes local
files; move just those explicitly allowed artifacts through the approved
transport. Native contracts, histories, full arrays, logs and private IDs stay
local. Keep multiple targets for a position in separate explicit sparse groups;
verify expected coverage of **all** groups. Bind same-side repeat and exact
checks as usual. This grant does not permit community sharing.

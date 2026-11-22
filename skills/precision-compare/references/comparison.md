# Compare artifacts

## Tools

Query interfaces without importing dependencies; use `--full` when building
manifests, plans or capture integration:

```sh
python scripts/tool_help.py --list                  # module + purpose
python scripts/tool_help.py compare_trees           # declaration + signature
python scripts/tool_help.py --full tree_compare     # full contract
```

| Operation | Interface | Input → output |
|---|---|---|
| Compare arrays | [array_compare](../scripts/array_compare.py): `compare_arrays` | Named arrays / NPZ → equality, coverage; `mode=nonfinite` adds categories |
| Compare state | [tree_compare](../scripts/tree_compare.py): `compare_trees`, `compare_checkpoints` | Nested state / Torch files → structure, metadata, differences |
| Group results | [group_metrics](../scripts/group_metrics.py): `reduce_groups` | Coordinates, groups, reducer → global/per-group results |
| Compare tensor artifacts | [compare_tensors](../scripts/compare_tensors.py) | Contract + two manifests → metrics, or `--mode nonfinite` anomaly counts |
| Compare target scores | [compare_target_scores](../scripts/compare_target_scores.py) | Contract + score records → NLL and logprob errors |
| Compare distributions | [compare_distributions](../scripts/compare_distributions.py) | Contract + manifests → top-100 union/tail metrics |
| Query remote distributions | [query_distributions](../scripts/query_distributions.py) | Source inventories + union queries → metrics; arrays stay at source |
| Compare assistant-body scores | [compare_scores](../scripts/compare_scores.py) | Cache-off scoring artifacts → metrics and execution checks |
| Judge results | [acceptance](../scripts/acceptance.py) | Comparator reports → verdict and route ([limits](acceptance.md)) |
| Validate artifacts | [validate_artifacts](../scripts/validate_artifacts.py) | Contract + one manifest → validity/coverage |
| Export / compare tensor views | [tensor_artifacts](../scripts/tensor_artifacts.py) | Rank inventories / view pairs → logical manifests / comparisons |
| Capture inference | [inference_capture](../scripts/inference_capture.py) | Fixed tokens + native logits → validated histories/scores |
| Capture module boundaries | [capture_boundaries](../scripts/capture_boundaries.py): `BoundaryCapture`, `replay_module` | Eager Torch model + module paths → token-subset arrays, inventory, replayable calls ([layerwise](layerwise.md)) |
| Plan msProbe capture | [msprobe_capture](../scripts/msprobe_capture.py): `plan_modules` | Modules + sizes → bounded L0 plan ([integration](layerwise.md#integrate-and-verify)) |
| Probe runtime | [runtime_probe](../scripts/runtime_probe.py) | Imports / selected files → runtime evidence or reusable results |
| Record execution | [record_run](../scripts/record_run.py) | Argv + execution JSON → receipt, logs, failure summary |
| Find prepared resources | [resource_catalog](../scripts/resource_catalog.py) | Resource identities + receipt → verified lookup |
| Run / resume a plan | [workflow](../scripts/workflow.py): `run`, `status` | Dependency plan + workspace → reuse, execution, next action |
| Collect / package evidence | [evidence_bundle](../scripts/evidence_bundle.py) | Remote files → verified local files; portable ZIP |
| Replay evidence | [replay_bundle](../scripts/replay_bundle.py) | Bundle + replay spec → reproduced reports |

Adapters own semantic correspondence, masks, shifts, input/state and dtype
identity; equal names or shapes do not establish correspondence. A leaf adapter
may return `{dtype: native_dtype, values: exported_array}` to keep dtype identity.
`exact_equal` requires finite, equal values and equal dtypes; other metrics use
FP64. Use `--all-findings` (`max_findings=None`) for every differing coordinate.
Pass `groups={name: array_names}` to `compare_arrays` for grouped reports from
one scan; use `reduce_groups` for other coordinate reducers.

Manifest tools run from the skill directory and share `--contract`, `--side-a`,
`--side-b` and `--output` (fresh paths). Put equal conditions in `common` and
declared differences in `side_conditions`; each rank records actual settings and
imported versions, checked by [conditions](../scripts/conditions.py). Declare
per-side `state` for intentionally different initial representations. See the
[fixed-history example](../examples/fixed-history-inference.md) and the
[gradient example](../examples/compare_gradients.py).

```sh
python scripts/compare_tensors.py --contract gradients.json --side-a a.json --side-b b.json --output report.json
python scripts/acceptance.py --report report.json --output acceptance.json
```

Comparator contracts keep `thresholds: null`; comparator exit codes are
0=metrics, 1=unavailable comparison, 2=CLI error. Judge results with
[acceptance](acceptance.md).

## Shared tasks

Execution, identities and evidence transport live in the shared `tools` package;
the script paths here are its entrypoints.

Keep execution options and identity manifests in adapter files.
`runtime_probe.save_identity(paths, manifest, results)` stores file hashes with
completed results; `check_identity(manifest)` returns them only while every
identity matches. Use one execution JSON for preflight and capture:

```json
{"argv_prefix":["/env/bin/python"],"path_prepend":["/env/bin"],"env":{"HF_HUB_OFFLINE":"1"}}
```

Fields are literal; `null` env values remove inherited variables. Environment
adapters own scheduling, device binding and runtime/cache paths; reuse a prepared
environment's verified launch command. In a workflow plan, declare the preflight
receipt as an output and enforce its pass conditions with `checks`.

### Wait for native execution

Use completion notifications or a bounded blocking wait; keep scheduler polling
and terminal status in the launcher or adapter. Record each command:

```sh
python scripts/record_run.py --execution environment.json --output-root runs --side SIDE --task capture --timeout 300 -- COLLECTOR ARGS
```

Failures return log tails and a `failure_class` (for example `out_of_memory`,
`import_error`, `collective_timeout`, `unsupported_operation`). A submitted job
is complete only at its native terminal status.

Fetch terminal artifacts in one transport per side; `fetch` verifies paths,
hashes and sizes, and large arrays stay at source:

```json
{"transport":["ssh","HOST","bash -lc 'python3 -I -'"],"source_dir":"/work/native-run","include":["*.json","*/command.json","*/stderr.bin"]}
```

```sh
python scripts/evidence_bundle.py fetch fetch.json NEW_SIDE_DIRECTORY
```

For dependency-linked commands, use a workflow plan (interface in its
[docstring](../scripts/workflow.py)). It reuses stages whose declared inputs,
dependency outputs, executables and execution profile are unchanged, runs
independent stages concurrently with `--jobs`, and returns `next_action` with
the skill to load on failure. Numeric `checks` (`min`, `max`) can route a failed
acceptance to `on_check_failure: precision-repair`. Start a plan from
`python scripts/workflow.py init --task inference|sft|rl --sides A,B --output plan.json`;
the [Torch example](../examples/workflow_demo.py) shows reuse, a repair and
original-workload verification.

```sh
python scripts/workflow.py run --plan task.json --workspace work [--jobs 2]
python scripts/workflow.py status --plan task.json --workspace work
```

### Recover an interrupted stage

Rerun the same plan and workspace. Use `next_action.run`, `declared_outputs` and
the case's job/process IDs to find the native run. Follow an active job; while
its state is unknown, keep dependents blocked and advance independent work. Once
terminal, reuse landed results only after their input, runtime, device, output
and coverage checks pass, and record the recovery separately from the failed
wrapper. Use `--refresh STAGE` for requested fresh measurements; descendants
rerun and unrelated stages stay reused. Register resources reused across
workloads in the resource catalog.

## Portable evidence

When requested, pack selected artifacts; the index keeps original/exported
SHA256 and sizes. Replay needs `REPLAY.json` with named cases (see the
[replay docstring](../scripts/replay_bundle.py)).

```sh
python scripts/evidence_bundle.py pack SELECTED_FILES NEW.zip
python scripts/evidence_bundle.py verify NEW.zip
python scripts/replay_bundle.py --bundle NEW.zip --output-root runs
```

## Coordinates

Pair target scores by sample/target position/token, distributions by
sample/prediction position, and tensors by logical tensor/element. In causal
inference, prediction position $t-1$ predicts target position $t$. For
`stage: observations`, declare each tensor's logical `coordinates` (sample/token
selection, boundary, invocation, execution stage) in the contract and both
manifests. Reconstruct logical coordinates from shards, packed batches and fused
projections; keep tied-weight mappings and reduction tags. Exclude padding,
deduplicate equal replicas, keep differing replicas as explicit views, and
distinguish absent gradients from present zeros.

## Target-token scores

For logprobs $\ell_{a,i},\ell_{b,i}$ over $N$ valid targets, report each side's
NLL and their absolute difference:

$$
\mathrm{NLL}_{s} = -\frac{1}{N}\sum_{i=1}^{N} \ell_{s,i}
$$

For $e_i=|\ell_{a,i}-\ell_{b,i}|$, report MAE, P99 (linear at $0.99(N-1)$) and
maximum with its coordinate. Use the global valid-target count; native loss keeps
its implemented reduction.

## Tensors

For paired tensors $a$ and $b$, use stable FP64 reductions:

| Metric | Definition |
|---|---|
| L2 error | $\lVert a-b\rVert_2$ |
| RMSE | $\lVert a-b\rVert_2/\sqrt{N}$ |
| Cosine | $a^\top b/(\lVert a\rVert_2\lVert b\rVert_2)$, null when a norm is zero |
| Norm ratio | $\lVert b\rVert_2/\lVert a\rVert_2$ |
| Maximum absolute error | $\max_i \lvert a_i-b_i\rvert$ |

Global metrics concatenate coordinates; keep counts and norms. For hidden-state
observations, also compute cosine per valid token and report its mean, P1 and
minimum, since a global cosine hides a few bad tokens. Actual updates are
$\theta_{after}-\theta_{before}$ with stored values promoted to at least FP32.
For NaN or Inf, run `compare_tensors.py --mode nonfinite` on the same inputs and
report anomaly counts and paired-finite coverage beside the finite-subset metrics.

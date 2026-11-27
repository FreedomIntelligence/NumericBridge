# Bounded diagnostic rounds

Use [diagnostic_round.py](../scripts/diagnostic_round.py) for a repeatable
launch/stop decision when the original case has not passed. Keep the ledger next
to `diagnosis.json`; the latter still owns hypotheses, findings and draft decisions.
Do not use the ledger to launch jobs or to change the original verdict.

```sh
python scripts/diagnostic_round.py round --input diagnostic-round.json
python scripts/diagnostic_round.py probe --input probe-control.json
```

## Plan, spend and resume

A v1 ledger is a JSON object:

| Field | Value |
|---|---|
| `schema_version` | `1` |
| `trigger` | Acceptance `verdict: "fail"` and `evidence` (report reference), or a `native_failure` trigger as shown below |
| `accelerator_seconds_limit`, `check_limit` | Approved limits for this round, expressed as integers; count accelerator-seconds across all devices |
| `prior_accelerator_seconds`, `prior_checks` | Optional spend/count outside the listed checks but charged to this budget; do not count the same work twice |
| `checks` | Checks in work order, including negative, interrupted and blocked checks |
| `bounded_delivery` | Optional `established`, `not_established`, `reopen_when`, and `evidence` (nonempty list of report references) |

If a supported native run fails before acceptance, record the original failure
and a separate run reproducing it under comparable conditions:

```json
{"kind":"native_failure","evidence":"original/summary.json","reproduction":"repeat/summary.json"}
```

Save that object for `diagnosis_state.py init --native-failure` and use it as
`trigger` in the round ledger. The evidence must show the same failure; a log
label alone does not prove it. There is no acceptance verdict yet.

Each check needs `id`, `kind` (`offline`, `native`, `probe`), `phase`,
`fingerprint`, `question`, `on_match`, `on_mismatch`, and
`estimated_accelerator_seconds`. The optional `resource` defaults to `cpu` for
`offline` and `accelerator` otherwise. Set `resource: "cpu"` for CPU-native
runs or taps; only CPU-only checks use zero accelerator seconds. The two outcomes
must lead to distinct next decisions. Build the fingerprint from the relevant
inputs, runtime, batch/coordinates, method and factor under test; a new label
alone does not justify re-running a negative. Use `depends_on` for earlier checks
whose usable results this one consumes. Use `after` to wait for an earlier check
to finish even if it failed to produce usable evidence. A check with `after` is
diagnostic-only: use it to investigate a crash or capture fault, not to attribute
the original discrepancy. Do not put one prerequisite in both lists. Keep
independent, low-cost checks ahead of broad native sweeps.

- `planned`: eligible if dependencies and budget allow. Reuse a finished
  check with the same fingerprint and a usable `match` or `mismatch`. A probe
  must also pass the exact control. Reference the `reused` source rather than
  launching it. `unusable_prior_result` means the same fingerprint finished
  without usable evidence; do not relaunch it. `unusable_dependency` names a
  required result that cannot support attribution. Replan from independent
  evidence or make a material change to the method before retrying.
- `running`: attach `job_receipt`. Reconcile the native process first; a wrapper
  timeout is not terminal evidence. The result can expose one independent check
  that fits alongside existing reservations. Leave dependents waiting.
- `finished`: store `actual_accelerator_seconds`, `outcome` (`match`, `mismatch`,
  `inconclusive`) and `evidence` (one or more references). For `probe` also store
  `probe_control`. A crashed probe without paired receipts can be recorded as
  `inconclusive`, not as an attribution claim. `settled` preserves the outcome
  and evidence; the budget still counts the run. `usable_result: false` prevents
  reuse and blocks consumers through `depends_on`. An `after` follow-up can have
  a decision about the fault but is marked `diagnostic_only` and cannot support
  original attribution.
  Include any deliberately repeated runs in one check and charge all of them.
- `blocked`: provide `blocker` (`access`, `resource_scope`, `missing_evidence`)
  and a concrete `detail`. Budget-blocked planned checks are marked `budget`
  by the tool. Increase limits only under an explicit new resource grant.

`run_check` recommends the first eligible planned check; update its state when
it launches. `reconcile_running` does not authorize a duplicate launch.
`summarize` asks for the supported boundary or a new discriminating check.
`deliver_bounded` requires no running or eligible check and explicit evidence,
limits and a restart condition. Cite diagnostic-only or unusable results in the
fault notes, not in `bounded_delivery.evidence` for original attribution. Check
all open hypotheses are represented; this is not a substitute for
`diagnosis_state.py audit`. Bounded findings do not change the original verdict
or create one for a native failure. To claim a pass, rerun acceptance on the
original supported workload.

## Exact same-side probe control

Before attributing a difference to an instrumented native path, pair a small
untapped run with a tapped run on the **same side**. A probe control is a v1 JSON
object with `untapped` and `tapped` run receipts. Each has:

- `side`, a distinct `run_id` and an `evidence` reference to its native receipt;
- SHA-256 fields `workload_sha256`, `runtime_sha256`, `schedule_sha256`, and
  `coordinates_sha256` for the actual inputs, shared runtime/configuration
  excluding the probe, observed native schedule and paired endpoint coordinates;
- `expected` and `observed` endpoint items in the pilot and `endpoint_sha256`
  of their canonical bytes in coordinate order (for example, serving scores or
  SFT gradients). Require full coverage of the declared pilot, not of the whole
  model. Include all ranks that contribute to the claim.

The v1 gate requires equal endpoint digests and full, equal pilot coverage. A
changed schedule, endpoint, coordinate set or incomplete pilot invalidates this
probe for attribution even if selected metrics happen to agree. Do not repack
the original batch or treat a different execution path as a no-op control.
For a genuinely noisy native path, use independently repeated no-probe evidence
under the existing [repeat-noise policy](../../precision-compare/references/acceptance.md);
report the capture as diagnostic-only if exact fidelity cannot be established.
The interface verifies receipt consistency, not raw tensor bytes or that a
caller measured the declared inputs; archive the native receipts for replay.

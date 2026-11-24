# Optimization handoff

Diagnosis writes one handoff per localized operator as
`result/optimize/<operator>/handoff.json` before entering optimization.

| Field | Content |
|---|---|
| `issue` | The diagnosis.json hypothesis ID and its finding |
| `operator` | Name, module or class, call site and the side(s) that run it |
| `shapes` | Shapes and dtypes observed in the case, with the reduction length |
| `reference_semantics` | The model's reference implementation (file, revision, lines) and what it fixes: reduction dtype, rounding point, epsilon placement, weight offset |
| `operands` | Captured operand files with SHA-256, for each side |
| `verdict` | The `operator_verdict.py` report on those operands |
| `reading` | `a_deviates`, `b_deviates`, `shared_policy` or `not_the_source` from `operator_verdict.py` |
| `slow_fix` | The framework-level fix that follows the reference, with its operator error, or `null` |
| `end_to_end` | The operator's measured contribution (the original metric with only this operator changed), or `not_measured` |
| `reproduce` | Commands for `operator` (`operator_verdict.py`), `workload`, and (once run) `kernel_check` |
| `entry` | `required` (the verdict fails the bound) or `optional` (within the bound, optimization enabled) |

Example for a fused normalization that rounds an intermediate below the
reference precision:

```json
{
  "issue": "H11",
  "operator": {"name": "fused Gemma RMSNorm with residual", "side": "A",
               "call_site": "<module.Class.method on the tested ref>"},
  "shapes": [{"tokens": 47, "hidden": 5120, "dtype": "bf16", "reduction": 5120}],
  "reference_semantics": {"source": "transformers Gemma RMSNorm",
                          "precision": "fp32 intermediate, output scaled by (1 + weight)"},
  "operands": [{"file": "late-operands.npz", "sha256": "<sha256>"}],
  "verdict": "result/optimize/gemma-rmsnorm/operator-verdict.json",
  "reading": "not_the_source",
  "slow_fix": {"variant": "wide add, norm and scale",
               "rounded_reference_mismatches": 1,
               "total": 240640,
               "report": "result/optimize/gemma-rmsnorm/kernel-report.json"},
  "end_to_end": {"metric": "target_logprob_mae", "before": 0.127079, "after": 0.112766},
  "reproduce": {
    "operator": "python scripts/operator_verdict.py --spec result/optimize/gemma-rmsnorm/operator-verdict-spec.json",
    "workload": "run-native.sh",
    "kernel_check": "python scripts/kernel_check.py --spec result/optimize/gemma-rmsnorm/kernel-spec.json"
  },
  "entry": "optional"
}
```

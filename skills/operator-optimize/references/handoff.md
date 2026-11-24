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
| `slow_fix` | The framework-level fix that follows the reference, with its operator error, or `null` |
| `end_to_end` | The operator's measured contribution (the original metric with only this operator changed), or `not_measured` |
| `reproduce` | Commands for the operator check and the original-workload rerun |
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
  "verdict": "late-replay.json",
  "slow_fix": {"description": "fp32 intermediate in composed operators",
               "error": "1 of 240640 elements off the rounded reference"},
  "end_to_end": {"metric": "target_logprob_mae", "before": 0.122136, "after": 0.112766},
  "reproduce": {"operator": "python late-replay.py", "workload": "run-native.sh"},
  "entry": "required"
}
```

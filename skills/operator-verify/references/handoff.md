# Verification handoff contracts

`operator-verify` uses explicit JSON files under `result/verify/<operator>/` (mirroring `result/optimize/<operator>/` in `operator-optimize`) so standalone runs, source extractors, and NumericBridge workflows stay decoupled.

## Input: `verify-request.json`

Written by `triage.py` (standalone) or derived from `result/optimize/<operator>/handoff.json`.

| Field | Content |
|---|---|
| `schema_version` | Integer schema version (currently `1`) |
| `mode` | `standalone` or `numericbridge_companion` |
| `target` | Operator name, backend (`triton-ascend`, `ascendc`, `triton`, `cuda`, `unknown`), source path, source SHA-256, and optional `call_site` |
| `category` | `index_and_mask`, `resource_capacity`, `pipeline_sync`, `semantic_equivalence`, or `numeric_rounding` |
| `eligibility` | `lean_only`, `model_calibration`, or `out_of_scope_numeric` |
| `route` | Target skill: `operator-verify` or `precision-compare` |
| `claim_kind` | `refutation` (buggy counterexample), `universal_proof` (fixed invariant), or `equivalence` |
| `upstream_handoff` | Optional path to `result/optimize/<operator>/handoff.json` |
| `upstream_issue` | Optional upstream issue metadata propagated from `handoff.json` |
| `model_modules` | Recommended Lean library modules (e.g., `AscendLean.UBModel`, `AscendLean.PipeModel`, `AscendLean.TritonModel`) |

## Output: `verify-report.json`

Written by `lean_check.py` to `result/verify/<operator>/verify-report.json` (or `--output`).

| Field | Content |
|---|---|
| `schema_version` | Integer schema version (currently `1`) |
| `status` | `passed`, `failed`, `audit_failed`, or `toolchain_missing` |
| `verdict` | `proved`, `refuted`, `passed_unclassified`, `inconclusive`, or `not_run` |
| `claim_kind` | `universal_proof`, `refutation`, `equivalence`, or `unspecified` |
| `project` | Resolved Lake project directory path (or `null` for standalone `lean`) |
| `toolchain` | Recorded `lean --version`, `lake --version`, and `lean-toolchain` pin when present |
| `sources` | List of `{role, path, sha256}` for the Lean target and optional kernel source files |
| `audit` | Static + compiler check summary: `sorry_count`, `admit_count`, `custom_axioms`, `unsafe_escapes`, `has_theorems`, `compiler_sorry_warning`, `clean` |
| `theorems` | Declared `theorem` / `lemma` names discovered in the verified Lean target (must be non-empty for `passed`) |
| `preconditions` | Explicit or discovered preconditions required for the proved invariant to hold (e.g., `at least one finite logit`, `len >= 1`) |
| `tcb_assumptions` | Explicit hardware/modeling assumptions required to interpret the result |
| `counterexample` | Optional counterexample summary string (`--counterexample`) when `verdict == "refuted"` |
| `command` | Exact command argv, exit code, duration in seconds, and output tails |

Example `verify-report.json`:

```json
{
  "schema_version": 1,
  "status": "passed",
  "verdict": "proved",
  "claim_kind": "universal_proof",
  "toolchain": {
    "lean_version": "Lean (version 4.36.0)",
    "lean_toolchain": "leanprover/lean4:v4.36.0"
  },
  "sources": [
    {"role": "lean_target", "path": "Case3_TritonUB_Fixed.lean", "sha256": "<sha256>"}
  ],
  "audit": {
    "sorry_count": 0,
    "admit_count": 0,
    "custom_axioms": [],
    "unsafe_escapes": [],
    "has_theorems": true,
    "compiler_sorry_warning": false,
    "clean": true
  },
  "theorems": ["fixed_safe_all_hidden"],
  "preconditions": ["1 <= hidden_size <= 8192"],
  "tcb_assumptions": ["A5 UB limit = 2031616 bits (248 KiB)"],
  "counterexample": null,
  "command": {
    "argv": ["lake", "env", "lean", "Case3_TritonUB_Fixed.lean"],
    "exit_code": 0,
    "duration_sec": 0.42
  }
}
```

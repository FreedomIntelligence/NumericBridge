---
name: operator-verify
description: Formally verify accelerator operators (Triton-Ascend, Ascend C, Triton, CUDA) without target hardware using Lean 4 to prove index/mask safety, on-chip resource bounds, pipeline sync safety, and discrete semantic equivalence, or produce machine-checked counterexamples.
---

# Operator verification

Run tools from this skill directory. Each tool's `--help` gives its interface.

| Step | Tool | Output |
|---|---|---|
| Scan upstream kernel files for discrete bug candidates | [audit_scan](scripts/audit_scan.py) | `audit-findings.json` with candidate rule hits, lines, hashes, and categories |
| Classify verification scope and build a request | [triage](scripts/triage.py) | `verify-request.json` with category, eligibility, route, and model modules |
| Type-check Lean 4 proofs and audit `sorry` / axioms | [lean_check](scripts/lean_check.py) | `verify-report.json` with toolchain, hashes, theorem list, audit status, and verdict |

Read [taxonomy](references/taxonomy.md) for the discrete-verification vs floating-point boundary, [handoff](references/handoff.md) for the input/output JSON contracts, and [TCB](references/tcb.md) for hardware modeling assumptions.

`operator-verify` pairs with [operator-optimize](../operator-optimize/SKILL.md): `operator-verify` proves discrete structural, memory, synchronization, and algebraic/bit-level properties offline without an accelerator; pure floating-point rounding accuracy and runtime timing remain in [precision-compare](../precision-compare/SKILL.md) and [operator-optimize](../operator-optimize/SKILL.md).

## Entry

1. **Standalone mode.** Start from a kernel source file, PR diff, or Lean 4 target. Run `triage.py` to classify the issue into one of the discrete categories (`index_and_mask`, `resource_capacity`, `pipeline_sync`, `semantic_equivalence`) or route `numeric_rounding` back to dynamic precision tools.
2. **NumericBridge companion / pre-gate mode.** Accept an operator handoff (`result/optimize/<operator>/handoff.json`) or a `diagnosis.json` hypothesis, verify discrete invariants offline, and write `result/verify/<operator>/verify-report.json` as evidence alongside dynamic checks.
3. **Repository audit mode.** Scan candidate operator files in an upstream repository (e.g., `sgl-kernel-npu`, `vllm-ascend`, `RL-Kernel`) to surface unreported defects, rule out false positives, and document implicit preconditions. Confirmed defects hand off directly to [upstream_status.py](../precision-compare/scripts/upstream_status.py) and [pr_draft.py](../precision-compare/scripts/pr_draft.py) for upstream PR delivery.

## Verify

1. **Classify scope.** Run `triage.py` on the operator target or handoff. Stop and route `numeric_rounding` (quantitative FP rounding error) to `precision-compare` or `operator-optimize`; keep qualitative `±inf` / `NaN` propagation and poisoning in `semantic_equivalence`.
2. **Bind to a Lean target.** Map the kernel property to a reusable semantics module in `lean_lib/` (see [lean_lib/README.md](lean_lib/README.md)). Distinguish **refutation** (concrete counterexample proving a buggy kernel fails) from **universal proof** (invariant proved for all symbolic shapes/inputs). `lean_lib/` targets pure Lean 4 core with **no Mathlib** — use core tactics (`let`, `omega`, `decide`, `simp`, `by_cases`, `induction`) rather than Mathlib-only tactics (`set`, `linarith`, `ring`, `by_contra`).
3. **Run machine check.** Run `lean_check.py` against the target `.lean` file and Lake project, e.g. `python3 scripts/lean_check.py TARGET.lean --project lean_lib --source KERNEL.py --claim-kind refutation --output verify-report.json`. It records Lean/Lake versions, source SHA-256 digests, public theorems, `sorry`/`admit`/custom-`axiom` audit counts, compiler status, and verdict.
4. **Disclose TCB, preconditions, and deliver.** Record explicit modeling assumptions per [TCB](references/tcb.md) and any required or discovered `preconditions` in `result/verify/<operator>/verify-report.json` (or the standalone output directory).

```xml
<rules>
  A verdict of `proved` or `refuted` requires zero compiler errors, zero `sorry`
  or `admit` tokens, and zero unapproved custom axioms.
  Never claim floating-point rounding error bounds from a discrete Lean model;
  quantitative precision belongs to `precision-compare` and `kernel_check.py`
  (discrete `±inf`/`NaN` abstract-domain propagation is in scope under `semantic_equivalence`).
  Always preserve source SHA-256 digests, Lean toolchain identity, discovered
  `preconditions`, and explicit TCB assumptions in `verify-report.json`.
  When `lake` or `lean` is unavailable on the host, report `toolchain_missing`
  and `verdict: "not_run"` rather than fabricating or skipping verification.
  Keep experimental corpora, raw benchmarks, and `.lake/` build caches outside
  the tracked skill directory.
</rules>
```

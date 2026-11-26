# Trusted Computing Base (TCB) and modeling boundaries

Every `verify-report.json` must distinguish what is mechanically checked by the Lean 4 kernel from what remains an explicit modeling assumption.

## Mechanically verified (inside the Lean kernel)

- Logical soundness of all theorems and counterexample witnesses in the verified `.lean` target, audited for zero `sorry`, zero `admit`, and zero unapproved custom `axiom` declarations.
- Universal quantification over symbolic parameters (e.g., arbitrary `Nat` dimensions, `BitVec 64` inputs, arbitrary loop/chunk counts) within the formalized model.

## Explicit modeling assumptions (outside the Lean kernel)

1. **Source-to-Lean abstraction gap.** Until a certified compiler frontend exists, mapping from Triton Python AST or Ascend C C++ source into Lean 4 expressions is part of the TCB. Record the exact source path, SHA-256 digest, and extractor version.
2. **Hardware constants and execution semantics.**
   - Unified Buffer limits (e.g., Ascend A3 `192 KiB`, Ascend A5 / 950PR `248 KiB = 2,031,616 bits`) reflect documented compiler limits for the named architecture.
   - DaVinci pipeline synchronization models (`PIPE_MTE2`, `PIPE_V`, `PIPE_MTE3`, `PIPE_S`, and all 12 directed `HardEvent` FIFO queues) abstract hardware ordering at buffer/event granularity rather than cycle-accurate microarchitecture; `SyncWatermark` conservatively tracks direct cross-pipe edges without transitive vector-clock propagation (e.g., `MTE2 -> V -> MTE3`).
   - Vector register widths (e.g., 256-byte register = 64 `float32` lanes) match the targeted AscendC ISA.
3. **Numerical boundary.** Discrete bit-vector, integer, or extended-order models do **not** establish floating-point rounding accuracy; pair with `precision-compare` or `operator-optimize` (`kernel_check.py`) whenever floating-point precision matters.

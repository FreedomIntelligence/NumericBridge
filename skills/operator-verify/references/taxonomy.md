# Verification taxonomy

Classify each operator issue by **why dynamic numerical testing misses it**, using self-explanatory `snake_case` category labels consistent with NumericBridge diagnosis and optimization records.

| Category | Typical accelerator mechanism | Why numerical tests miss it | Eligibility | Route |
|---|---|---|---|---|
| `index_and_mask` | Tiling tail out-of-bounds, padding contamination (`padding_option="zero"` / `other=0.0`), `int32` overflow/truncation, shared vs independent mask counters, wrong `boundary_check` axis, unconditional OOB scatter/store, ignored tensor stride or grid-stride mismatch, indirect load from unpadded masked positions | Unit tests mostly cover aligned, divisible, single-row, or power-of-2 shapes where tail/stride/multi-row branches never fire | `lean_only` | `operator-verify` |
| `resource_capacity` | On-chip Unified Buffer / SRAM overflow (including auto multi-buffering doubling and hardcoded `BLOCK_M` scaling), byte-vs-element UB indexing overrun, 32B alignment violations, mixed-width in-place UB address overlap (`AscendC::Cast` aliasing), silent host-side tile clamping without `RuntimeError` | Small and power-of-2 shapes fit; large, non-power-of-2, or narrowed-dtype shapes overflow or alias on-chip memory | `lean_only` | `operator-verify` |
| `pipeline_sync` | Missing `SetFlag`/`WaitFlag` across Cube/Vector/Scalar/MTE pipes, wrong `HardEvent` pipe pair (e.g., `MTE2_MTE3` guarding `PIPE_S`), inter-core `IBWait`/`IBSet` ring deadlocks on empty segments, UB read/write races, leaked event tokens | Timing-dependent, architecture-dependent (passes on A3, fails on A5), or masked by single-head / non-empty-segment tests | `model_calibration` | `operator-verify` |
| `semantic_equivalence` | Fused kernels, `torch.compile` lowering mismatches, bit-level workarounds (`BitVec 64` select), tiled reductions, qualitative IEEE-754 special-value (`±inf` / `NaN`) propagation & poisoning (e.g., online softmax `(-inf) - (-inf) = NaN`), wrong inverse transpose permutation, wrong Welford online denominator (`step` vs per-lane `cnt`), inverted boolean mask / missing `LOG2E` scaling, uninitialized output quadrant, store dtype truncation, hidden preconditions | Fusion introduces small FP drift so loose tolerances hide logic bugs; dense random tests (`randn`) never trigger masked `-inf` leading tiles, `head_first`, `training=True` multi-step tiles, or boolean/additive masks | `lean_only` | `operator-verify` |
| `numeric_rounding` | Quantitative BF16/FP16 accumulation order, transcendental rounding tolerance (ULP/RMSE), cross-stack FP drift | Directly measurable against FP64 on captured operands | `out_of_scope_numeric` | `precision-compare` / `operator-optimize` |

## Eligibility states

- `lean_only`: Self-contained discrete arithmetic, bit-vector, mask, or capacity property requiring no hardware timing calibration.
- `model_calibration`: Requires an explicit hardware execution model (such as DaVinci pipeline FIFO event ordering) documented in [TCB](tcb.md).
- `out_of_scope_numeric`: Pure floating-point rounding accuracy or unformalized numerical behavior; handled by dynamic NumericBridge tracks.

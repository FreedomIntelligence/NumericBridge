# Verification taxonomy

Classify each operator issue by **why dynamic numerical testing misses it**, using self-explanatory `snake_case` category labels consistent with NumericBridge diagnosis and optimization records.

| Category | Typical accelerator mechanism | Why numerical tests miss it | Eligibility | Route |
|---|---|---|---|---|
| `index_and_mask` | Tiling tail out-of-bounds, padding contamination, `int32` overflow/truncation, shared vs independent mask counters | Unit tests mostly cover aligned, divisible, or power-of-2 shapes where tail branches never fire | `lean_only` | `operator-verify` |
| `resource_capacity` | On-chip Unified Buffer / SRAM overflow (including auto multi-buffering doubling), 32B alignment violations | Small and power-of-2 shapes fit; large or non-power-of-2 hidden sizes overflow on-chip memory | `lean_only` | `operator-verify` |
| `pipeline_sync` | Missing `SetFlag`/`WaitFlag` across Cube/Vector/MTE pipes, UB read/write races, deadlocks, leaked event tokens | Timing-dependent, architecture-dependent (passes on A3, fails on A5), or masked by single-head tests | `model_calibration` | `operator-verify` |
| `semantic_equivalence` | Fused kernels, `torch.compile` lowering mismatches, bit-level workarounds (`BitVec 64` select), tiled reductions, qualitative IEEE-754 special-value (`±inf` / `NaN`) propagation & poisoning (e.g., online softmax `(-inf) - (-inf) = NaN`), hidden preconditions | Fusion introduces small FP drift so loose tolerances hide logic bugs; dense random tests (`randn`) never trigger masked `-inf` leading tiles or tie/edge preconditions | `lean_only` | `operator-verify` |
| `numeric_rounding` | Quantitative BF16/FP16 accumulation order, transcendental rounding tolerance (ULP/RMSE), cross-stack FP drift | Directly measurable against FP64 on captured operands | `out_of_scope_numeric` | `precision-compare` / `operator-optimize` |

## Eligibility states

- `lean_only`: Self-contained discrete arithmetic, bit-vector, mask, or capacity property requiring no hardware timing calibration.
- `model_calibration`: Requires an explicit hardware execution model (such as DaVinci pipeline FIFO event ordering) documented in [TCB](tcb.md).
- `out_of_scope_numeric`: Pure floating-point rounding accuracy or unformalized numerical behavior; handled by dynamic NumericBridge tracks.

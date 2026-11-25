# Formal model library (`lean_lib/`)

This directory contains the reusable `AscendLean` Lean 4 library for offline accelerator operator verification (`leanprover/lean4:v4.36.0`, zero external dependencies).

## Modules

| Module | Scope | Key primitives |
|---|---|---|
| `AscendLean.PipeModel` | `pipeline_sync` | Ascend DaVinci async pipes (`PIPE_MTE2`, `PIPE_V`, `PIPE_MTE3`, `PIPE_S`), all 12 directed FIFO `HardEvent`s (`MTE3_MTE2`, `V_S`, `S_MTE2`, etc.), `runOps`, `VerifiedSafe` |
| `AscendLean.UBModel` | `resource_capacity`, `index_and_mask` | `A3_UB_BITS` (192 KiB), `A5_UB_BITS` (248 KiB = 2,031,616 bits), `INT32_MAX`, `INT64_MAX`, `nextPow2`, `le_ceilDiv_mul`, AscendC `updateMask` / 4-way packed stores |
| `AscendLean.TritonModel` | `semantic_equivalence`, `index_and_mask` | `BitVec 64` bitwise select, `ExtInt` tiled reductions, IEEE-754 online-softmax `(-inf) - (-inf) = NaN` abstract domain, masked-load `LaneVal` padding taint, `indexedLoadSafe` |

## Build and check

```bash
cd skills/operator-verify/lean_lib
lake build
```

Verify any external `.lean` target against this library via [lean_check.py](../scripts/lean_check.py):

```bash
python3 skills/operator-verify/scripts/lean_check.py TARGET.lean \
  --project skills/operator-verify/lean_lib \
  --output result/verify/<operator>/verify-report.json
```

Keep `.lake/` build caches untracked (ignored by the repository `.gitignore`).

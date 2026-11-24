# Load a kernel as an operator override

Keep the framework's call site and replace only the operator behind it, in a
case-private environment. Record how each variant is loaded so the original
workload can run with it and without it.

| Stack | Override point | Kernel language |
|---|---|---|
| PyTorch eager (GPU or NPU) | The module's `forward`, or a `torch.library` implementation for the op's dispatch key | Triton, CUDA extension, Ascend C through a torch_npu custom op |
| vLLM | The layer's custom op (`CustomOp.forward_cuda` / `forward_native`) used by the model class | Triton, CUDA |
| vLLM-Ascend | The layer's `forward_oot`, or the registered `torch.ops` call it makes | triton-ascend, Ascend C |
| Transformers | The modeling module's class method or kernel hook | Triton, CUDA, Ascend C |

## Rules

- Patch through a module the launcher imports from the case directory (a
  plugin, or `sitecustomize` on the case's `PYTHONPATH`); do not edit packages
  installed in shared images.
- Select the variant by an environment variable read at import, so one build
  serves the baseline, the slow fix and every kernel. Record the variable in the
  run's command receipt.
- Confirm the override is active: count calls or check the dispatched function
  in the first forward, and fail the run when the count is zero.
- Keep graph capture and compilation modes as in the original case. If a
  variant needs eager mode, say so and time the baseline in eager mode too.
- Build toolchains and compiled kernels under the case directory, and point the
  compiler caches there (for example `TRITON_CACHE_DIR`, `TORCH_EXTENSIONS_DIR`
  and the CANN kernel cache).
- On a host without internet, use toolchains already in the image, or wheels
  and sources transferred from another host in the request. Record versions.

## Timing

Time on the original hardware with the case's shapes. Synchronize the device
before reading the clock, discard warmup iterations, interleave the variants in
one session and give `kernel_check.py` the raw per-iteration samples. Record the
device, driver, toolkit and framework versions, warmup and sample counts in
`timing.conditions`. End-to-end speed (tokens/s or step time, TTFT and TPOT for
serving, peak memory) comes from the original workload run with each variant,
paired with a baseline run on the same host.

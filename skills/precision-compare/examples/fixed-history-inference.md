# Example: fixed-history inference

## Task

Compare Qwen3.5-9B-Base on NVIDIA H200 and Ascend 910C through their native
SGLang stacks. Connections, model snapshots and tokenized data are supplied;
platform-supported dependency versions may differ.

Use BF16 weights and cache, TP=1 and one request at a time. Select two records with
32/64-token prompts and their next three tokens as fixed continuations. Collect
continuation scores and distributions globally and per prefill/first-decode/
later-decode stage. Setup, adapter code, jobs and retries are authorized within 30
accelerator-minutes per side, one device each.

## Collection

Use the [inference recipe](../references/inference.md) and the
[tool index](../references/comparison.md#tools). Reuse the supplied environments,
adapt the native SGLang entrypoint where needed, and capture full-vocabulary
scores before forcing the fixed tokens, preserving each side's cache transitions.

For prompt length `L`, distribution positions `L-1, L, L+1` predict targets at
`L, L+1, L+2`; the final prefill row supplies the first continuation score. Pair by
these logical positions, not native row names.

## Expected result

Six paired target scores and six distributions, two per stage, with the selected
metrics, the [acceptance](../references/acceptance.md) verdict (`nb-default-v1`;
top-1 agreement is reported but not judged below 100 positions), actual
configurations, coverage, maximum-error coordinates and reproduction paths. Keep
full arrays at source and release device allocations when finished.

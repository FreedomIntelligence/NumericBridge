# Inference

## Collection

Replay the selected token IDs, position IDs, attention masks and prediction
positions; check checkpoint, tokenizer, config and numerical settings against the
contract. At prediction position $t-1$, collect the distribution predicting $x_t$.

For target scores, save labels, logical positions and full-vocabulary-normalized
logprobs. For distributions, use full-vocabulary-normalized scores before sampling
processors, keeping model-defined scaling/softcapping. When deriving logprobs from
logits, declare the FP32/FP64 policy and record native score dtype separately;
save full rows or queryable arrays with the vocabulary mapping, and keep requested
label scores outside the top 100.

### Full forward

For cache-off scoring, use eval/no-grad and collect the requested positions in the
native forward pass; record cache flags and forward shapes.

### Cached execution

Feed the same predetermined continuation while each side evolves its native
cache. Record prefill/decode stages, batching, cache settings and context length.
For generated continuations, pair scores only while token histories match.

## Framework integration

Record attention backend, TP, KV and recurrent-state dtypes, graph/cache/chunking
and batching. Map reordered requests and chunks to absolute prediction positions:
prompt token $x_t$ uses position $t-1$ and the final prefill row supplies the
first output distribution; account for BOS, templates and initial prompt entries.
Reconstruct vocabulary shards and exclude padding. Inspect the installed
implementation when score semantics or mappings are unresolved.

| Framework / entrypoint | Inspect in the installed implementation |
|---|---|
| PyTorch / [Transformers](https://huggingface.co/docs/transformers/main_classes/text_generation) | Native forward and cache state; raw logits versus processed generation scores |
| [vLLM](https://docs.vllm.ai/en/latest/api/vllm/sampling_params/) | `prompt_logprobs`, output `logprobs`, raw-score mode, full-vocabulary limits; initial `None` entries, chunk offsets, platform plugins |
| [SGLang](https://docs.sglang.io/docs/basic_usage/sampling_params) | `return_logprob`, `logprob_start_len`, top/selected-ID queries, score transforms; radix-cache and extend/decode behavior |
| [TensorRT-LLM](https://nvidia.github.io/TensorRT-LLM/features/additional-outputs.html) | Execution backend, context/generation logits, vocabulary layout, build/precision settings |
| [LMDeploy](https://lmdeploy.readthedocs.io/en/latest/) | PyTorch/TurboMind backend, native score outputs, request/token/cache mappings |
| [MindIE-LLM](https://github.com/Ascend/MindIE-LLM) | Service/model backend, score access, scheduled request/prefill/decode mappings |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | GGUF/checkpoint identity, quantization, tokenizer conversion, context/logit-row mapping |

The [inference capture tool](../scripts/inference_capture.py) saves full-vocabulary
logprobs before forcing each fixed token, checks prefill/decode histories and
exports distribution and target-score records (FP32 by default;
`logprob_dtype='float64'` derives offline from copied native values).
`validate_generation_scores` checks context/generation row alignment. For other
engines, follow [native adaptation](../../framework-adapt/SKILL.md#execute): locate
the native scoring path and score transforms, map outputs to token/vocabulary
coordinates, and for cached execution identify prefill/decode boundaries.

## Metrics

| Observations | Measures |
|---|---|
| Output distributions | Tail-merged Jensen–Shannon divergence, top-1 agreement, probability RMSE, maximum probability error |
| Target scores | Each side's NLL and absolute difference; logprob MAE, P99, maximum |
| Coverage | Paired positions/targets, each side's top-100 and union mass; top-100 overlap |

For exact-equivalence checks, report equality, coverage and mismatch coordinates.
Across hosts, the [distribution-query tool](../scripts/query_distributions.py)
exchanges top-100 inventories, union scores and tail statistics while arrays stay
at source. Keep the prefill/first-decode/later-decode and context-length breakdowns
the task requests. Judge with [acceptance](acceptance.md).

### Distribution comparison

1. Take each side's top 100 token IDs per position (exact ties by ascending ID).
2. Form their union $S$ and read both sides' full-vocabulary probabilities for $S$.
3. Merge probability outside $S$ into one `other` category; keep each side's
   top-100 and union mass.
4. Compute, with probabilities $p$ and $q$:

| Metric | Per position | Aggregate |
|---|---|---|
| Tail-merged JS divergence | $\frac12\sum_i[p_i\log(p_i/m_i)+q_i\log(q_i/m_i)]$, $m=(p+q)/2$, $i\in S\cup\{other\}$ | Mean, P99, maximum and coordinate |
| Top-1 agreement | Equal argmax IDs; mismatches keep both tokens' probabilities and each side's top-1/top-2 margin | Fraction and count |
| Probability RMSE | $\sqrt{\sum_{i\in S}(q_i-p_i)^2/\lvert S\rvert}$ | Mean, P99, maximum |
| Maximum probability error | $\max_{i\in S}\lvert q_i-p_i\rvert$ | Mean, P99, maximum and token |
| Mass coverage | Own top-100 and union mass per side | Mean, P1, minimum |
| Top-100 overlap | $\lvert T_A\cap T_B\rvert/100$ | Mean |

Weight positions equally and use FP64 with a log-space mixture (nats); zero
probability contributes zero.

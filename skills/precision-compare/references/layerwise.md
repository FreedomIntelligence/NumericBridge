# Layerwise comparison

Within a confirmed scope (a repair request includes it), choose samples,
boundaries, collectors and trials autonomously within the resource budget. Ask
only when changing the comparison question or the resource limit.

## Collection

Use [capture_boundaries.py](../scripts/capture_boundaries.py) on both sides; the
same hooks run on GPU and NPU, and the outputs feed
[localize.py](../../precision-repair/scripts/localize.py) and
[replay.py](../../precision-repair/scripts/replay.py) without conversion.

1. **Screen.** Capture the block boundaries (`default_boundaries(model)`) for a
   token subset (`valid_tokens(mask, limit=64)`, same list on both sides) with
   `inventory=True` on the first run. Token subsets keep full-tensor capture cheap
   and give the per-token cosine that statistics alone cannot.
2. **Add noise.** Repeat the capture once on one side; pass it as `--noise-a` so
   growth within run-to-run variation is ignored.
3. **Rank and refine.** Run `localize.py --side-a A.npz --side-b B.npz --order A.json`;
   capture the children it names in `next.capture`, again for both sides. Several
   candidate intervals can share one run.
4. **Replay the leaf.** Save the leaf module's call on both sides
   (`save_calls=[path]`), then run each side's module on both calls with
   `replay_module` (and `dtype=torch.float64` on CPU for a reference) to build the
   [cross-replay spec](../../precision-repair/examples/causal-replay.md); use
   `inject` to run it inside the full model instead.

Pair boundaries by computational meaning, not names or call order; record the
module path, invocation, direction, rank and token selection (the inventory does).
Intermediate differences include upstream propagation; only same-input replay
isolates a module. For backward boundaries capture with `directions=('backward',)`;
`output` is the incoming gradient. `first_nonfinite` in the inventory names the
first boundary with NaN/Inf. Disabling the KV cache can simplify a diagnostic
capture; it does not represent an original path that uses the cache. Before
attributing a native-path difference from hooks, compare a
same-side untapped/tapped pilot under the [exact probe gate](../../precision-repair/references/diagnostic-round.md#exact-same-side-probe-control).
Keep the actual scheduling and endpoint coordinates, not just intended batch
settings; an unverified pilot is diagnostic-only.

## Graph execution and msProbe

Hooks fire only in eager execution. CUDA graphs, ACLGraph, `torch.compile` and
compiled serving paths bypass them; running eager for diagnosis is a narrower
check of a different path. When the graph path itself is in question, compare
eager against graph outputs first, or use msProbe:

| Situation | Collector |
|---|---|
| Eager PyTorch training or inference (Transformers, TRL, most trainers) | `capture_boundaries.py` |
| ACLGraph whole-network capture, Inductor comparison on Ascend | msProbe |
| vLLM/SGLang serving with dynamic start/stop | msProbe, or the engine in eager mode with hooks |
| VeRL/Slime train-inference consistency already wired to msProbe | msProbe |

## Integrate and verify

For msProbe, pin a compatible `mindstudio-probe` release
([install guide](https://msprobe.readthedocs.io/zh-cn/latest/zh/install_guide/msprobe_install_guide.html))
constrained to the selected framework and runtime, and test imports of the model
class and collector before allocating devices. Build L0 selectors with
[`plan_modules`](../scripts/msprobe_capture.py) from the capture inventory's
`modules` sizes (L0 list entries match substrings). Construct
`PrecisionDebugger(config_path=...)` in each worker before the selected calls,
bracket execution with `start(model=...)`/`stop()`, and call `step()` after the
captured iteration. Parameter-gradient counters count `grad_fn` triggers, not
module calls.

For any collector, trial a small portion first: expected boundaries fire, per-rank
files complete, shapes and invocations match the plan, and outputs with collection
on and off agree. Map records to logical coordinates and keep source paths,
unmatched observations and per-rank coverage. For formal observation comparison,
export them as `stage: observations` through the [comparison interfaces](comparison.md).
See the msProbe [capture](https://msprobe.readthedocs.io/zh-cn/latest/zh/user_guide/dump/pytorch_data_dump_instruct.html)
and [configuration](https://msprobe.readthedocs.io/zh-cn/latest/zh/user_guide/dump/config_json_introduct.html)
guides for options.

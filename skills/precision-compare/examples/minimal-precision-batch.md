# Fixed precision batch

The bundled [100-example v1 dataset](precision-batch-v1.json)
(`examples/precision-batch-v1.json` under the `precision-compare` skill) holds
coin, chip draw, spinner and die events, each with a unique ID and a fixed result.
Inference and SFT prompts state the result; RL prompts ask for a guess and keep
the result in the separate answer key. The [v1 lock](precision-batch-v1.lock.json)
pins the exact batch; the smoke install checks it against the lock and the
[generator](generate_precision_batch.py):

```sh
python -B examples/generate_precision_batch.py --count 100 --seed 17 --group-size 4 --output NEW.json
```

Without a specified dataset, use all 100 events in order on both sides of the
formal comparison. Preflight may use the smallest valid subset; diagnostic replay
may use only the triggering events. Event count does not set samples per event,
groups per update or updates. Record the batch ID and hash, selected event IDs in
order, and the chosen views and groups. Publish a new dataset version for any
content change.

Views `inference`, `sft`, `rl_online`, `rl_replay` and `answer_key` share `id`;
join `rl_replay` with `rl_online` for its prompt. Feed only the relevant `prompt`
to the model and keep `answer_key` with the evaluator. The original
[four-prompt fixture](minimal-precision-batch.json) suits a manual smoke check.
Freeze rendered token IDs, masks and initial states once the model and runtime are
selected.

| Path | Check |
|---|---|
| Inference | Score the fixed A/B continuations at matching histories. Mix short and long prompts to exercise batch padding; extend the continuation when decode after the first output is in scope. |
| SFT | Use the visible result and its `completion`; compare the loss mask, loss, gradients and one optimizer update. |
| RL, fixed replay | Use the fixed `completions`; reward 1 for an exact target match after stripping whitespace, else 0. Check a mixed group for a nonzero reward-driven reference gradient; all-correct and all-wrong groups are controls for the group-centered term. |
| RL, online | Sample from the policy with the same prompts and reward rule; record actual groups and invalid completions. Claim mixed-reward coverage only for valid actions that reached the objective; verify the next rollout uses the updated policy when handoff is in scope. |

Check the reward-driven gradient before aggregating prompts, since opposing groups
can cancel. Fixed choices are replay inputs; keep native old-policy scores and
mark injected or recomputed dependencies ([RL recipe](../references/rl.md#collection)).
Extend the batch only for an uncovered path (minimum group, accumulation window,
collective, context length) and record which condition it triggered. The batch
measures numerical behavior, not task accuracy.

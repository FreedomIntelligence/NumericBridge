# RL

## Native preflight

Apply the [native preflight](../../framework-adapt/SKILL.md#native-preflight) to the
RL path in scope. Find the minimum valid sample count, complete group size,
microbatch and accumulation window, with prompts and responses that yield valid,
unmasked actions and the workers and topology the path needs. One sample can
bypass or invalidate the computation, for example group-based advantages.

| Requested path | Evidence in the reduced run |
|---|---|
| Rollout only | Native generation returns valid actions and the requested scores. |
| Training update | The native trainer consumes valid trajectories, evaluates the objective, runs backward and applies an optimizer update; captured state identifies it. Fixed trajectories are valid; label injected dependencies. |
| Online rollout-to-update loop | Native rollout and reward/advantage feed a training update, and the next rollout's recorded policy version is the updated one. |

One valid update plus the next rollout checks one handoff; repeated updates, long
sequences or scale need more iterations or the target workload.

## Collection

For the first full-loop comparison, start with a fixed prompt set sized for a
valid update, one optimizer update, and the first rollout using the updated
policy. Keep evidence of policy consumption separate from evidence of numerical
agreement. Pair per-action scores only at matching histories and distinguish
common model state from independently updated state at the same step number.

For each in-scope stage, verify the common initial model and optimizer state
where applicable, then pair the required scoring, reward/advantage, loss,
selected gradients and actual parameter deltas. When both native stacks are in
scope and online divergence prevents those pairs, replay the smallest valid
fixed trajectory batch on both stacks from the matched state. Reuse existing
paired captures and replay only enough to cover the missing stage. Mark the
aligned comparison incomplete if a required native capture is unavailable;
CPU objective recomputation or a score-only probe covers only its own stages.

For each claimed group-centered reward gradient check, retain mixed-group and
valid-action counts after filtering and masking on both sides, plus a nonzero
reward-driven reference gradient before cross-prompt aggregation. Put coverage
in the comparison receipt. An optimizer step or nonzero total gradient cannot
satisfy this check. If coverage is missing, reuse matching evidence or run the
smallest [fixed mixed-reward replay](../examples/minimal-precision-batch.md#fixed-precision-batch)
that covers it. Keep online and replay results separate; do not rerun the online
batch just to fill this gap.

For each in-scope stage, verify common initial model and optimizer state, then
pair scoring, reward/advantage, loss, selected gradients and actual parameter
deltas. When online divergence prevents those pairs, replay the smallest valid
fixed trajectory batch on both stacks from matched state, reusing existing paired
captures. Mark the aligned comparison incomplete when a required native capture
is missing; CPU recomputation or score-only probes cover only their own stages.

For a group-centered reward gradient check, record mixed-group and valid-action
counts after filtering and masking on both sides and a nonzero reward-driven
reference gradient before cross-prompt aggregation, in the comparison receipt.
If missing, run the smallest [fixed mixed-reward replay](../examples/minimal-precision-batch.md#fixed-precision-batch);
keep online and replay results separate.

For replay, fix prompt/response IDs, action positions, masks and EOS/truncation.
For objectives, keep the effective old/reference scores, rewards, advantages,
returns and values, marked injected or recomputed. Preserve native score
transforms, detach behavior and dynamic masks. Rewards, masks and valid counts are
exact checks for [acceptance](acceptance.md).

For training, apply the [training stages](sft.md#training-stages) to actor and
critic and identify the optimizer step, accumulation window and actor/critic order.

| Objective component | State to capture |
|---|---|
| Clipped policy objective | Ratios, bounds and branch decisions; token PPO uses $r=\exp(\log\pi_{current}-\log\pi_{old})$ |
| PPO critic | Current/old values, returns, bootstrap masks, value clipping |
| GRPO | Complete groups, reward components, mean/std, normalization, epsilon |
| KL/entropy | Estimator, direction, coefficient, sampling distribution |

## Framework integration

Inspect the installed implementation when trajectory, objective or capture semantics are unresolved.

| Framework / entrypoint | Inspect in the installed implementation |
|---|---|
| [TRL](https://huggingface.co/docs/trl/grpo_trainer) | Batch preparation, action scoring, loss, optimizer steps; completion/tool/truncation masks, group normalization, detached old scores, fused loss |
| [verl](https://verl.readthedocs.io/en/latest/) | Sample/group/action mappings through repeats, filtering, balancing, packing, rank redistribution; old-policy anchors, correction weights, reward/KL placement, aggregation |
| [OpenRLHF](https://github.com/OpenRLHF/OpenRLHF) | Experience construction, worker roles, action masks, advantage estimator, actor/critic steps |
| [slime](https://github.com/THUDM/slime) | Rollout-data conversion, Megatron loss inputs, rollout/training weight versions |
| [NeMo RL](https://docs.nvidia.com/nemo/rl/latest/index.html) | Policy/generation backends, batch layout, objective reduction, policy update |
| [ms-swift](https://github.com/modelscope/ms-swift) | RLHF algorithm, template/masks, reward/group normalization, trainer backend |
| [AReaL](https://github.com/areal-project/AReaL) | Asynchronous trajectory/policy versions, action rescoring, off-policy corrections |
| [ROLL](https://github.com/alibaba/ROLL) | Role pipeline, sample dispatch, reward/advantage processing, training backend |
| [MindSpeed-RL](https://github.com/Ascend/MindSpeed-RL) | Actor/rollout/reward steps, sample redistribution, distributed parameter/gradient storage |

Record the RL framework, training backend and rollout engine separately, with the
policy/state version of each fixed trajectory. Use [training capture](sft.md#training-stages)
for distributed tensors and the [inference entrypoints](inference.md#framework-integration)
for rollout scores. For other frameworks, follow
[native adaptation](../../framework-adapt/SKILL.md#execute).

## Metrics

| Observations | Measures |
|---|---|
| Action scores | Logprob MAE, P99, maximum at paired valid actions |
| Native objective | Loss components and relative differences; reward/advantage/value inputs for localization |
| Advantages | Maximum absolute difference given equal rewards |
| Gradients, postclip gradients, updates | L2 error, RMSE, cosine, norm ratio, maximum absolute error |
| Coverage | Paired trajectories/groups/actions, effective masks, parameter/element counts, optimizer events |

Use [comparison](comparison.md) for definitions and judge with [acceptance](acceptance.md).

# SFT

## Collection

Select the requested objective, gradient or update stages and their parameter and
token coordinates. For target scores, collect labels, prediction/target positions
and full-vocabulary-normalized logprobs under the native loss mask, including
active EOS. Plain CE uses the global valid-token mean of negative logprobs; native
loss keeps its implemented reduction and extra terms.

### Full-parameter finetuning

Declare the logical parameter inventory (all trainable parameters for full-model
requests) with tied-weight mappings and trainable flags. At each stage include
absent and present-zero gradients and unchanged updates.

### LoRA

Check initial adapter values, target modules, rank, alpha/scaling, dropout,
bias/modules-to-save, dtype, quantization and merged state against the contract.
Use the requested adapter inventory and keep frozen-base identity checks. With
zero-B initialization, keep present-zero first-step A gradients/updates and the
changing B coordinates. Capture merged-weight deltas when requested.

### Training stages

| Stage | Observations |
|---|---|
| Objective | Scores, masks, native losses, coefficients and effective denominators |
| Backward | All present gradients after accumulation, unscale and synchronization; presence inventory |
| Clipping (when selected) | Native norm/coefficient and postclip gradients |
| Update | Stored before/after parameters including unchanged coordinates; applied/skipped event; optimizer state needed to recompute the update |

Record native parameter, gradient and master storage separately from exported and
offline-reduction dtypes. For distributed training, capture accumulated, unscaled,
synchronized gradients from native storage (for example Megatron `main_grad`),
postclip gradients before optimizer mutation, and stored parameters after mutation
and copy-back; map shards and replicas with the [coordinate rules](comparison.md#coordinates).

For optimizer-state or checkpoint-continuation comparisons, establish the starting
state (parameters, moments, step, scheduler, RNG, data position) from checkpoint or
trajectory evidence and compare the requested cold/restored trajectories.

## Framework integration

Inspect the installed implementation when capture or reduction semantics are unresolved.

| Framework / entrypoint | Inspect in the installed implementation |
|---|---|
| PyTorch training loop | Batch preparation, model/loss call, optimizer boundaries; native label shift, mask, accumulation, reduction |
| [Transformers Trainer](https://huggingface.co/docs/transformers/main_classes/trainer) | Data collator, `compute_loss`, `training_step`, optimizer; effective labels and accumulation denominators |
| [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) | Formatting/packing, completion or assistant masks, loss path, PEFT trainable state |
| [LLaMAFactory](https://github.com/hiyouga/LlamaFactory) | SFT workflow, resolved template, full/LoRA configuration, resulting trainer/model |
| [ms-swift](https://github.com/modelscope/ms-swift) | SFT entrypoint, training backend, template/loss settings, tuner state |
| [Axolotl](https://docs.axolotl.ai/) | Resolved configuration, preprocessing/packing, effective labels, adapter state |
| [Megatron Bridge](https://docs.nvidia.com/nemo/megatron-bridge/latest/training/peft.html) | Finetuning entrypoint, checkpoint/adapter conversion, logical tensor mapping |
| [MindSpeed-LLM](https://github.com/Ascend/MindSpeed-LLM) | Finetuning workflow, backend, checkpoint conversion, parameter/gradient storage |

Resolve the distributed backend: FSDP/ZeRO shard and optimizer ownership, offload
and synchronization; Megatron tensor/pipeline/context/data/expert layouts. For other
frameworks, follow [native adaptation](../../framework-adapt/SKILL.md#execute) to
locate effective labels, loss reduction and trainable-parameter storage.

## Metrics

| Observations | Measures |
|---|---|
| Target scores | Each side's NLL and absolute difference; logprob MAE, P99, maximum |
| Native objective | Each side's loss components and relative differences |
| Gradients, postclip gradients, updates | L2 error, RMSE, cosine, norm ratio, maximum absolute error; worst per-tensor cosine and norm ratio |
| Optimizer | Each side's update recomputed from its own gradient and state (relative error) |
| Coverage | Valid targets, paired parameters/elements, gradient presence, optimizer events; for LoRA, adapter coverage and frozen-base checks |

See the [metric definitions](comparison.md) and judge with [acceptance](acceptance.md)
(`--optimizer adaptive` for Adam-family, `sgd` otherwise).

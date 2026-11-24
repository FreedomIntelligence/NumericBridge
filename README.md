<p align="center">
  <img src="docs/images/numericbridge-logo.svg" width="88" height="88" alt="NumericBridge logo">
</p>

<h1 align="center">NumericBridge</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-009688.svg" alt="License: MIT"></a>
  <a href="https://github.com/FreedomIntelligence/NumericBridge/actions/workflows/ci.yml"><img src="https://github.com/FreedomIntelligence/NumericBridge/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status"></a>
  <a href="https://freedomintelligence.github.io/NumericBridge/"><img src="https://img.shields.io/badge/Homepage-Community-2563EB.svg" alt="Project homepage: NumericBridge community"></a>
  <a href="https://github.com/FreedomIntelligence/NumericBridge/stargazers"><img src="https://img.shields.io/github/stars/FreedomIntelligence/NumericBridge?logo=github" alt="GitHub stars"></a>
</p>

<p align="center"><strong>A framework and collaborative community for cross-stack precision alignment in inference, training, and reinforcement learning</strong></p>

<p align="center">English · <a href="README.zh-CN.md">简体中文</a></p>

<p align="center"><a href="#framework-overview">Overview</a> · <a href="#getting-started">Getting started</a> · <a href="#typical-cases">Typical cases</a> · <a href="#evaluation-and-repair-history">Results and repair history</a></p>

NumericBridge helps compare, diagnose and repair numerical differences across inference, training and RL stacks. Confirm the setup, look up community cases, then run the checks in your environment. Verify fixes on the original workload and share results with the community if you choose.

## Framework overview

![NumericBridge workflow: community lookup, native setup, comparison, diagnosis, repair, verification and delivery](docs/images/numericbridge-overview.svg)

*The Ascend 910C / Qwen3.8-27B / verl configuration in the figure is an example. See [Typical cases](#typical-cases) for measured results and verified coverage.*

## Getting started

**Install without cloning.** On [GitHub Releases](https://github.com/FreedomIntelligence/NumericBridge/releases), choose a `skills-vMAJOR.MINOR.PATCH` release and download the `numericbridge-skills-*.zip` asset and its matching `.zip.sha256` file into the same directory. Do not use GitHub's “Source code” archives. Replace the example version with the one you downloaded:

```sh
version=v1.2.3
archive="numericbridge-skills-$version.zip"
skills="numericbridge-skills-$version"
shasum -a 256 -c "$archive.sha256" && mkdir "$skills" && unzip "$archive" -d "$skills"
```

On Linux you can use `sha256sum -c "$archive.sha256"` in place of `shasum -a 256 -c "$archive.sha256"`. Point your agent client at the directory containing the five skills, or copy the five skill folders together into its skills directory. The bundle includes the shared tools and an offline snapshot of the community case library. [Updates and checkout-based installation](tools/README.md#portable-skills)

**Look up a case offline.** From the download directory, query the bundled snapshot for a PEFT case. Replace the hardware, model, library and task with your workload's details.

```sh
python -B "$skills/precision-compare/scripts/community_search.py" \
  --hardware H20Z --model Qwen3.5-9B-Base --library peft --task inference --limit 1
```

**Run a first comparison.** With PyTorch and NumPy installed, run this CPU example. It uses the same classifier, weights and batch for two backward passes, one in FP32 and one in BF16. The output gives the gradient differences and the path to both captures and `report.json`.

```sh
python -B "$skills/precision-compare/examples/compare_gradients.py"
```

For your workload, start with the [NumericBridge Skill](skills/numericbridge/SKILL.md). Give the agent your hardware, model, framework and task, along with any run commands or captures you have.

## Workflow and skills

The [workflow](skills/numericbridge/references/workflow.md) loads only the method it needs: [Framework Adapt](skills/framework-adapt/SKILL.md) for native setup, [Precision Compare](skills/precision-compare/SKILL.md) for measurement, [Precision Repair](skills/precision-repair/SKILL.md) for supported-path failures or numerical findings that warrant diagnosis, and [Operator Optimize](skills/operator-optimize/SKILL.md) for operators that diagnosis finds outside their FP64 bound, reporting each variant's precision and speed. Each stage reuses the confirmed request and evidence.

Task recipes: [Inference](skills/precision-compare/references/inference.md) · [SFT](skills/precision-compare/references/sft.md) · [RL](skills/precision-compare/references/rl.md).

## Typical cases

| Workload | What the measurements showed | Evidence |
| --- | --- | --- |
| Qwen3.5-9B-Base · LoRA | On the first training step, cosine similarity across GPU and NPU was about 0.9976 for gradients and 0.9775 for stored weight updates. | [Training measurements](reports/qwen35-precision-report.md) |
| Qwen3.8-Flash-Next · inference | With automatic placement on GPU and TP8 on NPU, the top token matched in 254 of 264 sampled full-forward rows and 2 of 32 later fixed-history rows. The runs included 32K inputs. Native NPU vLLM and SGLang failed during initialization, before scoring. | [Inference report](reports/qwen38-precision-report.md) |
| DeepSeek-V4.1-Flash-1017 · GPU/NPU comparison | The original comparison paired 191 of 275 tensor observations and all 3,168 target scores. A later, separately adapted configuration paired 275 of 275 observations. | [Original comparison](reports/deepseek41-precision-report.md) · [Follow-up report](reports/deepseek41-services-final-20261109/report.md) |
| PEFT 0.17.1 · LoRA adapter merging | SVD combination applied the adapter scale twice. After backporting the upstream fix, all 24 small CPU workloads passed, including forward, input-gradient and checkpoint checks. | [Repair record](community/site/evidence/peft-lora-svd-scaling.md) · [Patch](community/site/evidence/peft-lora-svd-scaling.patch) |

The three model comparisons report measurements for the configurations tested. Numerical acceptance thresholds were unspecified. [Browse all reports](reports/README.md)

## Evaluation and repair history

Use the [community case library](https://freedomintelligence.github.io/NumericBridge/index.html#/cases) to select your model, hardware and framework version. Open a matching case to see the observed difference, candidate fixes and the workload used to verify each tested fix.

- [History archive](https://freedomintelligence.github.io/NumericBridge/archive.html) — look up earlier evaluations and repairs by configuration.
- [Results and evidence](https://freedomintelligence.github.io/NumericBridge/paper.html) — find the figures, measured values and source records behind a report.

## Contributing

Share a comparison or repair through the [community submission MCP](community/README.md#mcp-contribution). Include the model and library revisions, run configuration, observed difference and evidence. For a fix, include before-and-after results on the original workload. Community maintainers review submissions, publish records and coordinate any upstream PRs. [Community record guide](skills/precision-compare/references/community-records.md) · [Publication guidelines](docs/publication.md)

[Community acknowledgments](https://freedomintelligence.github.io/NumericBridge/#/acknowledgments) recognizes contributors to published cases.

Before submitting, run the repository and record checks from the repository root. [Developer guide](AGENTS.md)

```sh
python -m pip install -r requirements-dev.txt
python -B -m scripts validate
python -B -m community.records validate
```

## Supported by

![Sun Yat-sen University, CUHK-Shenzhen, Shenzhen Loop Area Institute, China Merchants Lion Rock AI Lab, AutoKernel and Shenzhen Research Institute of Big Data](docs/images/numericbridge-community.png)

[Sun Yat-sen University](https://www.sysu.edu.cn/) · [The Chinese University of Hong Kong, Shenzhen](https://www.cuhk.edu.cn/en) · [Shenzhen Loop Area Institute](https://www.slai.edu.cn/en) · [China Merchants Lion Rock AI Lab](https://lionrocklab.com/) · [AutoKernel](https://autokernel.cn/) · [Shenzhen Research Institute of Big Data](https://www.sribd.cn/en)

<p align="center">
  <img src="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge/efe48ac7f3fdd18f4c721820ff8eeab342e121af/docs/images/numericbridge-logo.svg" width="88" alt="NumericBridge logo">
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

NumericBridge helps compare, diagnose and repair numerical differences across inference, training and RL stacks. Confirm the setup, optionally look up community cases, then run the checks in your environment. Verify fixes on the original workload and share results with the community if you choose.

## Framework overview

The workflow reuses the confirmed setup and evidence across native preparation, precision comparison, diagnosis, and original-workload verification.

## Getting started

**Install without cloning.** On [GitHub Releases](https://github.com/FreedomIntelligence/NumericBridge/releases/tag/skills-v0.1.1), choose a `skills-vMAJOR.MINOR.PATCH` release and download the `numericbridge-skills-*.zip` asset and its matching `.zip.sha256` file into the same directory. Do not use GitHub's “Source code” archives. Replace the example version with the one you downloaded:

```sh
version=v0.1.1
archive="numericbridge-skills-$version.zip"
skills="numericbridge-skills-$version"
shasum -a 256 -c "$archive.sha256" && mkdir "$skills" && unzip "$archive" -d "$skills"
```

On Linux you can use `sha256sum -c "$archive.sha256"` in place of `shasum -a 256 -c "$archive.sha256"`. Point your agent client at the directory containing the five skills, or copy the five skill folders together into its skills directory. New Skills ZIP builds include the shared tools, but no community records or search index; older releases may include a snapshot. [Updates and checkout-based installation](tools/README.md#portable-skills)

**Develop from source.** Use `git clone --depth 1 https://github.com/FreedomIntelligence/NumericBridge.git` for a clean current checkout without the historical catalog objects. Full Git history still contains independently published older revisions.

**Community lookup is optional.** Use the [case library](https://freedomintelligence.github.io/NumericBridge/index.html#/cases) or connect your agent to the [managed search MCP](community/README.md#mcp-search). If you have a separate snapshot, [pass it explicitly to the search tool](tools/README.md#optional-community-lookup). You can continue offline without community context; installation and the local workflow do not fetch a snapshot.

**Run a first comparison.** With PyTorch and NumPy installed, run this CPU example. It uses the same classifier, weights and batch for two backward passes, one in FP32 and one in BF16. The output gives the gradient differences and the path to both captures and `report.json`.

```sh
python -B "$skills/precision-compare/examples/compare_gradients.py"
```

For your workload, start with the [NumericBridge Skill](skills/numericbridge/SKILL.md) or copy the [request template](skills/numericbridge/references/prompt-template.md). Describe what you want to achieve, and include any existing case or run details you have.

## Workflow and skills

The [workflow](skills/numericbridge/references/workflow.md) loads only the method it needs: [Framework Adapt](skills/framework-adapt/SKILL.md) for native setup, [Precision Compare](skills/precision-compare/SKILL.md) for measurement, [Precision Repair](skills/precision-repair/SKILL.md) for supported-path failures or numerical findings that warrant diagnosis, and [Operator Optimize](skills/operator-optimize/SKILL.md) for operators that diagnosis finds outside their FP64 bound, reporting each variant's precision and speed. Each stage reuses the confirmed request and evidence.

Task recipes: [Inference](skills/precision-compare/references/inference.md) · [SFT](skills/precision-compare/references/sft.md) · [RL](skills/precision-compare/references/rl.md).

## Typical cases

Browse the [public case library](https://numericbridge.com/#/cases) and [measured reports](https://numericbridge.com/paper.html). Published records and evidence are served separately; the workflow checkout and the current Skills release do not contain them.

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
```

## Supported by

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge-site/21c6406591393233bd9928e7b28227fc0fe5925b/assets/readme-supporters-dark.png">
  <img src="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge-site/21c6406591393233bd9928e7b28227fc0fe5925b/assets/readme-supporters-light.png" alt="Sun Yat-sen University, CUHK-Shenzhen, Shenzhen Loop Area Institute, China Merchants Lion Rock AI Lab, AutoKernel and Shenzhen Research Institute of Big Data">
</picture>

[Sun Yat-sen University](https://www.sysu.edu.cn/) · [The Chinese University of Hong Kong, Shenzhen](https://www.cuhk.edu.cn/en) · [Shenzhen Loop Area Institute](https://www.slai.edu.cn/en) · [China Merchants Lion Rock AI Lab](https://lionrocklab.com/) · [AutoKernel](https://autokernel.cn/) · [Shenzhen Research Institute of Big Data](https://www.sribd.cn/en)

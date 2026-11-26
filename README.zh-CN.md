<p align="center">
  <img src="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge/efe48ac7f3fdd18f4c721820ff8eeab342e121af/docs/images/numericbridge-logo.svg" width="88" alt="NumericBridge 标志">
</p>

<h1 align="center">NumericBridge</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-009688.svg" alt="License: MIT"></a>
  <a href="https://github.com/FreedomIntelligence/NumericBridge/actions/workflows/ci.yml"><img src="https://github.com/FreedomIntelligence/NumericBridge/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI 状态"></a>
  <a href="https://freedomintelligence.github.io/NumericBridge/"><img src="https://img.shields.io/badge/%E9%A1%B9%E7%9B%AE%E4%B8%BB%E9%A1%B5-%E7%A4%BE%E5%8C%BA-2563EB.svg" alt="项目主页：NumericBridge 社区"></a>
  <a href="https://github.com/FreedomIntelligence/NumericBridge/stargazers"><img src="https://img.shields.io/github/stars/FreedomIntelligence/NumericBridge?logo=github" alt="GitHub 星标数"></a>
</p>

<p align="center"><strong>面向推理、训练与强化学习的跨栈精度对齐框架与协作社区</strong></p>

<p align="center"><a href="README.md">English</a> · 简体中文</p>

<p align="center"><a href="#框架概览">框架概览</a> · <a href="#快速开始">快速开始</a> · <a href="#典型案例">典型案例</a> · <a href="#评测与修复历史">评测与修复历史</a></p>

NumericBridge 用于对比、诊断和修复推理、训练与强化学习（RL）中的数值差异。确认配置后，可选择查阅社区案例，再在自己的环境中运行检查。修复用原负载复验，结果可选择分享给社区。

## 框架概览

![以目标配置为起点，依次进行数值对比、问题诊断、修复优化和回归测试。工作流参考社区知识库，交付适配实现、运行配置和验证报告。](https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge-site/a107784e5271dd54d7e4a8d2e7a2bec7e675855a/assets/overview.zh-CN.drawio.svg)

工作流在原生环境准备、精度对比、问题诊断和原任务复验之间复用已确认的配置与证据。

## 快速开始

**无需克隆即可安装。** 在 [GitHub Releases](https://github.com/FreedomIntelligence/NumericBridge/releases/tag/skills-v0.1.1) 中选择一个 `skills-vMAJOR.MINOR.PATCH` 版本，将其附件中的 `numericbridge-skills-*.zip` 和对应的 `.zip.sha256` 校验文件下载到同一目录。不要下载 GitHub 自动生成的 “Source code” 压缩包。把下方的示例版本换成你下载的版本：

```sh
version=v0.1.1
archive="numericbridge-skills-$version.zip"
skills="numericbridge-skills-$version"
shasum -a 256 -c "$archive.sha256" && mkdir "$skills" && unzip "$archive" -d "$skills"
```

在 Linux 上也可以用 `sha256sum -c "$archive.sha256"` 代替 `shasum -a 256 -c "$archive.sha256"`。让 Agent 客户端从解压后的目录加载五个 Skill，或将五个 Skill 文件夹一起复制到客户端的 Skill 目录。新版 Skills ZIP 包含共享工具，但不附带社区记录或检索索引；旧版可能包含快照。[更新与从仓库安装](tools/README.md#portable-skills)

**需要源码时**，用 `git clone --depth 1 https://github.com/FreedomIntelligence/NumericBridge.git` 获取干净的当前版本；完整 Git 历史仍保留以前公开发布过的记录。

**社区检索是可选步骤。** 可以浏览[社区案例库](https://freedomintelligence.github.io/NumericBridge/index.html#/cases)，或让 Agent 连接[托管的检索 MCP](community/README.md#mcp-search)。如果另有检索快照，可以[显式传给检索工具](tools/README.md#optional-community-lookup)。离线时可以跳过社区检索；安装和本地工作流都不会自行下载快照。

**跑一次梯度比较。** 安装 PyTorch 和 NumPy 后，运行下面的 CPU 示例。它使用相同的分类器、初始权重和同一批输入，分别用 FP32 和 BF16 做反向传播。输出会列出梯度差异，并给出两份采集结果及 `report.json` 的保存路径。

```sh
python -B "$skills/precision-compare/examples/compare_gradients.py"
```

自己的任务从 [NumericBridge Skill](skills/numericbridge/SKILL.md) 开始，也可以复制[通用任务模板](skills/numericbridge/references/prompt-template.md)。讲清楚你想解决什么，有已有案例或运行材料就一并给出，不用自己写实验步骤。

## 工作流与 Skill

[工作流](skills/numericbridge/references/workflow.md)按需加载各阶段的 Skill：[Framework Adapt](skills/framework-adapt/SKILL.md) 准备原生环境，[Precision Compare](skills/precision-compare/SKILL.md) 采集和对比，支持的路径运行失败或发现值得调查的数值差异时，进入 [Precision Repair](skills/precision-repair/SKILL.md)；诊断发现算子超出 FP64 误差范围时，进入 [Operator Optimize](skills/operator-optimize/SKILL.md)，给出每个方案的精度和速度。每一步沿用已确认的需求和证据。

任务配方：[推理](skills/precision-compare/references/inference.md) · [SFT](skills/precision-compare/references/sft.md) · [RL](skills/precision-compare/references/rl.md)。

## 典型案例

实测案例见[社区案例库](https://numericbridge.com/#/cases)及[结果与证据](https://numericbridge.com/paper.html)。公开记录和证据由官网独立提供，不随工作流检出或当前 Skills 发行包分发。

## 评测与修复历史

在[社区案例库](https://freedomintelligence.github.io/NumericBridge/index.html#/cases)中选择模型、硬件和框架版本。打开匹配的案例，可以查看观测到的差异、候选修复，以及每个已测试修复所用的验证负载。

- [历史归档](https://freedomintelligence.github.io/NumericBridge/archive.html)可按配置查找既有评测和修复记录。
- [结果与证据](https://freedomintelligence.github.io/NumericBridge/paper.html)可查阅报告中的图表、实测数值及其来源。

## 参与贡献

通过[社区投稿 MCP](community/README.md#typed-contribution-input) 的 `prepare_contribution` 分享实测结果、已验证的问题或补丁候选。附上可复现的范围、结果和证据；原始验收和补丁局部回归分开记录。完整补丁和上游 PR 草稿仅交给维护者评审，由维护者决定发布哪些内容、是否向上游开 PR。[社区记录指南](skills/precision-compare/references/community-records.md) · [发布规则](docs/publication.md)

[社区鸣谢](https://freedomintelligence.github.io/NumericBridge/#/acknowledgments)记录已收录案例的贡献。

提交前，在仓库根目录运行仓库和社区记录检查。[开发指南](AGENTS.md)

```sh
python -m pip install -r requirements-dev.txt
python -B -m scripts validate
```

## 支持单位

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge-site/21c6406591393233bd9928e7b28227fc0fe5925b/assets/readme-supporters-dark.png">
  <img src="https://raw.githubusercontent.com/FreedomIntelligence/NumericBridge-site/21c6406591393233bd9928e7b28227fc0fe5925b/assets/readme-supporters-light.png" alt="中山大学、香港中文大学（深圳）、深圳河套学院、招商局狮子山人工智能实验室、智子芯元（深圳）科技有限责任公司和深圳市大数据研究院">
</picture>

[中山大学](https://www.sysu.edu.cn/) · [香港中文大学（深圳）](https://www.cuhk.edu.cn/) · [深圳河套学院](https://www.slai.edu.cn/) · [招商局狮子山人工智能实验室](https://lionrocklab.com/) · [智子芯元（深圳）科技有限责任公司](https://autokernel.cn/) · [深圳市大数据研究院](https://www.sribd.cn/)

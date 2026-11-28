# Shared Skill tools

Command execution, resource identities and portable evidence shared by the
skills and the optional research runner. Numerical comparators, acceptance,
collection adapters and comparison-specific replay live in
[precision-compare](../skills/precision-compare/SKILL.md); diagnosis tools live in
[precision-repair](../skills/precision-repair/SKILL.md). Import on demand:

```python
from tools.record_run import record_run, summarize_run, classify_failure
from tools.workflow import run_workflow, inspect_workflow, init_plan
from tools.resource_catalog import find, register
from tools.runtime_probe import probe_runtime
from tools.evidence_bundle import fetch, pack, verify
from tools.community_search import load_index, search
```

Contracts live in the module docstrings; modules use the standard library. From a
checkout, run `python -B -m tools.workflow --help` (or `record_run`). The Compare
script paths are compatibility entrypoints for the same implementation; RSI
imports this package directly.

## Portable skills

Use the [download and verification steps](../README.md#getting-started) to install
without a checkout. For updates, verify and extract the new version into a fresh
directory, then switch the client to it or replace all five installed skill
folders together. Keep the previous directory if you need to roll back.

For an unreleased checkout, export from the repository root:

```sh
python -B -m scripts install --output /path/to/fresh/skills
```

Checkout installs and new ZIP builds bundle this package once inside Compare's
`scripts/tools/`, without community record data or a search index. Once installed, the skills do not
require a checkout, the RSI package or `PYTHONPATH`. Installation and local
workflow execution do not fetch the community index.

### Test a catalog index locally

The workflow uses only the [official search MCP](../community/README.md#mcp-search).
For maintenance and local index tests, pass a snapshot path and optionally its
expected SHA-256 digest:

```sh
python -B /path/to/skills/precision-compare/scripts/community_search.py \
  --index /path/to/search-index.json --sha256 "$snapshot_sha256" \
  --hardware H20Z --model Qwen3.5-9B-Base --library peft --task inference --limit 1
```

From a checkout, use `python -B -m tools.community_search` with the same flags.
The CLI requires an explicit index. It is not a fallback for the workflow.

### Build a workflow release

From a clean checkout with development requirements and CPU Torch installed:

```sh
python -B -m scripts validate
python -B -m scripts.workflow_release --version v1.2.3 --output /path/to/fresh/dist
```

The builder reuses the standalone exporter and tests a fresh extraction before
writing `numericbridge-workflow-v1.2.3.zip` and its `.zip.sha256` file. Tag the
reviewed source commit as `workflow-v1.2.3`. Create a GitHub Release draft for
that existing tag, upload both assets, and verify the checksum on a fresh
GitHub download before publishing. Use the title `NumericBridge Workflow v1.2.3`.
Do not overwrite existing releases or assets. The original `skills-v*` releases
retain their tags, downloads and [legacy release workflow](../.github/workflows/skills-release.yml).

The workflow engine fingerprints its own implementation files, so engine changes
invalidate earlier reuse identities. Historical runs stay untouched; affected
validations need a new run rather than rewritten receipts.

## Which tools belong here?

Tools used by several skills or the research runner. Comparison arithmetic and
collectors stay in `skills/precision-compare/scripts/`; diagnosis tools stay in
`skills/precision-repair/scripts/`. Maintainer commands use `python -m scripts`.

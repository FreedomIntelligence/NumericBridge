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

`community_search` is a read-only client for the pinned community index shared by
Compare, Adapt and Repair. It returns matching records and version alternatives
without starting a model; query the
[community records interface](../community/records/README.md) by a returned ID
for original metrics.

## Portable skills

Use the [download and verification steps](../README.md#getting-started) to install
without a checkout. For updates, verify and extract the new version into a fresh
directory, then switch the client to it or replace all five installed skill
folders together. Keep the previous directory if you need to roll back.

For an unreleased checkout, export from the repository root:

```sh
python -B -m scripts install --output /path/to/fresh/skills
```

Both methods bundle this package once inside Compare's `scripts/tools/`, with a
pinned community search snapshot for offline retrieval. Once installed, the
skills do not require a checkout, the RSI package or `PYTHONPATH`. To select a
different reviewed snapshot, use the search tool's `--index` and `--sha256`.

### Build a skills release

From a clean checkout with development requirements and CPU Torch installed:

```sh
python -B -m scripts.skills_release --version v1.2.3 --output /path/to/fresh/dist
```

The builder reuses the standalone exporter and tests a fresh extraction before
writing the ZIP and its SHA-256 file. To publish, use the
[Publish skills release workflow](../.github/workflows/skills-release.yml) on an
existing `skills-v1.2.3` tag, after the workflow is on the default branch. The
workflow validates and then creates a new GitHub Release; it does not overwrite
existing release assets.

The workflow engine fingerprints its own implementation files, so engine changes
invalidate earlier reuse identities. Historical runs stay untouched; affected
validations need a new run rather than rewritten receipts.

## Which tools belong here?

Tools used by several skills or the research runner. Comparison arithmetic and
collectors stay in `skills/precision-compare/scripts/`; diagnosis tools stay in
`skills/precision-repair/scripts/`. Maintainer commands use `python -m scripts`.

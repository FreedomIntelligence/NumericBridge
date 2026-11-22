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

```sh
python -B -m scripts install --output /path/to/client/skills
```

The exporter copies the skills and bundles this package once inside Compare's
scripts directory, with a dated search snapshot for offline retrieval; no checkout,
RSI package or `PYTHONPATH` is needed. Existing destinations are rejected, so
export to a fresh directory and replace the installed version after inspection.
Select another reviewed snapshot with the search tool's `--index` and `--sha256`.

The workflow engine fingerprints its own implementation files, so engine changes
invalidate earlier reuse identities. Historical runs stay untouched; affected
validations need a new run rather than rewritten receipts.

## Which tools belong here?

Tools used by several skills or the research runner. Comparison arithmetic and
collectors stay in `skills/precision-compare/scripts/`; diagnosis tools stay in
`skills/precision-repair/scripts/`. Maintainer commands use `python -m scripts`.

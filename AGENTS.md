# Repository maintenance

## Layout

- `skills/numericbridge/` is the entrypoint for the full workflow. It owns stage transitions and handoffs and links to the installable `framework-adapt`, `precision-compare`, `precision-repair`, `operator-optimize` and `operator-verify` skills for execution methods. Load those methods on demand. Export the suite with `python -B -m scripts install --output NEW_DIRECTORY` to bundle the shared `tools/` once in Compare. Reuse workload execution, verified resources, inputs/state, collectors and results. `skills/operator-optimize/` owns operator optimization after diagnosis: configuration, backend and kernel variants measured against FP64 with paired timing (`scripts/kernel_check.py`). `skills/operator-verify/` owns offline Lean 4 formal verification (`scripts/triage.py`, `scripts/lean_check.py`, `lean_lib/AscendLean/`) for discrete operator invariants.

- `research/` is retained local RSI research, Git-ignored and excluded from both the public repository and normal Skill installation. Preserve its local code, state and recorded experiments. It owns task/iteration orchestration over existing workflow tools and an external harness. Index successful/incomplete task evidence; keep full traces and datasets private. The independent improver builds candidates. Evaluate exact tool/context/procedure bundles on independent task families before activation; label dataset outcomes at task scope. Pin task versions, preserve raw usage and reuse comparable baseline receipts. Native checks remain task-owned. Agent-weight training and operator search are extension points.

- `skills/framework-adapt/` owns native framework deployment and model/backend adaptation. Keep the entrypoint concise; retain environment declarations and framework-specific implementations with their projects and cases. Reuse comparison tools when numerical validation is needed.
- `skills/precision-repair/` is the installable repair skill. Its entrypoint owns correctness evidence, causal diagnosis, patching and original-workload verification. Its `scripts/` hold the standalone diagnosis tools (triage, localization, cross replay and `diagnosis.json` state); they read comparator reports and arrays and do not import Compare scripts. Keep framework-specific fixes and experiments with their cases.
- `skills/precision-compare/` is the installable comparison skill. Keep routing and common collection steps in `SKILL.md`, the three task recipes and `comparison.md` in `references/`, and artifact interfaces in script docstrings. Optional internal sampling and its msProbe capability summary live in `references/layerwise.md`. Task recipes own capture requirements, framework integration and metric selection. Define shared training stages in SFT, distribution metrics in inference, common coordinate/metric/tool rules in `comparison.md`, and acceptance limits in `acceptance.md` with `scripts/acceptance.py`; change default limits only as a new profile version.
- Maintain matching English `README.md` and Chinese `README.zh-CN.md`. Technical documentation and maintenance instructions use English. Follow the Agent Skills specification.
- `skills/precision-compare/examples/` contains concise runnable artifact examples. Extract task-independent operations into composable tools with explicit interfaces and meaningful regressions; keep task-specific settings in adapters and environments.
- `tests/` contains retained local developer regressions, is Git-ignored, and is not distributed in the public repository or skill export. Preserve these local files. Public CI uses the supported validation and smoke-install commands. Artifact tools require NumPy; the target-score comparator uses the standard library.
- Private `projects/<project>/environments/` owns reusable environment declarations. Cases own configuration, collectors, tests and execution artifacts. Keep case-specific code there; extract shared code when reused.
- Keep current guides focused on task semantics and tool usage. Work logs and `runs/` own dated decisions, one-off execution failures and results; resolve tool defects in code and regressions.

`community/records/` owns public schema and relation validation. `scripts/` owns reusable collection, integration, export and installation tools. `.github/` contains GitHub configuration; `docs/` contains current guides. Follow [publication.md](docs/publication.md) for server-owned records and evidence. Keep interim artifacts and private cases out of Git. Do not add research dependencies to community/shared tools or restore bare script imports via sys.path.

`community/site/` contains only the frontend pin and operator guide. Put approved catalog objects and evidence in a reviewed server candidate, never Git. Validate with `python -B -m community.records validate --site "$SITE"` and `python -B -m scripts export-search --check --site "$SITE"`. Publish via the [server catalog tool](docs/community-storage.md), which verifies the prior generation and refuses historical edits or removals. Preserve IDs, bytes, measurements and exact provenance.

The RSI dashboard frontend is separate. Generate reviewed RSI candidates only to explicit staging: `python -B -m scripts rsi-snapshot --monitor "$MONITOR" --output "$STAGING/rsi.json" --refresh`. The active catalog policy deliberately freezes existing RSI snapshot bytes until reviewed update rules are defined. Keep full traces, resource connection details and unreviewed snapshots private.

## Changes

Use the configured, authorized human identity for commits. Do not add AI tools
or assistants as commit authors or co-authors, or list them as contributors in
project materials. Check authorship and co-author trailers before pushing.

Confirm the approach before editing. For a bug fix, reproduce the behavior temporarily, add a meaningful regression and remove the temporary reproducer. Preserve permanent tests.

Create new runs exclusively as `<side>-<task>-<UTC YYYYmmddTHHMMSSZ>`, using the task's side ID or `compare`. Keep model/data/environment resources in place and preserve historical run bytes and paths. Use a new run for later validation. For undated migrations, use `<side>-<task>-migrated-<UTC migration timestamp>` and retain the path mapping.

For skill/no-skill benchmark iterations, reuse a verified no-skill baseline while the task and model/runtime conditions remain comparable; rerun only the skill arm. Keep baseline artifacts outside evaluator inputs, record the reused baseline identity, and retain raw usage for consistent repricing.

## Validation

Install maintenance dependencies with `python -m pip install -r requirements-dev.txt`. Run `python -B -m scripts validate` from the repository root. For catalog work, also run the explicit `--site` validators above. Preserve retained local tests; they are not distributed. With Torch installed, run `python -B -m scripts smoke-install` to verify an isolated skill installation and the CPU comparison/repair examples.

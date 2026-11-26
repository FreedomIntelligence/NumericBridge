"""Typed contributor input, compiled to public records and separate review attachments."""

from datetime import date
from hashlib import sha256
import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .records.submission import MAX_EVIDENCE_FILES, encode


Slug = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$', max_length=60)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[^\x00-\x1f\x7f]+$', max_length=120)]
SHA = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{40}$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Url = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
SettingName = Annotated[str, StringConstraints(pattern=r'^[a-zA-Z][a-zA-Z0-9_ .-]{0,63}$')]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Number = Annotated[float, Field(allow_inf_nan=False)] | int
Task = Literal['inference', 'sft', 'rl']


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class PublicReference(Record):
    label: Short
    url: Url

    @model_validator(mode='after')
    def public_url(self):
        parts = urlsplit(self.url)
        host = parts.hostname or ''
        if (parts.scheme != 'https' or '.' not in host or parts.username is not None
                or parts.query or host.endswith(('.local', '.internal', '.localhost'))
                or any(c.isspace() or ord(c) < 32 for c in self.url)):
            raise ValueError('evidence requires a public HTTPS URL without credentials or query parameters')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('public evidence must use a public hostname, not an IP literal')
        return self


class Library(Record):
    name: Short
    version: Short
    commit: SHA | None = None


class Setup(Record):
    hardware: Short
    libraries: Annotated[list[Library], Field(min_length=1, max_length=12)]
    settings: Annotated[dict[SettingName, Short | bool | Number], Field(max_length=24)] = Field(default_factory=dict)


class Side(Setup):
    id: Slug


class Model(Record):
    name: Short
    revision: Short


class Operator(Record):
    name: Short
    shape: Annotated[list[Annotated[int, Field(gt=0)]], Field(min_length=1, max_length=8)]
    dtype: Short


class ModelSubject(Model):
    kind: Literal['model']


class OperatorSubject(Operator):
    kind: Literal['operator']


class Coverage(Record):
    observed: Annotated[int, Field(ge=0)]
    expected: Annotated[int, Field(gt=0)]
    unit: Short

    @model_validator(mode='after')
    def check_observed(self):
        if self.observed > self.expected:
            raise ValueError('observed coverage exceeds expected coverage')
        return self


class Metric(Record):
    id: Slug
    value: Number
    unit: Short


class Measurement(Record):
    id: Slug
    before: Number
    after: Number
    unit: Short


class Acceptance(Record):
    verdict: Literal['pass', 'gap', 'fail', 'noise_limited', 'incomplete']
    policy: Short
    criterion: Text


class Evaluation(Record):
    kind: Literal['evaluation']
    id: Slug
    title: Title
    task: Task
    model: Model
    workload: Text
    sides: Annotated[list[Side], Field(min_length=2, max_length=2)]
    acceptance: Acceptance
    run_status: Literal['completed', 'partial'] = 'completed'
    metrics: Annotated[list[Metric], Field(min_length=1, max_length=40)]
    coverage: Coverage
    limitations: list[Text] = Field(default_factory=list, max_length=8)
    reproducer: Text | None = None
    references: list[PublicReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_evaluation(self):
        _unique(self.sides, 'side')
        _unique(self.metrics, 'metric')
        _proof(self.reproducer, self.references)
        return self


class Issue(Record):
    kind: Literal['issue']
    id: Slug
    title: Title
    task: Task
    subject: Annotated[ModelSubject | OperatorSubject, Field(discriminator='kind')]
    setup: Setup
    classification: Literal['confirmed-defect', 'numerical-discrepancy', 'unconfirmed-hypothesis']
    trigger: Text
    expected: Text
    observed: Text
    reproducer: Text | None = None
    references: list[PublicReference] = Field(default_factory=list, max_length=8)
    limitations: list[Text] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_evidence(self):
        _proof(self.reproducer, self.references)
        return self


class PatchTarget(Record):
    repository: Short
    base_branch: Short
    commit: SHA
    template_path: Short | None = None


class BaselineFailure(Record):
    verdict: Literal['fail']
    summary: Text


class PatchedPass(Record):
    verdict: Literal['pass']
    summary: Text


class FocusedVerification(Record):
    scope: Text
    tested_commit: SHA
    tested_patch_sha256: Digest
    execution: Literal['native', 'offline']
    command: Text
    baseline: BaselineFailure
    patched: PatchedPass
    coverage: Coverage
    metrics: Annotated[list[Measurement], Field(min_length=1, max_length=40,
        description='Pair the metric that describes the problem under the same workload, configuration and definition.')]
    references: list[PublicReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_pair(self):
        if self.baseline.summary == self.patched.summary or self.coverage.observed == 0:
            raise ValueError('focused verification needs a measured baseline failure and a distinct patched pass')
        _unique(self.metrics, 'metric')
        return self


class TemplateSlot(Record):
    # Kinds are filled from verified structured fields; note is only extra prose.
    sections: Annotated[list[Literal['problem', 'change', 'validation', 'patch']], Field(max_length=4)] = Field(default_factory=list)
    note: Text | None = None


class PatchCandidate(Record):
    kind: Literal['patch_candidate']
    id: Slug
    title: Title
    issue_id: Slug
    change: Text
    target: PatchTarget
    patch: Annotated[str, StringConstraints(min_length=1, max_length=512000)]
    verification: FocusedVerification
    original_relation: Literal['independent', 'not_established']
    template_slots: list[TemplateSlot] | None = Field(default=None, max_length=40)
    limitations: list[Text] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_patch(self):
        if not self.patch.startswith('diff --git ') or not self.patch.endswith('\n') or '\x00' in self.patch:
            raise ValueError('patch must be a Git unified diff, preserving the final newline')
        return self


Item = Annotated[Evaluation | Issue | PatchCandidate, Field(discriminator='kind')]


class Contribution(Record):
    schema_version: Literal[1]
    id: Slug
    title: Title
    measured_on: Annotated[str, StringConstraints(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    items: Annotated[list[Item], Field(min_length=1, max_length=12)]

    @model_validator(mode='after')
    def check_items(self):
        if type(self.schema_version) is not int:
            raise ValueError('schema_version must be integer 1')
        date.fromisoformat(self.measured_on)
        _unique(self.items, 'item')
        issues = {item.id for item in self.items if isinstance(item, Issue)}
        patches = [item for item in self.items if isinstance(item, PatchCandidate)]
        if len(self.items) + len(patches) > MAX_EVIDENCE_FILES:
            raise ValueError(f'public evidence and private attachments exceed {MAX_EVIDENCE_FILES} objects')
        tasks = {item.task for item in self.items if not isinstance(item, PatchCandidate)}
        if len(tasks) != 1 or any(item.issue_id not in issues for item in patches):
            raise ValueError('one task per contribution; each patch must reference an issue in this contribution')
        return self


def _unique(rows, label):
    if len({row.id for row in rows}) != len(rows):
        raise ValueError(f'duplicate {label} ID')


def _proof(reproducer, references):
    if not reproducer and not references:
        raise ValueError('provide a minimal public reproducer or at least one public evidence URL')


def _references(rows):
    return [row.model_dump() for row in rows]


def _setup(row):
    result = row.model_dump(exclude_none=True)
    result['frameworks'] = [item.name for item in row.libraries]
    return result


def _pr_attachment(candidate, issue, credit, trusted_repos):
    from tools.strict_pr import inspect_template, render_pr

    target = candidate.target
    version = dict(candidate_id=candidate.id, target_commit=target.commit,
                   patch_sha256=sha256(candidate.patch.encode('utf-8')).hexdigest(),
                   tested_commit=candidate.verification.tested_commit,
                   tested_patch_sha256=candidate.verification.tested_patch_sha256)
    args = dict(repository=target.repository, target_commit=target.commit, template_path=target.template_path,
                trusted_repos=trusted_repos)
    template = inspect_template(**args, base_branch=target.base_branch)
    v = candidate.verification
    exact = (v.tested_commit, v.tested_patch_sha256) == (target.commit, version['patch_sha256'])
    checks = {}
    if exact and v.execution == 'native' and v.coverage.observed == v.coverage.expected:
        checks['native_on_target'] = dict(status='passed', evidence=(
            f'Focused native red/green comparison at {v.tested_commit}; '\
            f'patch SHA-256 {v.tested_patch_sha256}; {v.coverage.observed}/{v.coverage.expected} covered.'))
    proof = issue.reproducer or '\n'.join(f'- [{ref.label}]({ref.url})' for ref in issue.references)
    fence = '`' * (max([2, *(len(x) for x in re.findall(r'`+', v.command))]) + 1)
    metrics = '\n\nPaired metrics (before → after):\n\n' + '\n'.join(
        f'- {metric.id}: {metric.before} → {metric.after} ({metric.unit})' for metric in v.metrics)
    blocks = dict(problem=f'{issue.trigger}\n\nExpected: {issue.expected}\n\nObserved: {issue.observed}',
                  change=candidate.change,
                  validation=(f'{v.scope}\n\nReproduction:\n\n{proof}\n\nCommand:\n\n{fence}text\n'
                              f'{v.command}\n{fence}\n\nBefore (failed): {v.baseline.summary}'
                              f'\n\nAfter (passed): {v.patched.summary}\n\nCoverage: {v.coverage.observed}/'
                              f'{v.coverage.expected} {v.coverage.unit}.\n\nTested base: `{v.tested_commit}`; '
                              f'tested patch SHA-256: `{v.tested_patch_sha256}`.{metrics}'),
                  patch=f'Attached unified diff (SHA-256 `{version["patch_sha256"]}`).')
    contents = None
    if template['format_status'] == 'verified':
        rows = candidate.template_slots
        if rows is None and template['template_proof']['source'] == 'numericbridge_default':
            rows = [TemplateSlot(sections=[key]) for key in blocks]
        if rows is None or len(rows) != len(template['slots']):
            raise ValueError(f'{candidate.id}: supply template_slots in the inspected official order')
        placed = [key for row in rows for key in row.sections]
        if len(placed) != len(blocks) or set(placed) != set(blocks):
            raise ValueError(f'{candidate.id}: place problem/change/validation/patch exactly once in template_slots')
        contents = ['\n\n'.join([*(blocks[key] for key in row.sections), *([row.note] if row.note else [])]) for row in rows]
    elif candidate.template_slots is not None:
        raise ValueError(f'{candidate.id}: unsupported template requires manual review, not custom slots')
    pr = render_pr(**args, base_branch=target.base_branch, title=candidate.title, section_contents=contents,
                   checks=checks, attribution=credit)
    return dict(upstream_pr_draft=pr, candidate_version=version, patch_text=candidate.patch)


def compile_contribution(packet, credit, *, trusted_repos=None):
    """Turn one typed packet into existing catalog additions and v5 review evidence."""
    if not isinstance(packet, Contribution):
        packet = Contribution.model_validate(packet)
    trusted_repos = trusted_repos or {}
    evaluations, entities, links, public, review = [], [], [], {}, {}
    case_id = f'contribution-{packet.id}'
    issues = {item.id: item for item in packet.items if isinstance(item, Issue)}
    case_refs = []

    def evidence(item, document):
        path = f'evidence/submissions/{packet.id}-{item.id}.json'
        public[path] = document
        ref = dict(file=path, sha256=sha256(encode(document)).hexdigest())
        case_refs.append(ref)
        return ref

    def link(name, left, right, scope, ref):
        links.append(dict(id=f'{left[1]}-{name}-{right[1]}', relation=name,
                          **{'from': dict(kind=left[0], id=left[1]), 'to': dict(kind=right[0], id=right[1])},
                          scope=scope, evidence=[ref]))

    for item in packet.items:
        identity = f'{packet.id}-{item.id}'
        if isinstance(item, Evaluation):
            measured = dict(**({'reproducer': item.reproducer} if item.reproducer else {}),
                            references=_references(item.references))
            ref = evidence(item, measured)
            comparison = dict(id=identity, title=item.title, scope=item.workload,
                              side_ids=[side.id for side in item.sides],
                              metrics=[row.model_dump() | {'label':row.id} for row in item.metrics],
                              coverage=[item.coverage.model_dump() | {'label': 'Measured workload'}])
            evaluations.append(dict(id=identity, title=item.title, date=packet.measured_on, task=item.task,
                                    status=item.run_status,
                                    acceptance=item.acceptance.model_dump(), model=item.model.model_dump(),
                                    summary=item.workload, sides=[_setup(side) | {'label':side.id} for side in item.sides],
                                    common=[], differences=[], comparisons=[comparison], notes=item.limitations,
                                    evidence=[dict(label='Minimal verification', url=ref['file'])], archive=None))
            link('includes', ('case',case_id), ('comparison',identity), item.workload, ref)
        elif isinstance(item, Issue):
            report = item.model_dump(exclude={'kind','id','title'}, exclude_none=True)
            ref = evidence(item, report)
            details = dict(title=item.title, sources=[ref | {'pointer': '/observed'}],
                           classification=item.classification, limitations=item.limitations)
            entities.append(dict(id=identity, kind='issue', scope=item.trigger, evidence=[ref], details=details))
            link('includes', ('case',case_id), ('issue',identity), item.trigger, ref)
        else:
            issue = issues[item.issue_id]
            v = item.verification
            attachment = _pr_attachment(item, issue, credit, trusted_repos)
            review[f'review/{packet.id}-{item.id}.json'] = attachment
            report = dict(change=item.change, original_relation=item.original_relation, scope=v.scope,
                          verification=dict(tested_commit=v.tested_commit, tested_patch_sha256=v.tested_patch_sha256,
                                            execution=v.execution, command=v.command, baseline=v.baseline.model_dump(),
                                            patched=v.patched.model_dump(), coverage=v.coverage.model_dump(),
                                            metrics=[metric.model_dump() for metric in v.metrics],
                                            references=_references(v.references)),
                          patch_sha256=attachment['candidate_version']['patch_sha256'], limitations=item.limitations)
            ref = evidence(item, report)
            details = dict(title=item.title, sources=[ref | {'pointer':'/verification'}], solution_type='patch',
                           target=dict(library=item.target.repository, version=item.target.base_branch, commit=item.target.commit),
                           applicability=v.scope,
                           evidence_level='runtime-tested-in-scope' if v.execution == 'native' else 'source-inspected',
                           decision='not-recorded', limitations=item.limitations)
            entities.append(dict(id=identity, kind='candidate', scope=v.scope, evidence=[ref], details=details))
            link('includes', ('case',case_id), ('candidate',identity), v.scope, ref)
            link('addresses', ('candidate',identity), ('issue',f'{packet.id}-{issue.id}'), v.scope, ref)
    primary = next(item for item in packet.items if not isinstance(item, PatchCandidate))
    model = primary.model.name if isinstance(primary, Evaluation) else (
        primary.subject.name if isinstance(primary.subject, ModelSubject) else None)
    setup = primary.sides[0] if isinstance(primary, Evaluation) else primary.setup
    entities.append(dict(id=case_id, kind='case', scope=packet.title, evidence=case_refs,
                         details=dict(title=packet.title, sources=[], category='repair-investigation' if issues else 'precision-evaluation',
                                      request=dict(hardware=setup.hardware, model=model, framework=setup.libraries[0].name,
                                                   task=primary.task, goal=packet.title))))
    return dict(evaluations=dict(evaluations=evaluations), repairs={},
                relations=dict(entities=entities, links=links), evidence=public), review

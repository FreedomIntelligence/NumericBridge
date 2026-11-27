"""Local-only, explicit-grant minimal evidence exchange for inference.

Run ``python -B -m tools.minimal_exchange --help`` from the checkout or from
an installed precision-compare/scripts directory. See GRANT below for the
reviewed authorization object. No command connects to another host.

On each source host run inspect-source with its local contract and manifest.
Use the resulting commitments to construct a GRANT through a trusted approval
channel. Independently provide the digest of that approved document to each
operator. Then use inventory -> query -> compare (distributions), or targets ->
compare (target-scores). Move only explicitly authorized wire files to the
named recipient. Source peers receive inventories only; the reviewer receives
its own addressed inventory copies plus queries or target scores. Compare
locally at the reviewer. Neither native contract nor manifest, rank
logs, filenames, private coordinates, labels or per-source full arrays are wire
objects. Distribution sources use the native inference-capture FP32/FP64
``.npy`` score descriptors (NumPy is required on the source only); target sources
use native finite ``target-scores`` manifests. Read source-specific exceptions
only on the local host.

GRANT = {
  "schema_version": 1, "kind": "minimal-inference-grant",
  "nonce": "<64 lowercase hex, fresh random value>",
  "modality": "distributions" | "target-scores",
  "contract_sha256": "<hash from inspect-source>",
  "row_count": <positive integer from inspect-source>,
  "policy": <policy from inspect-source>,
  "sources": {"0": {"source_sha256": "...", "selection_sha256": "..."},
              "1": {"source_sha256": "...", "selection_sha256": "..."}},
  "transfers": [{"from": 0, "to": 1, "kinds": ["inventory"]},
                {"from": 1, "to": 0, "kinds": ["inventory"]},
                {"from": 0, "to": "reviewer", "kinds": ["inventory", "query"]},
                {"from": 1, "to": "reviewer", "kinds": ["inventory", "query"]}]
}
For target-scores authorize ["targets"] only to the reviewer. Transfers may be
partial; each command requires the corresponding explicit directions and kinds.
``inventory --to reviewer`` creates the reviewer copy; omit ``--to`` for peer.
Reviewer must be bound to an independently approved recipient outside this
local tool. It has no addresses or transfer implementation. Numeric side
aliases are assigned by lexically sorting native contract side IDs. Rows are
numbered starting at zero in lexically sorted native coordinate order. Target
contracts allow one token per sample/position per group. For alternative
preselected tokens, use a separate reviewed target contract and grant, with
no repeated (sample, position, token) across groups; check the exact coverage
of each group. Review cumulative disclosure across groups; never use multiple
grants to reconstruct a vocabulary. Treat GRANT and inspect-source outputs
as approval metadata, not public evidence.

SHA256 binds canonical JSON (UTF-8, sorted keys, compact, ASCII escapes); it
is not a peer signature. Authenticate approvals, their digests and transported
files independently. Distribution comparison also requires both query digests
from authenticated senders, not values taken from the received query files.
This prevents redigesting a changed query after the digest handoff; hashes do
not by themselves authenticate senders.
This protocol assumes honest local collectors and terminal, immutable artifacts.
It is not a confidentiality sandbox for a malicious authorized collector. An
approval is reusable for exactly the same exchange; fresh scope/source changes
require a new approval. The report is local, not a wire artifact. If the
approved native contract is already present at the reviewer, optional
``compare --local-report-contract C`` restores its actual side IDs solely in
the local report for acceptance against a native repeat. Do not move C.
"""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

from .conditions import canonical, execution_settings, expected_conditions

SCHEMA = 1
REQUIRED = ('model', 'inputs', 'numerics', 'scope')
TOP = 100
MAX_WIRE_BYTES = 16 * 1024**2
REVIEWER = 'reviewer'
_SHA = re.compile(r'[a-f0-9]{64}')
_PAYLOADS = {'distributions': {'inventory', 'query'}, 'target-scores': {'targets'}}


def _require(test, message):
    if not test:
        raise ValueError(message)


def _fields(value, fields, label):
    _require(type(value) is dict and set(value) == set(fields.split()), f'invalid {label} fields')


def _int(value, minimum=0):
    return type(value) is int and value >= minimum


def _hash(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _logprob(value):
    return _finite(value) and value <= 0


def digest(value):
    """Return the canonical JSON SHA256 of an approval or payload."""
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def _parse(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result

    result = json.loads(data.decode('utf-8'), object_pairs_hook=unique)
    canonical(result)  # Also rejects overflowed floats, NaN and Infinity.
    return result


def _load(path):
    return _parse(Path(path).read_bytes())


def _wire_load(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_WIRE_BYTES + 1)
    _require(len(data) <= MAX_WIRE_BYTES, 'wire file exceeds byte limit')
    return _parse(data)


def _file_sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            value.update(block)
    return value.hexdigest()


def _policy(value, modality):
    if modality == 'target-scores':
        _fields(value, '', 'target policy')
    else:
        _fields(value, 'vocab_size normalization_tolerance renormalization', 'distribution policy')
        _require(_int(value['vocab_size'], TOP + 4), 'vocabulary too small for minimal exchange')
        tol = value['normalization_tolerance']
        _require(_finite(tol) and 0 < tol < 1, 'invalid normalization tolerance')
        _require(value['renormalization'] in ('none', 'full_vocabulary_fp64'), 'invalid renormalization policy')


def _grant(grant, trusted_digest):
    _fields(grant, 'schema_version kind nonce modality contract_sha256 row_count policy sources transfers', 'grant')
    _require(_hash(trusted_digest) and digest(grant) == trusted_digest, 'grant does not match trusted SHA256')
    _require(type(grant['schema_version']) is int and grant['schema_version'] == SCHEMA and
             grant['kind'] == 'minimal-inference-grant' and _hash(grant['nonce']) and
             _hash(grant['contract_sha256']), 'invalid grant identity')
    modality = grant['modality']
    _require(type(modality) is str and modality in _PAYLOADS and _int(grant['row_count'], 1), 'invalid grant scope')
    _policy(grant['policy'], modality)
    _fields(grant['sources'], '0 1', 'grant sources')
    for source in grant['sources'].values():
        _fields(source, 'source_sha256 selection_sha256', 'source binding')
        _require(all(_hash(value) for value in source.values()), 'invalid source binding')
    _require(type(grant['transfers']) is list and 1 <= len(grant['transfers']) <= 4, 'explicit transfer directions required')
    seen = set()
    for transfer in grant['transfers']:
        _fields(transfer, 'from to kinds', 'grant transfer')
        source, destination, kinds = transfer['from'], transfer['to'], transfer['kinds']
        _require(_int(source) and source < 2 and _recipient(source, destination) and (source, destination) not in seen,
                 'invalid or duplicated transfer direction')
        allowed = _PAYLOADS[modality] if destination == REVIEWER else _PAYLOADS[modality] & {'inventory'}
        _require(type(kinds) is list and bool(kinds) and all(type(k) is str and k in allowed for k in kinds)
                 and len(kinds) == len(set(kinds)), 'invalid transfer scope for recipient')
        seen.add((source, destination))
    return grant


def _recipient(side, destination):
    return destination == REVIEWER or (_int(destination) and destination == 1 - side)


def _direction(grant, side, kind, destination):
    _require(_int(side) and side < 2 and _recipient(side, destination) and
             any(t['from'] == side and t['to'] == destination and kind in t['kinds'] for t in grant['transfers']),
             'transfer direction or payload not granted')


def _require_modality(grant, modality):
    _require(grant['modality'] == modality, 'operation not permitted for grant modality')


def _index(rows, *, vocab=None, targets=False):
    _require(type(rows) is list and bool(rows), 'source coordinate coverage required')
    result = {}
    for row in rows:
        _require(type(row) is dict and type(row.get('sample')) is str and bool(row['sample'])
                 and _int(row.get('position')), 'invalid source coordinate')
        key = (row['sample'], row['position'])
        _require(key not in result, 'duplicate source coordinate')
        if targets:
            _require(_int(row.get('token')), 'invalid target token')
        else:
            history = row.get('history')
            _require(type(history) is list and len(history) == key[1] + 1 and
                     all(_int(t) and t < vocab for t in history), 'invalid causal history')
        result[key] = row
    return result


def _lse(values):
    maximum = max(values, default=-math.inf)
    return maximum if maximum == -math.inf else maximum + math.log(math.fsum(math.exp(v - maximum) for v in values))


def _row(base, record, policy):
    import numpy as np

    descriptor = record.get('scores')
    _require(type(descriptor) is dict and 'row' not in record and
             isinstance(descriptor.get('path'), str), 'invalid source score descriptor')
    path = (base / descriptor['path']).resolve()
    index = descriptor.get('row', 0)
    vocab = policy['vocab_size']
    _require(_int(index), 'invalid source score row index')
    _require(path.suffix == '.npy', 'minimal exchange requires a local .npy score array')
    array = np.load(path, mmap_mode='r', allow_pickle=False)
    try:
        _require(isinstance(array, np.memmap) and array.dtype.kind == 'f' and array.dtype.itemsize in (4, 8)
                 and array.flags.c_contiguous, 'source scores require contiguous FP32 or FP64 arrays')
        if array.ndim == 1:
            _require(array.shape == (vocab,) and index == 0, 'source vocabulary/row mismatch')
            result = array.astype(np.float64)
        else:
            _require(array.ndim == 2 and array.shape[1] == vocab and index < len(array) and
                     (len(array) == 1 or 'row' in descriptor), 'source vocabulary/row mismatch')
            result = array[index].astype(np.float64)
    finally:
        if isinstance(array, np.memmap):
            array._mmap.close()
        elif hasattr(array, 'close'):
            array.close()
    _require(bool((result <= 0).all()), 'invalid full-vocabulary logprobs')
    maximum = float(np.max(result))
    _require(math.isfinite(maximum), 'source probabilities have zero mass')
    norm = maximum + math.log(math.fsum(np.exp(result - maximum)))
    _require(abs(math.expm1(norm)) <= policy['normalization_tolerance'], 'source probabilities are not normalized')
    if policy['renormalization'] == 'full_vocabulary_fp64':
        result -= norm
    return result


def _local(contract, manifest, *, grant=None, operation=None, peer=None, to=None):
    spec, data = _load(contract), _load(manifest)
    _require(type(spec) is dict and type(data) is dict and type(spec.get('kind')) is str and
             spec['kind'] in _PAYLOADS, 'invalid inference artifact kind')
    modality = spec['kind']
    _require(type(spec.get('schema_version')) is int and spec['schema_version'] in
             ((1, 2) if modality == 'distributions' else (1,)) and 'thresholds' in spec and spec['thresholds'] is None,
             'invalid inference contract schema')
    _require(data.get('kind') == modality and type(data.get('schema_version')) is int and
             data['schema_version'] == spec['schema_version'], 'invalid source manifest schema')
    ranks, native_side = spec.get('expected_ranks'), data.get('side')
    _require(type(ranks) is dict and len(ranks) == 2 and type(native_side) is str and native_side in ranks,
             'exchange requires two declared source sides')
    for name in ranks:
        expected_conditions(spec, name, REQUIRED)
    side = sorted(ranks).index(native_side)
    contract_sha = digest(spec)
    if grant is not None:
        _require_modality(grant, modality)
        _require(grant['contract_sha256'] == contract_sha, 'local contract differs from approved contract')
        _direction(grant, side, operation, 1 - side if to is None else to)
        if peer is not None:
            _require(peer['from'] == 1 - side and peer['to'] == side, 'inventory does not address this source')

    base = Path(manifest).resolve().parent
    documents = {}

    def read_rank(path):
        content = Path(path).read_bytes()
        resolved = path.resolve()
        fingerprint = hashlib.sha256(content).hexdigest()
        _require(resolved not in documents or documents[resolved] == fingerprint, 'rank record changed during read')
        documents[resolved] = fingerprint
        return _parse(content)

    execution_settings(spec, data, base, read=read_rank,
                       require_actual=(modality == 'target-scores' or spec['schema_version'] == 2 or bool(spec.get('side_conditions'))))
    policy = ({k: spec.get(k) for k in ('vocab_size', 'normalization_tolerance', 'renormalization')}
              if modality == 'distributions' else {})
    _policy(policy, modality)
    targets = modality == 'target-scores'
    opts = dict(targets=targets, vocab=policy.get('vocab_size'))
    expected = _index(spec.get('targets' if targets else 'positions'), **opts)
    observed = _index(data.get('scores' if targets else 'positions'), **opts)
    _require(expected.keys() == observed.keys(), 'source coordinate coverage differs from contract')
    if grant is not None:
        _require(grant['row_count'] == len(expected) and canonical(grant['policy']) == canonical(policy),
                 'source row count or score policy differs from grant')
    paths, selected, projected = {}, [], []
    for i, key in enumerate(sorted(expected)):
        record = observed[key]
        if targets:
            _require(record['token'] == expected[key]['token'], 'observed target differs from preselected token')
            score = record.get('logprob')
            _require(_logprob(score), 'preselected targets require finite nonpositive logprobs')
            selected.append(score)
            continue
        import numpy as np

        _require(record['history'] == expected[key]['history'], 'observed history differs from contract')
        descriptor = record.get('scores')
        _require(type(descriptor) is dict and type(descriptor.get('path')) is str, 'invalid source score descriptor')
        path = (base / descriptor['path']).resolve()
        if path not in paths:
            paths[path] = _file_sha(path)
        values = _row(base, record, policy)
        top = np.lexsort((np.arange(len(values)), -values))[:TOP].tolist()
        selected.append(top)
        if peer is not None:
            union = _union(top, peer['rows'][i], len(values))
            mask = np.ones(len(values), dtype=bool)
            mask[union] = False
            tail = values[mask]
            maximum = float(np.max(tail))
            tail = maximum if maximum == -math.inf else maximum + math.log(math.fsum(np.exp(tail - maximum)))
            projected.append(dict(logprobs=[_wire_score(float(v)) for v in values[union]], tail_logmass=_wire_score(tail)))
    _require(all(_file_sha(path) == fingerprint for path, fingerprint in {**documents, **paths}.items()),
             'source files changed during evidence read')
    # Include sorted rank content and each used file's content hash; the private
    # manifest binds local descriptor paths/rows and the role of each rank file.
    binding = {
        'source_sha256': digest(dict(manifest=digest(data),
                                    ranks=[documents[(base / r[k]).resolve()] for r in data['ranks']
                                           for k in ('environment', 'status')],
                                    arrays=[paths[(base / observed[key]['scores']['path']).resolve()]
                                            for key in sorted(expected)] if not targets else [])),
        'selection_sha256': digest(selected),
    }
    info = dict(modality=modality, contract_sha256=contract_sha, row_count=len(expected), policy=policy, side=side, **binding)
    if grant is not None:
        _require(grant['sources'][str(side)] == binding, 'source does not match grant commitments')
    return info, projected if peer is not None else selected


def inspect_source(contract, manifest):
    """Return local approval metadata, not scores, IDs, histories or native identity strings."""
    return _local(contract, manifest)[0]


def _wire_score(value):
    return None if value == -math.inf else value


def _union(top, other, vocab):
    union = sorted(set(top) | set(other))
    # Reserve a nontrivial complement even if one primary and one alternative
    # target are separately approved for the same prediction row.
    _require(len(union) <= vocab - 4, 'union would disclose effectively full-vocabulary scores')
    return union


def _wire(kind, trusted_digest, side, destination, rows, **extra):
    result = dict(schema_version=SCHEMA, kind=kind, grant_sha256=trusted_digest, to=destination, rows=rows, **extra)
    result['from'] = side
    result['payload_sha256'] = digest(result)
    _require(len(canonical(result)) + 1 <= MAX_WIRE_BYTES, 'wire evidence exceeds byte limit')
    return result


def _verify(grant, approved, payload, kind, *, recipient):
    extras = ' own_selection_sha256 peer_selection_sha256' if kind == 'query' else ''
    _fields(payload, 'schema_version kind grant_sha256 from to rows payload_sha256' + extras, 'wire payload')
    _require(type(payload['schema_version']) is int and payload['schema_version'] == SCHEMA and
             payload['kind'] == kind and payload['grant_sha256'] == approved, 'wire schema or grant binding mismatch')
    side = payload['from']
    destination = payload['to']
    _require(_int(side) and side < 2 and _recipient(side, destination) and
             destination == (1 - side if recipient is None else recipient), 'wire destination mismatch')
    _direction(grant, side, kind, destination)
    rows = payload['rows']
    _require(type(rows) is list and len(rows) == grant['row_count'], 'wire row coverage mismatch')
    if kind == 'inventory':
        vocab = grant['policy']['vocab_size']
        for row in rows:
            _require(type(row) is list and len(row) == TOP and all(_int(t) and t < vocab for t in row)
                     and len(set(row)) == TOP, 'invalid wire top-100 IDs')
    elif kind == 'targets':
        _require(all(_logprob(score) for score in rows), 'invalid target logprob')
    else:
        _require(_hash(payload['own_selection_sha256']) and _hash(payload['peer_selection_sha256']),
                 'invalid query inventory selection bindings')
        for row in rows:
            _fields(row, 'logprobs tail_logmass', 'wire query row')
            selected = row['logprobs']
            _require(type(selected) is list and TOP <= len(selected) <= 2 * TOP and
                     all(v is None or _logprob(v) for v in [*selected, row['tail_logmass']]), 'invalid wire query logprobs')
    if kind != 'query':
        _require(digest(rows) == grant['sources'][str(side)]['selection_sha256'], 'evidence differs from grant selection commitment')
    unsigned = {k: v for k, v in payload.items() if k != 'payload_sha256'}
    _require(_hash(payload['payload_sha256']) and digest(unsigned) == payload['payload_sha256'], 'wire payload digest mismatch')
    _require(len(canonical(payload)) + 1 <= MAX_WIRE_BYTES, 'wire evidence exceeds byte limit')
    return payload


def inventory(grant, grant_sha256, contract, manifest, *, to=None):
    """Project top IDs for the peer (default) or the independently allowed reviewer."""
    grant = _grant(grant, grant_sha256)
    _require_modality(grant, 'distributions')
    info, rows = _local(contract, manifest, grant=grant, operation='inventory', to=to)
    side = info['side']
    return _wire('inventory', grant_sha256, side, 1 - side if to is None else to, rows)


def query(grant, grant_sha256, contract, manifest, own_inventory, peer_inventory):
    """Respond to reciprocal peer inventories, exclusively for the granted reviewer."""
    grant = _grant(grant, grant_sha256)
    _require_modality(grant, 'distributions')
    own = _verify(grant, grant_sha256, own_inventory, 'inventory', recipient=None)
    peer = _verify(grant, grant_sha256, peer_inventory, 'inventory', recipient=None)
    _require(own['from'] != peer['from'], 'query inventories must be reciprocal')
    info, rows = _local(contract, manifest, grant=grant, operation='query', peer=peer, to=REVIEWER)
    _require(info['side'] == own['from'], 'own inventory is not from this source')
    return _wire('query', grant_sha256, info['side'], REVIEWER, rows,
                 own_selection_sha256=info['selection_sha256'], peer_selection_sha256=digest(peer['rows']))


def targets(grant, grant_sha256, contract, manifest):
    """Project only the contract-preselected scores, exclusively for the reviewer."""
    grant = _grant(grant, grant_sha256)
    _require_modality(grant, 'target-scores')
    info, rows = _local(contract, manifest, grant=grant, operation='targets', to=REVIEWER)
    return _wire('targets', grant_sha256, info['side'], REVIEWER, rows)


def _score_reducer():
    """Load the existing standalone module by path without modifying sys.path."""
    directory = Path(__file__).resolve().parent.parent
    paths = (directory / 'score_metrics.py', directory / 'skills/precision-compare/scripts/score_metrics.py')
    path = next((p for p in paths if p.is_file()), None)
    if path is None:
        raise ImportError('precision-compare score_metrics.py is unavailable')
    spec = importlib.util.spec_from_file_location('_numericbridge_score_metrics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.reduce_scores


def _ordered(a, b):
    _require(a['from'] == 0 and b['from'] == 1, 'expected side aliases 0 then 1')
    return a, b


def _distribution_metrics(union, t_a, t_b, a, b, tolerance):
    values = []
    for top, row in ((t_a, a), (t_b, b)):
        _require(len(row['logprobs']) == len(union), 'query does not cover exactly the approved union')
        scores = [-math.inf if v is None else v for v in [*row['logprobs'], row['tail_logmass']]]
        _require(abs(math.expm1(_lse(scores))) <= tolerance, 'queried probabilities are not normalized')
        lookup = dict(zip(union, scores))
        order = sorted(union, key=lambda t: (-lookup[t], t))
        _require(top == order[:TOP], 'query contradicts committed top-100 ordering')
        values.append(scores)
    p, q = [list(map(math.exp, v)) for v in values]
    joint = [(_lse((x, y)) - math.log(2)) for x, y in zip(*values)]
    js = (0. if values[0] == values[1] else max(0., .5 * math.fsum(
        prob * (logp - middle) for probs, logs in zip((p, q), values)
        for prob, logp, middle in zip(probs, logs, joint) if math.isfinite(logp))))
    errors = [abs(x - y) for x, y in zip(p[:-1], q[:-1])]
    worst = max(range(len(union)), key=errors.__getitem__)
    index = {token: i for i, token in enumerate(union)}
    result = dict(union_count=len(union), tail_merged_js=dict(value=js, status='no_thresholds'),
                  top1_agreement=t_a[0] == t_b[0], top100_overlap=len(set(t_a) & set(t_b)) / TOP,
                  probability_rmse=math.sqrt(math.fsum(e * e for e in errors) / len(union)),
                  max_probability_error=errors[worst], max_error_token=union[worst],
                  coverage=dict(side_a_top100=math.fsum(p[index[t]] for t in t_a),
                                side_b_top100=math.fsum(q[index[t]] for t in t_b),
                                side_a_union=math.fsum(p[:-1]), side_b_union=math.fsum(q[:-1])))
    if not result['top1_agreement']:
        result['top1_mismatch'] = dict(side_a_token=t_a[0], side_b_token=t_b[0],
                                       side_a_margin=p[index[t_a[0]]] - p[index[t_a[1]]],
                                       side_b_margin=q[index[t_b[0]]] - q[index[t_b[1]]],
                                       probabilities=[dict(token=t, side_a=p[index[t]], side_b=q[index[t]])
                                                      for t in sorted((t_a[0], t_b[0]))])
    return result


def _quantile(values, q):
    values = sorted(values)
    at = q * (len(values) - 1)
    lo, hi = math.floor(at), math.ceil(at)
    return values[lo] + (at - lo) * (values[hi] - values[lo])


def _aggregate(positions):
    result = {}
    for name in ('tail_merged_js', 'probability_rmse', 'max_probability_error'):
        values = [p[name]['value'] if name == 'tail_merged_js' else p[name] for p in positions]
        worst = max(range(len(values)), key=values.__getitem__)
        coordinate = dict(sample='row', position=worst)
        if name == 'max_probability_error':
            coordinate['token'] = positions[worst]['max_error_token']
        result[name] = dict(mean=math.fsum(values) / len(values), p99=_quantile(values, .99), max=values[worst],
                            max_coordinate=coordinate)
    total = len(positions)
    count = sum(row['top1_agreement'] for row in positions)
    result['top1_agreement'] = dict(count=count, total=total, fraction=count / total)
    result['top100_overlap'] = dict(mean=math.fsum(p['top100_overlap'] for p in positions) / total)
    result['coverage'] = {}
    for name in positions[0]['coverage']:
        values = [p['coverage'][name] for p in positions]
        result['coverage'][name] = dict(mean=math.fsum(values) / total, p1=_quantile(values, .01), min=min(values))
    return result


def compare(grant, grant_sha256, side_a, side_b, *, inventory_a=None, inventory_b=None,
            query_a_sha256=None, query_b_sha256=None, local_report_contract=None):
    """Verify a reciprocal pair and compute no-threshold metrics on opaque rows.

    Optional local_report_contract resolves native side labels for local-only
    acceptance, and must hash to the approved contract.
    """
    grant = _grant(grant, grant_sha256)
    mode, count = grant['modality'], grant['row_count']
    kind = 'query' if mode == 'distributions' else 'targets'
    a, b = _ordered(*(_verify(grant, grant_sha256, item, kind, recipient=REVIEWER) for item in (side_a, side_b)))
    labels = ('0', '1')
    if local_report_contract is not None:
        spec = _load(local_report_contract)
        _require(type(spec) is dict and digest(spec) == grant['contract_sha256'] and spec.get('kind') == mode,
                 'local report contract differs from approved contract')
        ranks = spec.get('expected_ranks')
        _require(type(ranks) is dict and len(ranks) == 2 and all(k.strip() for k in ranks),
                 'invalid local report side IDs')
        labels = sorted(ranks)
    report = dict(kind=mode, schema_version=2 if kind == 'query' else 1, status='no_thresholds',
                  thresholds=None, sides=dict(zip(('side_a', 'side_b'), labels)), minimal_exchange=True,
                  grant_sha256=grant_sha256, self_comparison=False)
    if kind == 'targets':
        _require(all(v is None for v in (inventory_a, inventory_b, query_a_sha256, query_b_sha256)),
                 'target-score reports do not use distribution bindings')
        metrics = _score_reducer()(*({(i,): v for i, v in enumerate(p['rows'])} for p in (a, b)))
        metrics['max_coordinate'] = dict(sample='row', position=metrics['max_coordinate'][0])
        return dict(report, metrics=metrics, coverage=dict(expected=count, paired=count))
    _require(_hash(query_a_sha256) and _hash(query_b_sha256) and
             (query_a_sha256, query_b_sha256) == (a['payload_sha256'], b['payload_sha256']),
             'query differs from independently approved payload binding')
    left, right = _ordered(*(_verify(grant, grant_sha256, item, 'inventory', recipient=REVIEWER)
                             for item in (inventory_a, inventory_b)))
    _require(a['own_selection_sha256'] == b['peer_selection_sha256'] == digest(left['rows']) and
             b['own_selection_sha256'] == a['peer_selection_sha256'] == digest(right['rows']),
             'query inventory bindings are not reciprocal')
    policy = grant['policy']
    positions = []
    for i, (top_a, top_b, row_a, row_b) in enumerate(zip(left['rows'], right['rows'], a['rows'], b['rows'])):
        union = _union(top_a, top_b, policy['vocab_size'])
        positions.append(dict(sample='row', position=i, **_distribution_metrics(
            union, top_a, top_b, row_a, row_b, policy['normalization_tolerance'])))
    return dict(report, position_count=count, positions=positions, aggregate=_aggregate(positions),
                normalization_tolerance=policy['normalization_tolerance'], renormalization=policy['renormalization'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('inspect-source', 'grant-id', 'inventory', 'query', 'targets', 'compare'):
        command = sub.add_parser(action)
        if action != 'inspect-source':
            command.add_argument('--grant', required=True)
        if action not in ('inspect-source', 'grant-id'):
            command.add_argument('--grant-sha256', required=True, help='independently approved canonical SHA256')
        if action in ('inspect-source', 'inventory', 'query', 'targets'):
            command.add_argument('--contract', required=True)
            command.add_argument('--manifest', required=True)
        if action == 'inventory':
            command.add_argument('--to', choices=('peer', REVIEWER), default='peer')
        if action == 'query':
            command.add_argument('--own-inventory', required=True)
            command.add_argument('--peer-inventory', required=True)
        if action == 'compare':
            command.add_argument('--side-a', required=True)
            command.add_argument('--side-b', required=True)
            command.add_argument('--inventory-a')
            command.add_argument('--inventory-b')
            command.add_argument('--query-a-sha256', help='trusted sender-provided query A payload_sha256')
            command.add_argument('--query-b-sha256', help='trusted sender-provided query B payload_sha256')
            command.add_argument('--local-report-contract', help='already-local approved native contract for report side IDs; never transferred')
        if action != 'grant-id':
            command.add_argument('--output', required=True, help='new file only')
    args = parser.parse_args(argv)
    try:
        if args.action == 'inspect-source':
            result = inspect_source(args.contract, args.manifest)
        else:
            grant = _wire_load(args.grant)
            if args.action == 'grant-id':
                approved = digest(grant)
                _grant(grant, approved)
                print(approved)
                return 0
            approved = args.grant_sha256
            if args.action == 'compare':
                result = compare(grant, approved, _wire_load(args.side_a), _wire_load(args.side_b),
                                 inventory_a=_wire_load(args.inventory_a) if args.inventory_a else None,
                                 inventory_b=_wire_load(args.inventory_b) if args.inventory_b else None,
                                 query_a_sha256=args.query_a_sha256, query_b_sha256=args.query_b_sha256,
                                 local_report_contract=args.local_report_contract)
            else:
                local = (grant, approved, args.contract, args.manifest)
                if args.action == 'inventory':
                    result = inventory(*local, to=REVIEWER if args.to == REVIEWER else None)
                elif args.action == 'query':
                    result = query(*local, _wire_load(args.own_inventory), _wire_load(args.peer_inventory))
                else:
                    result = targets(*local)
        output = canonical(result) + '\n'
        with Path(args.output).open('x', encoding='utf-8') as stream:
            stream.write(output)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, ImportError, RecursionError) as exc:
        print(f'minimal exchange: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

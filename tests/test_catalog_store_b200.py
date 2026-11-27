"""Regression checks for explicitly approved display-only B200 corrections."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from community.catalog_store import operate, read_current, verify_release
from community.records.catalog import Catalog
from community.records.hardware import canonical_b200_case, canonical_b200_evaluation
from community.records.search import compile_index


REVISION = 'a' * 40
EVIDENCE = 'evidence/native-b20z.json'


def evaluation(identity, hardware='2xB20Z'):
    peer = dict(id='b20z-workload', label='b20z under test', hardware=hardware,
                libraries=[dict(name='PyTorch', version='v1')],
                settings={'hardware': 'B20Z', 'path': '/retained/b20z/run', 'devices': 2})
    reference = dict(id='reference', label='Reference', hardware='CPU',
                     libraries=peer['libraries'], settings={})
    return dict(id=identity, date='2026-01-02', title=f'{hardware} evaluation', task='inference',
                status='completed', model=dict(name='Fixture', revision='fixture-v1'),
                content=dict(title={'en': f'{hardware} fixture', 'zh': f'在{hardware}测试'},
                             summary={'en': 'Native B20Z was retained.'}),
                summary='Original B20Z annotation.', common=['B20Z report retained'],
                differences=[f'({hardware}) vs CPU', 'H20Z and XB20Z, B20ZX, B20Z_v2, a2xB20Z'],
                notes=['B20Z is the native device name.'], acceptance=None,
                sides=[peer, reference], comparisons=[dict(
                    id='paired', title='B20Z vs CPU', scope='One fixture input',
                    side_ids=['b20z-workload', 'reference'],
                    metrics=[dict(id='error', value=0.125, label='B20Z max error', unit='slot')],
                    coverage=[dict(label='Inputs', observed=1, expected=1, unit='sample')])],
                evidence=[dict(url=EVIDENCE, label='Original B20Z')],
                archive=dict(revision=REVISION, prefix='reports/b20z', replay_cases=1))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n',
                    encoding='utf-8')


def write_site(site, evaluations):
    (site / 'data').mkdir(parents=True)
    (site / 'evidence').mkdir()
    write_json(site / EVIDENCE, {'device': 'B20Z', 'value': 0.125})
    (site / 'evidence/report.md').write_text('Native B20Z report.\n', encoding='utf-8')
    write_json(site / 'data/rsi.json', {'snapshot': 'unchanged'})
    source = dict(id='fixture-source', commit=REVISION,
                  url=f'https://example.invalid/blob/{REVISION}/results.json', json_pointer='')
    write_json(site / 'data/differences.json', dict(
        schema_version=1, updated_at='2026-01-02', branches=[dict(branch='survey/fixture', commit=REVISION)],
        models=[dict(id='fixture-model')], sources=[dict(id=source['id'])],
        records=[dict(id='fixture-observation', model_id='fixture-model', comparison_kind='native-reference',
                      label='Fixture', scope='One value', source=source,
                      metrics=[dict(key='error', value=1, source_pointer='/error')])],
        coverage=dict(branches=1, unique_source_files=1, raw_json_sources=1,
                      recorded_views=1, numeric_metrics=1, by_comparison_kind={'native-reference': 1})))
    write_json(site / 'data/differences-raw.json', dict(schema_version=1, sources={source['id']: {'error': 1}}))
    write_json(site / 'data/repairs.json', dict(schema_version=1, updated_at='2026-01-02', repairs=[], library_groups=[]))
    write_json(site / 'data/relations.json', dict(schema_version=1, entities=[], links=[]))
    write_evaluations(site, evaluations)


def case_relations(site):
    evidence = [dict(file=EVIDENCE, sha256=sha256((site / EVIDENCE).read_bytes()).hexdigest())]
    cases = [dict(id=identity, kind='case', scope='Original B20Z scope', evidence=evidence,
                  details=dict(title='Original B20Z title', sources=[], request=dict(
                      hardware=hardware, model='Fixture', framework=None, task='inference', goal='Retain B20Z goal')))
             for identity, hardware in (('linked-case', '2xB20Z'), ('already-case', 'B200'))]
    investigation = dict(id='investigation', kind='investigation', scope='B20Z', evidence=evidence)
    links = [{'id': f"{row['id']}-member", 'relation': 'includes', 'from': dict(kind='case', id=row['id']),
              'to': dict(kind='comparison', id='second-run'), 'scope': 'B20Z', 'evidence': evidence}
             for row in cases]
    return dict(schema_version=1, entities=[*cases, investigation], links=links)


def write_relations(site, relations):
    write_json(site / 'data/relations.json', relations)
    write_json(site / 'data/search-index.json', compile_index(Catalog(site)))


def write_evaluations(site, evaluations, *, refresh=True):
    write_json(site / 'data/evaluations.json', dict(schema_version=1, updated_at='2026-01-02', evaluations=evaluations))
    if refresh:
        write_json(site / 'data/search-index.json', compile_index(Catalog(site)))


class B200EvaluationTests(unittest.TestCase):
    def test_only_named_display_fields_change(self):
        source = evaluation('run-b20z')
        source['differences'].append('B20Z, 12XB20z, 8×b20z and 4 x b20Z')
        original = deepcopy(source)
        expected = deepcopy(source)
        expected['title'] = '2xB200 evaluation'
        expected['content']['title'] = {'en': '2xB200 fixture', 'zh': '在2xB200测试'}
        expected['differences'][0] = '(2xB200) vs CPU'
        expected['differences'][-1] = 'B200, 12XB200, 8×B200 and 4 x B200'
        expected['sides'][0].update(hardware='2xB200', label='B200 under test')
        corrected = canonical_b200_evaluation(source)
        self.assertEqual(corrected, expected)
        self.assertEqual(source, original)
        self.assertEqual(canonical_b200_evaluation(corrected), corrected)


class B200PromotionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        self.root = workspace / 'store'
        self.root.mkdir()
        self.site = workspace / 'candidate'
        self.old = [evaluation('run-b20z'), evaluation('second-run', 'B20Z'),
                    canonical_b200_evaluation(evaluation('already-b200'))]
        write_site(self.site, self.old)
        self.relations = case_relations(self.site)
        write_relations(self.site, self.relations)
        operate(self.args('seed', 'seed'), self.root)  # Existing API: no b200_evaluations argument.
        self.prior = self.root / 'releases/seed'

    def args(self, command='promote', generation='new', dry_run=True, **kwargs):
        return SimpleNamespace(command=command, generation=generation, dry_run=dry_run if command != 'seed' else False,
                               expect_current='seed', site=self.site, reports=None, source_revision=REVISION, **kwargs)

    def cli(self, module, *args):
        return subprocess.run([sys.executable, '-B', '-m', module, *map(str, args)],
                              check=True, text=True, capture_output=True).stdout

    def test_repeated_cli_selection_promotes_and_rolls_back_exactly(self):
        old_hashes = verify_release(self.prior)
        old_manifest = (self.prior / 'SHA256SUMS').read_bytes()
        fixed = [canonical_b200_evaluation(row) for row in self.old]
        write_evaluations(self.site, fixed + [evaluation('new-run', 'B200')])
        relations = deepcopy(self.relations)
        relations['entities'][0] = canonical_b200_case(relations['entities'][0])
        write_relations(self.site, relations)
        records = self.cli('community.records', 'validate', '--site', self.site)
        self.assertIn('Validated 4 evaluations', records)
        search = self.cli('scripts', 'export-search', '--check', '--site', self.site)
        self.assertTrue(json.loads(search)['checked'])
        promote = ['promote', '--root', self.root, '--expect-current', 'seed', '--generation', 'new',
                   '--site', self.site, '--source-revision', REVISION,
                   '--b200-evaluation', 'run-b20z', '--b200-evaluation', 'second-run', '--b200-case', 'linked-case']
        self.assertIn('Dry run: promote seed -> new', self.cli('community.catalog_store', *promote, '--dry-run'))
        self.assertEqual(read_current(self.root), 'seed')
        self.assertFalse((self.root / 'releases/new').exists())
        self.assertIn('promote seed -> new', self.cli('community.catalog_store', *promote))
        self.assertEqual(read_current(self.root), 'new')
        promoted = self.root / 'releases/new'
        new_hashes = verify_release(promoted)
        changed = {name for name, digest in old_hashes.items() if new_hashes[name] != digest}
        self.assertEqual(changed, {'site/data/evaluations.json', 'site/data/relations.json', 'site/data/search-index.json'})
        self.assertEqual((promoted / 'site/data/relations.json').read_bytes(),
                         (self.site / 'data/relations.json').read_bytes())
        self.assertEqual((promoted / 'site/data/evaluations.json').read_bytes(),
                         (self.site / 'data/evaluations.json').read_bytes())
        self.assertEqual(verify_release(self.prior), old_hashes)
        self.assertEqual((self.prior / 'SHA256SUMS').read_bytes(), old_manifest)
        operate(self.args('rollback', dry_run=False), self.root)
        self.assertEqual(read_current(self.root), 'seed')
        self.assertEqual(verify_release(promoted), new_hashes)

    def test_default_preservation_and_every_selection_is_required(self):
        self.assertIn('Dry run: promote', operate(self.args(), self.root))
        cases = [
            (self.old, {'b200_evaluations': ['run-b20z']}, 'record run-b20z was modified'),
            (self.old, {'b200_evaluations': ['already-b200']}, 'no B20Z display correction for already-b200'),
            (self.old + [evaluation('new-only')], {'b200_evaluations': ['new-only']}, 'not published.*new-only'),
            (self.old, {'b200_evaluations': ['run-b20z', 'run-b20z']}, 'Duplicate B200 evaluation ID'),
            ([canonical_b200_evaluation(self.old[0]), *self.old[1:]], {}, 'record run-b20z was modified'),
            ([canonical_b200_evaluation(self.old[0]), *self.old[1:]],
             {'b200_evaluations': ['run-b20z', 'second-run']}, 'record second-run was modified'),
        ]
        for rows, options, message in cases:
            with self.subTest(message=message):
                write_evaluations(self.site, rows)
                with self.assertRaisesRegex(ValueError, message):
                    operate(self.args(**options), self.root)
                self.assertEqual(read_current(self.root), 'seed')

    def test_reject_inexact_and_unselected_edits(self):
        selected = ('run-b20z',)
        corrected = [canonical_b200_evaluation(self.old[0]), *self.old[1:]]
        partial, arbitrary, unselected, measurement, new_id = (deepcopy(corrected) for _ in range(5))
        partial[0]['sides'][0]['label'] = self.old[0]['sides'][0]['label']
        arbitrary[0]['sides'][0]['settings']['hardware'] = 'B200'
        unselected[1] = canonical_b200_evaluation(unselected[1])
        measurement[0]['comparisons'][0]['metrics'][0]['value'] = 0.25
        new_id[0]['id'] = 'rewritten-id'
        cases = [partial, arbitrary, unselected, measurement, new_id, corrected[1:],
                 [corrected[1], corrected[0], corrected[2]]]
        for i, rows in enumerate(cases):
            with self.subTest(case=i):
                write_evaluations(self.site, rows)
                with self.assertRaisesRegex(ValueError, 'was modified|was removed|must stay as a prefix'):
                    operate(self.args(b200_evaluations=selected), self.root)
                self.assertEqual(read_current(self.root), 'seed')

    def test_correction_does_not_exempt_snapshots_or_evidence(self):
        fixed = [canonical_b200_evaluation(self.old[0]), *self.old[1:]]
        args = self.args(b200_evaluations=['run-b20z'])
        write_evaluations(self.site, fixed, refresh=False)
        with self.assertRaisesRegex(ValueError, 'stale or edited'):
            operate(args, self.root)
        write_evaluations(self.site, fixed)
        rsi = self.site / 'data/rsi.json'
        write_json(rsi, {'snapshot': 'changed'})
        with self.assertRaisesRegex(ValueError, 'must retain its original bytes: site/data/rsi.json'):
            operate(args, self.root)
        shutil.copyfile(self.prior / 'site/data/rsi.json', rsi)
        (self.site / 'evidence/report.md').write_text('Altered B200 report.\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'must retain its original bytes: site/evidence/report.md'):
            operate(args, self.root)
        self.assertEqual(read_current(self.root), 'seed')

    def test_case_correction_is_exact_and_requires_independent_opt_in(self):
        original = deepcopy(self.relations['entities'][0])
        expected = deepcopy(original)
        expected['details']['request']['hardware'] = '2xB200'
        self.assertEqual(canonical_b200_case(self.relations['entities'][0]), expected)
        self.assertEqual(self.relations['entities'][0], original)
        other_kind = {**original, 'kind': 'investigation'}
        self.assertEqual(canonical_b200_case(other_kind), other_kind)
        unchanged = self.relations
        corrected = deepcopy(unchanged)
        corrected['entities'][0] = expected
        arbitrary = deepcopy(corrected)
        arbitrary['entities'][0]['details']['title'] = 'Original B200 title'
        link_edit = deepcopy(corrected)
        link_edit['links'][0]['scope'] = 'B200'
        for relations, selected, message in [
            (unchanged, ('linked-case',), 'record linked-case was modified'),
            (unchanged, ('already-case',), 'no B20Z display correction for already-case'),
            (unchanged, ('linked-case', 'linked-case'), 'Duplicate B200 case ID'),
            (unchanged, ('investigation',), 'not published as a case.*investigation'),
            (corrected, (), 'record linked-case was modified'),
            (arbitrary, ('linked-case',), 'record linked-case was modified'),
            (link_edit, ('linked-case',), 'record linked-case-member was modified'),
        ]:
            with self.subTest(message=message):
                write_relations(self.site, relations)
                with self.assertRaisesRegex(ValueError, message):
                    operate(self.args(b200_cases=selected), self.root)
                self.assertEqual(read_current(self.root), 'seed')
        write_relations(self.site, corrected)
        self.assertIn('Dry run: promote', operate(self.args(b200_cases=['linked-case']), self.root))

    def test_only_promote_can_select_historical_ids(self):
        for command in ('seed', 'rollback'):
            with self.subTest(command=command):
                with self.assertRaisesRegex(ValueError, 'require an explicit promotion'):
                    operate(self.args(command, b200_evaluations=['run-b20z']), self.root)
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'require a current generation'):
            operate(self.args(b200_evaluations=['run-b20z']), empty)


if __name__ == '__main__':
    unittest.main()

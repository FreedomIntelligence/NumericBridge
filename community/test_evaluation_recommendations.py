"""Exercise evaluation eligibility through catalog validation and published search."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from community.normalize import normalize_draft
from community.records.catalog import Catalog
from community.records.validation import validate_evaluations
from scripts.export_statistics import export
from tools.community_search import load_index, search


DATE = '2026-01-02'
REQUEST = dict(hardware='CPU', model='Fixture model', library='Fixture engine', version='1.0', task='inference')


def evaluation(**fields):
    def side(identity):
        return dict(id=identity, label=identity, hardware=REQUEST['hardware'], settings={},
                    libraries=[dict(name=REQUEST['library'], version=REQUEST['version'])],
                    frameworks=[REQUEST['library']])

    return dict(id='paired-evaluation', date=DATE, title='Synthetic paired evaluation', task=REQUEST['task'],
                status='completed', acceptance=dict(verdict='pass'), model=dict(name=REQUEST['model'], revision='v1'),
                summary='Compare outputs on a synthetic fixture.', sides=[side('a'), side('b')], common=[],
                differences=[], notes=[], archive=None, evidence=[dict(label='Fixture', url='evidence/fixture.json')],
                comparisons=[dict(id='paired', title='Paired', scope='Synthetic inputs', side_ids=['a', 'b'],
                                  metrics=[dict(id='mismatch', label='Mismatches', unit='output', value=0)],
                                  coverage=[dict(label='Inputs', observed=8, expected=8, unit='sample')])]) | fields


def write_site(site, *records):
    (site / 'data').mkdir(parents=True, exist_ok=True)
    (site / 'evidence').mkdir(exist_ok=True)
    payload = b'{"mismatches":0}'
    (site / 'evidence/fixture.json').write_bytes(payload)
    evidence = [dict(file='evidence/fixture.json', sha256=sha256(payload).hexdigest())]
    case = dict(kind='case', id='fixture-case')
    request = dict(hardware=REQUEST['hardware'], model=REQUEST['model'], framework=REQUEST['library'],
                   task=REQUEST['task'], goal='Compare outputs')
    relations = dict(entities=[dict(case, scope='Fixture', evidence=evidence,
                                    details=dict(title='Fixture case', sources=[], request=request))] if records else [],
                     links=[dict(id='includes-' + r['id'], relation='includes', scope='Fixture', evidence=evidence,
                                 **{'from': case, 'to': dict(kind='comparison', id=r['id'])}) for r in records])
    survey = dict(updated_at=DATE, branches=[dict(branch='survey/fixture', commit='1' * 40)],
                  sources=[dict(id='fixture-source')], models=[dict(id='fixture-model')],
                  records=[dict(id='fixture-view', model_id='fixture-model', comparison_kind='native-reference',
                                label='Fixture view', scope='Synthetic', metrics=[], source=dict(
                                    id='fixture-source', commit='1' * 40, json_pointer='',
                                    url='https://example.org/' + '1' * 40))],
                  coverage=dict(recorded_views=1, numeric_metrics=0, branches=1, unique_source_files=1,
                                raw_json_sources=1))
    for name, body in dict(evaluations=dict(updated_at=DATE, evaluations=records),
                           repairs=dict(updated_at=DATE, repairs=[], library_groups=[]), relations=relations,
                           differences=survey, **{'differences-raw': dict(sources={'fixture-source': {}})}).items():
        (site / 'data' / (name + '.json')).write_text(json.dumps(dict(schema_version=1, **body)), encoding='utf-8')


class EvaluationRecommendationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.site = Path(temporary.name) / 'site'

    def test_legacy_acceptance_pass_and_gap_still_recommend(self):
        for acceptance in ('passed', dict(verdict='pass'), dict(verdict='gap')):
            with self.subTest(acceptance=acceptance):
                write_site(self.site, evaluation(acceptance=acceptance))
                self.assertEqual(validate_evaluations(self.site), 1)
                result = Catalog(self.site).search(REQUEST)
                self.assertEqual(result['status'], 'matched')
                self.assertEqual(result['recommendation']['basis'], 'recorded-numerical-acceptance')
                record = result['recommendation']['configuration']['record']
                self.assertEqual(record['acceptance'], acceptance)
                self.assertNotIn('recommendation_eligible', record)

    def test_report_only_is_retrievable_from_published_index_without_recommendations(self):
        record = evaluation(recommendation_eligible=False, acceptance=dict(verdict='pass', passed=True))
        write_site(self.site, record)
        for module, args in [('scripts', ['export-search']), ('scripts', ['export-search', '--check']),
                             ('community.records', ['validate'])]:
            subprocess.run([sys.executable, '-B', '-m', module, *args, '--site', str(self.site)],
                           check=True, capture_output=True, text=True)
        index, _ = load_index(self.site / 'data/search-index.json')
        result = search(index, REQUEST | {'query': 'mismatches'})
        self.assertEqual(result['status'], 'matched')
        self.assertEqual([match['id'] for match in result['matches']], ['fixture-case'])
        self.assertEqual(len(result['matches'][0]['configurations']), 2)
        self.assertIsNone(result['recommendation'])
        self.assertEqual(result['recommendations'], [])
        self.assertIn('report-only', result['recommendation_gaps'])
        for config in result['matches'][0]['configurations']:
            self.assertEqual(config['record']['status'], 'completed')
            self.assertEqual(config['record']['acceptance'], record['acceptance'])
            self.assertIs(config['record']['recommendation_eligible'], False)
            self.assertEqual(config['recommendation_gaps'], ['report-only'])
        self.assertEqual(search(index, REQUEST | {'version': '2.0'}, allow_alternatives=True)['alternatives'], [])
        self.assertEqual(Catalog(self.site).case_bundle('fixture-case')['records'][0]['record'], record)

    def test_report_only_does_not_block_another_eligible_evaluation_in_the_same_case(self):
        write_site(self.site, evaluation(recommendation_eligible=False), evaluation(id='eligible-evaluation'))
        result = Catalog(self.site).search(REQUEST)
        self.assertEqual([match['id'] for match in result['matches']], ['fixture-case'])
        self.assertEqual({config['record']['id'] for config in result['matches'][0]['configurations']},
                         {'paired-evaluation', 'eligible-evaluation'})
        self.assertEqual({option['configuration']['record']['id'] for option in result['recommendations']},
                         {'eligible-evaluation'})

    def test_explicit_true_leaves_existing_gates_in_force(self):
        for fields, gap in [({'status': 'partial'}, 'incomplete-workload'),
                            ({'acceptance': {'verdict': 'fail'}}, 'no-recorded-numerical-acceptance')]:
            with self.subTest(gap=gap):
                write_site(self.site, evaluation(recommendation_eligible=True, **fields))
                self.assertEqual(validate_evaluations(self.site), 1)
                result = Catalog(self.site).search(REQUEST)
                self.assertEqual(result['status'], 'matched')
                self.assertIsNone(result['recommendation'])
                self.assertIn(gap, result['recommendation_gaps'])
                self.assertNotIn('report-only', result['recommendation_gaps'])
        write_site(self.site, evaluation(recommendation_eligible=True))
        result = Catalog(self.site).search(REQUEST)
        self.assertIs(result['recommendation']['configuration']['record']['recommendation_eligible'], True)

    def test_invalid_flag_types_are_rejected(self):
        for value in (None, 0, 1, 'true', 'false', [], {}):
            with self.subTest(value=value):
                write_site(self.site, evaluation(recommendation_eligible=value))
                for validate in (validate_evaluations, Catalog):
                    with self.assertRaisesRegex(ValueError, 'paired-evaluation: recommendation_eligible must be a boolean'):
                        validate(self.site)

    def test_review_and_statistics_projections_preserve_optional_flag(self):
        base = self.site.parent / 'base'
        write_site(base)
        for flag in ({}, {'recommendation_eligible': False}, {'recommendation_eligible': True}):
            with self.subTest(flag=flag):
                record = evaluation(archive=dict(revision='1' * 40, prefix='fixture/', replay_cases=1), **flag)
                write_site(self.site, record)
                summary = normalize_draft({'additions': {'evaluations': {'evaluations': [record]}}})['evaluations'][0]
                self.assertIs(summary['acceptance_passed'], True)
                statistics = export(base / 'data/evaluations.json', self.site / 'data/evaluations.json')['evaluations'][0]
                for projection in (summary, statistics):
                    self.assertEqual({k: v for k, v in projection.items() if k == 'recommendation_eligible'}, flag)


if __name__ == '__main__':
    unittest.main()

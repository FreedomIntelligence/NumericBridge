"""Synthetic CPU-only regression for the local minimal inference exchange."""

import copy
import json
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from tools import minimal_exchange as exchange

ROOT = Path(__file__).resolve().parents[1]
COMPARE = ROOT / 'skills/precision-compare/scripts'
PRIVATE = 'PRIVATE-FIXTURE-DO-NOT-SEND'


def put(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def rehash(message):
    message['payload_sha256'] = exchange.digest({k: v for k, v in message.items() if k != 'payload_sha256'})
    return message


class MinimalExchangeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.hosts = [self.root / name for name in ('host-a', 'host-b')]
        for host in self.hosts:
            host.mkdir()
        self.receiver = self.root / 'receiver'
        self.receiver.mkdir()
        self.common = dict(model=PRIVATE + '-checkpoint', inputs=PRIVATE + '-prompt', numerics='fp64', scope='cache-off')
        self.sides = [PRIVATE + '-a', PRIVATE + '-b']
        self.ranks = {s: [0] for s in self.sides}
        self.positions = [dict(sample=PRIVATE + '-sample', position=i, history=[30, 31][:i+1]) for i in range(2)]
        self.target_rows = [dict(sample=row['sample'], position=row['position'] + 1, token=260) for row in self.positions]
        self.dist, self.score, self.arrays = [], [], []
        self.vocab = 257 + 10
        for idx, host in enumerate(self.hosts):
            dist = dict(schema_version=2, kind='distributions', common=self.common)
            score = dict(schema_version=1, kind='target-scores', common=self.common)
            shared = dict(side=self.sides[idx], expected_ranks=[0], ranks=[dict(
                rank=0, environment=PRIVATE + '-environment.json', status=PRIVATE + '-status.json')])
            put(host / (PRIVATE + '-environment.json'), dict(rank=0, versions={'runtime': PRIVATE}, actual=self.common))
            put(host / (PRIVATE + '-status.json'), dict(rank=0, status='completed', native_log=PRIVATE))
            weights = np.ones((2, self.vocab), dtype=np.float64)
            weights[0, idx:100 + idx] = 5
            weights[0, idx] = 10000
            weights[1, 100:] = 0
            weights[0, 260] = 0.25 + idx / 8
            # Nonzero low score for the approved target on a different row. A
            # common middle row has exact cutoff ties and a zero-mass complement.
            with np.errstate(divide='ignore'):
                values = np.log(weights / weights.sum(axis=1, keepdims=True))
            path = host / (PRIVATE + '-full-vocab.npy')
            np.save(path, values)
            self.arrays.append(path)
            local_dist = [dict(row, scores=dict(path=path.name, row=i)) for i, row in enumerate(self.positions)]
            self.dist.append((
                put(host / 'dist-contract.json', dict(dist, thresholds=None, expected_ranks=self.ranks,
                    vocab_size=self.vocab, normalization_tolerance=1e-6, renormalization='none', positions=self.positions)),
                put(host / 'dist-source.json', dict(dist, **shared, positions=local_dist, private_payload=PRIVATE)),
            ))
            # This selected target is outside both top lists at the first
            # prediction. The second selected position uses a finite score
            # from a distinct synthetic target capture.
            target_values = [float(values[0, 260]), -9.0 - idx / 2]
            self.score.append((
                put(host / 'target-contract.json', dict(score, thresholds=None, expected_ranks=self.ranks, targets=self.target_rows)),
                put(host / 'target-source.json', dict(score, **shared,
                    scores=[dict(row, logprob=value, private_payload=PRIVATE) for row, value in zip(self.target_rows, target_values)])),
            ))

    def grant(self, sources, kinds):
        infos = [exchange.inspect_source(*pair) for pair in sources]
        a, b = infos
        scope = ('modality', 'contract_sha256', 'row_count', 'policy')
        self.assertTrue(all(a[k] == b[k] for k in scope))
        value = dict(schema_version=1, kind='minimal-inference-grant', nonce='1' * 64,
                     **{k: a[k] for k in scope},
                     sources={str(info['side']): {k: info[k] for k in ('source_sha256', 'selection_sha256')} for info in infos},
                     transfers=([{'from': i, 'to': exchange.REVIEWER, 'kinds': list(kinds)} for i in range(2)] +
                                [{'from': i, 'to': 1-i, 'kinds': ['inventory']} for i in range(2)
                                 if 'inventory' in kinds]))
        return value, exchange.digest(value)

    def exchange_distributions(self):
        g = self.grant(self.dist, ['inventory', 'query'])
        ia, ib = [exchange.inventory(*g, *local) for local in self.dist]
        a, b = [exchange.query(*g, *local, own, peer)
                for local, own, peer in zip(self.dist, (ia, ib), (ib, ia))]
        return g, (ia, ib), (a, b)

    def compare_kwargs(self, g, queries):
        a, b = (exchange.inventory(*g, *side, to=exchange.REVIEWER) for side in self.dist)
        return dict(inventory_a=a, inventory_b=b,
                    query_a_sha256=queries[0]['payload_sha256'], query_b_sha256=queries[1]['payload_sha256'])

    def legacy(self, name, sources):
        output = self.receiver / f'legacy-{name}.json'
        subprocess.run([sys.executable, '-B', str(COMPARE / (name + '.py')),
                        '--contract', str(sources[0][0]), '--side-a', str(sources[0][1]),
                        '--side-b', str(sources[1][1]), '--output', str(output)], check=True, capture_output=True)
        return json.loads(output.read_bytes())

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', '-m', 'tools.minimal_exchange', *(str(a) for a in args)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_distributions_minimize_wire_and_match_native_comparator(self):
        g, inventories, queries = self.exchange_distributions()
        peer = inventories[1]
        self.assertEqual(peer['rows'][1], list(range(100)))
        self.assertEqual(len(queries[0]['rows'][0]['logprobs']), 101)
        self.assertIsNone(queries[0]['rows'][1]['tail_logmass'])
        self.assertNotIn(260, {token for entry in inventories for token in entry['rows'][0]})
        lp = np.load(self.arrays[0], allow_pickle=False)[0]
        excluded = set(inventories[0]['rows'][0]) | set(peer['rows'][0])
        self.assertAlmostEqual(math.exp(queries[0]['rows'][0]['tail_logmass']),
                               math.fsum(math.exp(v) for i, v in enumerate(lp) if i not in excluded))
        for payload in (*inventories, *queries):
            text = json.dumps(payload)
            for prohibited in (PRIVATE, '.npy', 'history', 'conditions', 'vocab', 'sample', 'status', 'environment'):
                self.assertNotIn(prohibited, text)
        report = exchange.compare(*g, *queries, **self.compare_kwargs(g, queries))
        native = self.legacy('compare_distributions', self.dist)
        self.assertEqual(report['position_count'], 2)
        self.assertEqual(report['aggregate']['top1_agreement'], native['aggregate']['top1_agreement'])
        for field in ('tail_merged_js', 'probability_rmse', 'max_probability_error'):
            self.assertAlmostEqual(report['aggregate'][field]['mean'], native['aggregate'][field]['mean'], places=14)
        self.assertEqual(report['positions'][0]['sample'], 'row')
        self.assertEqual(report['positions'][0]['top1_mismatch']['probabilities'][0]['token'], 0)
        using = put(self.receiver / 'dist-report.json', report)
        accepted = self.receiver / 'acceptance.json'
        call = subprocess.run([sys.executable, '-B', str(COMPARE / 'acceptance.py'), '--report', str(using),
                               '--sides', '0', '1', '--output', str(accepted), '--exit-zero'], capture_output=True, text=True)
        self.assertEqual(call.returncode, 0, call.stderr)
        verdict = json.loads(accepted.read_bytes())
        self.assertEqual(verdict['verdict'], 'fail')
        self.assertIn('decisive_top1_flips', verdict['failing'])

    def test_local_contract_labels_enable_native_repeat_acceptance(self):
        g, _, queries = self.exchange_distributions()
        contract, source = self.dist[0]
        flags = self.compare_kwargs(g, queries)
        with self.assertRaisesRegex(ValueError, 'local report contract differs'):
            exchange.compare(*g, *queries, **flags, local_report_contract=self.score[0][0])
        report = exchange.compare(*g, *queries, **flags, local_report_contract=contract)
        self.assertEqual(report['sides'], dict(side_a=self.sides[0], side_b=self.sides[1]))
        primary = put(self.receiver / 'primary.json', report)
        repeat_side = self.sides[0] + '-repeat'
        repeat_contract = json.loads(contract.read_bytes())
        repeat_contract['expected_ranks'] = {self.sides[0]: [0], repeat_side: [0]}
        repeat_contract = put(self.hosts[0] / 'repeat-contract.json', repeat_contract)
        repeat_source = put(self.hosts[0] / 'repeat-source.json', dict(json.loads(source.read_bytes()), side=repeat_side))
        repeat = put(self.receiver / 'repeat.json', self.legacy('compare_distributions',
                     ((repeat_contract, source), (repeat_contract, repeat_source))))
        output = self.receiver / 'native-repeat-acceptance.json'
        call = subprocess.run([sys.executable, '-B', str(COMPARE / 'acceptance.py'), '--report', str(primary),
                               '--noise-for', str(primary), str(repeat), '--sides', *self.sides,
                               '--output', str(output), '--exit-zero'], capture_output=True, text=True)
        self.assertEqual(call.returncode, 0, call.stderr)
        verdict = json.loads(output.read_bytes())
        self.assertFalse(verdict['unavailable'])
        js = next(check for check in verdict['checks'] if check['name'] == 'js_mean')
        self.assertEqual(js['noise'], 0)

    def test_targets_compare_only_preselected_scores_and_need_no_source_on_receiver(self):
        g = self.grant(self.score, ['targets'])
        payloads = [exchange.targets(*g, *local) for local in self.score]
        self.assertEqual(len(payloads[0]['rows']), 2)
        self.assertNotIn(PRIVATE, json.dumps(payloads))
        self.assertNotIn('token', json.dumps(payloads))
        grant = put(self.receiver / 'grant.json', g[0])
        a = put(self.receiver / 'score-a.json', payloads[0])
        b = put(self.receiver / 'score-b.json', payloads[1])
        result = self.receiver / 'score-report.json'
        call = self.cli('compare', '--grant', grant, '--grant-sha256', g[1], '--side-a', a, '--side-b', b, '--output', result)
        self.assertEqual(call.returncode, 0, call.stderr)
        report = json.loads(result.read_bytes())
        native = self.legacy('compare_target_scores', self.score)
        self.assertEqual(report['coverage'], {'expected': 2, 'paired': 2})
        self.assertEqual({k: v for k, v in report['metrics'].items() if k != 'max_coordinate'},
                         {k: v for k, v in native['metrics'].items() if k != 'max_coordinate'})
        forged = copy.deepcopy(payloads[1])
        forged['rows'][0] = -1
        with self.assertRaisesRegex(ValueError, 'selection commitment'):
            exchange.compare(*g, payloads[0], rehash(forged))
        source = json.loads(self.score[0][1].read_bytes())
        source['scores'][0]['token'] -= 1
        put(self.score[0][1], source)
        with self.assertRaisesRegex(ValueError, 'preselected token'):
            exchange.targets(*g, *self.score[0])

    def test_one_target_per_coordinate_requires_separate_reviewed_group(self):
        contract = json.loads(self.score[0][0].read_bytes())
        alternative = dict(contract['targets'][0], token=259)
        contract['targets'].append(alternative)
        put(self.score[0][0], contract)
        with self.assertRaisesRegex(ValueError, 'duplicate source coordinate'):
            exchange.inspect_source(*self.score[0])

    def test_explicit_grant_and_direction_are_mandatory(self):
        grant, approved = self.grant(self.dist, ['inventory', 'query'])
        restricted = copy.deepcopy(grant)
        restricted['transfers'][0]['kinds'].remove('query')
        with self.assertRaisesRegex(ValueError, 'trusted SHA256'):
            exchange.inventory(restricted, approved, *self.dist[0])
        token = exchange.digest(restricted)
        a, b = [exchange.inventory(restricted, token, *side) for side in self.dist]
        with self.assertRaisesRegex(ValueError, 'not granted'):
            exchange.query(restricted, token, *self.dist[0], a, b)
        restricted['transfers'].pop()
        with self.assertRaisesRegex(ValueError, 'not granted'):
            exchange.inventory(restricted, exchange.digest(restricted), *self.dist[1])
        output = self.receiver / 'denied.json'
        grant_path = put(self.receiver / 'grant.json', grant)
        result = self.cli('inventory', '--grant', grant_path, '--contract', self.dist[0][0],
                          '--manifest', self.dist[0][1], '--output', output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        output.write_text('do not replace')
        result = self.cli('inventory', '--grant', grant_path, '--grant-sha256', approved,
                          '--contract', self.dist[0][0], '--manifest', self.dist[0][1], '--output', output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(), 'do not replace')

    def test_scores_cannot_be_granted_or_addressed_to_peer(self):
        g, inventories, queries = self.exchange_distributions()
        self.assertEqual({m['to'] for m in queries}, {exchange.REVIEWER})
        self.assertEqual([m['to'] for m in inventories], [1, 0])
        flags = self.compare_kwargs(g, queries)
        with self.assertRaisesRegex(ValueError, 'wire destination'):
            exchange.compare(*g, *queries, **dict(flags, inventory_a=inventories[0]))
        wrong_copy = flags['inventory_b']
        with self.assertRaisesRegex(ValueError, 'wire destination'):
            exchange.query(*g, *self.dist[0], inventories[0], wrong_copy)
        peer_only = copy.deepcopy(g[0])
        peer_only['transfers'] = [t for t in peer_only['transfers'] if t['to'] != exchange.REVIEWER]
        pin = exchange.digest(peer_only)
        a, b = (exchange.inventory(peer_only, pin, *side) for side in self.dist)
        with self.assertRaisesRegex(ValueError, 'not granted'):
            exchange.query(peer_only, pin, *self.dist[0], a, b)
        with self.assertRaisesRegex(ValueError, 'not granted'):
            exchange.inventory(peer_only, pin, *self.dist[0], to=exchange.REVIEWER)
        peer_only['transfers'][0]['kinds'].append('query')
        with self.assertRaisesRegex(ValueError, 'scope for recipient'):
            exchange.inventory(peer_only, exchange.digest(peer_only), *self.dist[0])
        target_grant, _ = self.grant(self.score, ['targets'])
        target_grant['transfers'][0]['to'] = 1
        with self.assertRaisesRegex(ValueError, 'scope for recipient'):
            exchange.targets(target_grant, exchange.digest(target_grant), *self.score[0])

    def test_forged_peer_inventory_cannot_request_more_tokens(self):
        g, (a, b), _ = self.exchange_distributions()
        forged = copy.deepcopy(b)
        forged['rows'][0][0] = 260
        with self.assertRaisesRegex(ValueError, 'selection commitment'):
            exchange.query(*g, *self.dist[0], a, rehash(forged))
        extra = copy.deepcopy(b)
        extra['history'] = [42]
        with self.assertRaisesRegex(ValueError, 'wire payload fields'):
            exchange.query(*g, *self.dist[0], a, rehash(extra))
        with self.assertRaisesRegex(ValueError, 'does not address'):
            exchange.query(*g, *self.dist[0], b, a)

    def test_source_and_query_bindings_reject_changes(self):
        g, inventories, queries = self.exchange_distributions()
        a, b = queries
        flags = self.compare_kwargs(g, queries)
        forged = copy.deepcopy(b)
        forged['rows'][0]['logprobs'] = list(reversed(forged['rows'][0]['logprobs']))
        with self.assertRaisesRegex(ValueError, 'independently approved'):
            exchange.compare(*g, a, rehash(forged), **flags)
        forged = copy.deepcopy(b)
        forged['peer_selection_sha256'] = 'a' * 64
        rehash(forged)
        with self.assertRaisesRegex(ValueError, 'not reciprocal'):
            exchange.compare(*g, a, forged, **dict(flags, query_b_sha256=forged['payload_sha256']))
        with self.assertRaisesRegex(ValueError, 'side aliases'):
            exchange.compare(*g, b, a, **flags)
        # Change source bytes without changing its selected top-100 or numerical rows.
        with self.arrays[0].open('ab') as stream:
            stream.write(b'extra unapproved bytes')
        with self.assertRaisesRegex(ValueError, 'source does not match grant'):
            exchange.query(*g, *self.dist[0], *inventories)
        contract = json.loads(self.dist[1][0].read_bytes())
        contract['positions'][0]['history'][0] += 1
        put(self.dist[1][0], contract)
        with self.assertRaisesRegex(ValueError, 'approved contract'):
            exchange.inventory(*g, *self.dist[1])

    def test_refuses_union_that_would_reconstruct_full_vocabulary(self):
        for idx, ((contract_path, _), array) in enumerate(zip(self.dist, self.arrays)):
            contract = json.loads(contract_path.read_bytes())
            contract['vocab_size'] = 201
            put(contract_path, contract)
            weights = np.ones((2, 201))
            weights[:, idx * 100:(idx + 1) * 100] = 5
            np.save(array, np.log(weights / weights.sum(axis=1, keepdims=True)))
        g = self.grant(self.dist, ['inventory', 'query'])
        a, b = (exchange.inventory(*g, *side) for side in self.dist)
        with self.assertRaisesRegex(ValueError, 'effectively full-vocabulary'):
            exchange.query(*g, *self.dist[0], a, b)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Validate published evaluation/repair histories and evidence links (stdlib only)."""
import datetime
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from tools.environment_receipt import validate_receipt
from .content import validate_content
from .models import model_facets


def validate(site):
    data = json.loads((site / 'data/repairs.json').read_text(encoding='utf-8'))
    if data['schema_version'] != 1:
        raise ValueError('Unsupported history schema')
    datetime.date.fromisoformat(data['updated_at'])
    ids = set()
    for repair in data['repairs']:
        if 'content' in repair:
            validate_content(repair['content'])
        identity = repair['id']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identity) or identity in ids:
            raise ValueError('Repair IDs must be unique stable slugs')
        ids.add(identity)
        datetime.date.fromisoformat(repair['date'])
        for key in ('title', 'status', 'model', 'environment', 'problem', 'cause',
                    'repair', 'stages', 'validation', 'evidence', 'notes'):
            if key not in repair:
                raise ValueError(f'{identity}: missing {key}')
        if repair['status'] not in ('investigating', 'patched', 'validated'):
            raise ValueError(f'{identity}: invalid status')
        for section, keys in {'model': ('name', 'revision'),
                              'environment': ('hardware', 'libraries'),
                              'problem': ('summary', 'trigger', 'expected', 'observed', 'location'),
                              'cause': ('category', 'summary', 'components'),
                              'repair': ('summary', 'commit', 'upstream_status'),
                              'validation': ('scope', 'coverage', 'metrics', 'checks', 'acceptance', 'remaining')}.items():
            for key in keys:
                if key not in repair[section]:
                    raise ValueError(f'{identity}: missing {section}.{key}')
        runtime_settings = repair['environment'].get('settings')
        if isinstance(runtime_settings, dict) and 'runtime_environment' in runtime_settings:
            validate_receipt(runtime_settings['runtime_environment'])
        coverage = repair['validation']['coverage']
        model_facets(repair['model'])
        observed, expected = coverage['observed'], coverage['expected']
        if (type(observed) is not int or type(expected) is not int
                or not 0 <= observed <= expected or expected <= 0 or not coverage['unit']):
            raise ValueError(f'{identity}: invalid coverage')
        metric_ids = set()
        for metric in repair['validation']['metrics']:
            if metric['id'] in metric_ids or not metric['label'] or not metric['unit']:
                raise ValueError(f'{identity}: invalid metric identity or unit')
            metric_ids.add(metric['id'])
            for field in ('before', 'after'):
                value = metric[field]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError(f'{identity}: invalid error metric')
        for evidence in repair['evidence']:
            url = urlsplit(evidence['url'])
            if url.scheme:
                if url.scheme != 'https' or not url.netloc:
                    raise ValueError(f'{identity}: invalid evidence URL')
            else:
                target = (site / url.path).resolve()
                if url.netloc or not target.is_relative_to(site.resolve()) or not target.is_file():
                    raise ValueError(f'{identity}: missing or escaped evidence')
    groups = {}
    for group in data.get('library_groups', []):
        identity = group['id']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identity) or identity in groups:
            raise ValueError('Library group IDs must be unique stable slugs')
        components = group['components']
        if (not group['title'] or not isinstance(components, list) or not components
                or any(not isinstance(name, str) or not name.strip() for name in components)
                or len(set(components)) != len(components)):
            raise ValueError('Library groups need a title and distinct component names')
        groups[identity] = group
    records = {record['id']: record for record in data['repairs']}
    for record in records.values():
        root, seen = record, set()
        while root.get('related_repair_id') is not None:
            if root['id'] in seen:
                raise ValueError('Cyclic repair relationship')
            seen.add(root['id'])
            parent = root['related_repair_id']
            if not isinstance(parent, str) or parent not in records:
                raise ValueError('Missing parent repair')
            root = records[parent]
        group = groups.get(root.get('library_group_id'))
        if group is None:
            raise ValueError('Independent fixes require a declared library group')
        if record.get('library_group_id', group['id']) != group['id']:
            raise ValueError('Follow-up library group differs from its repair')
        if root.get('affected_library') and root['affected_library'] not in group['components']:
            raise ValueError('Affected library must belong to the declared group')
    return len(ids)


def validate_recommendation_eligible(record):
    """Validate the optional evaluation recommendation veto without defaulting it."""
    if type(record.get('recommendation_eligible', True)) is not bool:
        raise ValueError(f'{record["id"]}: recommendation_eligible must be a boolean')


def validate_evaluations(site):
    """Check peer identities, measured values, coverage and immutable provenance."""
    data = json.loads((site / 'data/evaluations.json').read_text(encoding='utf-8'))
    if data['schema_version'] != 1:
        raise ValueError('Unsupported evaluation schema')
    datetime.date.fromisoformat(data['updated_at'])
    ids = set()
    def slug(identity, used):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identity) or identity in used:
            raise ValueError('Evaluation identities must be distinct stable slugs')
        used.add(identity)
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    for record in data['evaluations']:
        if 'content' in record:
            validate_content(record['content'])
        slug(record['id'], ids)
        datetime.date.fromisoformat(record['date'])
        for name in ('title', 'task', 'status', 'acceptance', 'model', 'summary', 'sides',
                     'common', 'differences', 'comparisons', 'notes', 'evidence', 'archive'):
            if name not in record:
                raise ValueError(f'{record["id"]}: missing {name}')
        if record['task'] not in ('inference', 'sft', 'rl') or record['status'] not in ('completed', 'partial'):
            raise ValueError('Invalid evaluation task/status')
        if not record['title'] or not record['summary'] or not all(record['model'].get(k) for k in ('name', 'revision')):
            raise ValueError('Evaluation needs title, summary and model identity')
        model_facets(record['model'])
        if record['acceptance'] is not None and not isinstance(record['acceptance'], (str, dict)):
            raise ValueError('Invalid numerical acceptance')
        validate_recommendation_eligible(record)
        for key in ('common', 'differences', 'notes'):
            if not isinstance(record[key], list) or any(not isinstance(x, str) or not x.strip() for x in record[key]):
                raise ValueError('Evaluation conditions and notes must be text lists')
        sides = set()
        for side in record['sides']:
            slug(side['id'], sides)
            if not side['label'] or not side['hardware'] or not side['libraries'] or not isinstance(side['settings'], dict):
                raise ValueError('Each evaluation side needs its actual configuration')
            if any(not lib.get('name') or not lib.get('version') for lib in side['libraries']):
                raise ValueError('Evaluation libraries need actual names/versions')
            if 'runtime_environment' in side['settings']:
                validate_receipt(side['settings']['runtime_environment'])
        if len(sides) < 2 or not record['comparisons']:
            raise ValueError('Evaluation needs at least two sides and a comparison')
        comparisons = set()
        for comparison in record['comparisons']:
            slug(comparison['id'], comparisons)
            pair = comparison['side_ids']
            if len(pair) != 2 or len(set(pair)) != 2 or not set(pair) <= sides:
                raise ValueError('Comparison must reference two distinct declared sides')
            if not comparison['title'] or not comparison['scope'] or not comparison['metrics'] or not comparison['coverage']:
                raise ValueError('Comparison needs scope, metrics and coverage')
            metrics = set()
            for metric in comparison['metrics']:
                slug(metric['id'], metrics)
                if not metric['label'] or not metric['unit'] or not finite(metric['value']):
                    raise ValueError('Evaluation metrics need a unit and finite numeric value')
                if 'side_values' in metric and (set(metric['side_values']) != set(pair) or
                        not all(finite(v) for v in metric['side_values'].values())):
                    raise ValueError('Metric side values must match the compared sides')
                if 'coordinate' in metric and not isinstance(metric['coordinate'], dict):
                    raise ValueError('Maximum-error coordinate must be an object')
            for coverage in comparison['coverage']:
                observed, expected = coverage['observed'], coverage['expected']
                if (type(observed) is not int or type(expected) is not int or
                        not 0 <= observed <= expected or expected <= 0 or not coverage['label'] or not coverage['unit']):
                    raise ValueError('Invalid evaluation coverage')
        archive = record['archive']
        if archive is not None and (
                not isinstance(archive, dict) or not isinstance(archive.get('revision'), str) or
                not re.fullmatch('[a-f0-9]{40}', archive['revision']) or
                not isinstance(archive.get('prefix'), str) or not archive['prefix'].strip() or
                type(archive.get('replay_cases')) is not int or archive['replay_cases'] <= 0):
            raise ValueError('Evaluation archive needs a pinned revision and replay count')
        if not record['evidence']:
            raise ValueError('Evaluation needs published evidence')
        for evidence in record['evidence']:
            url = urlsplit(evidence['url'])
            if not evidence['label']:
                raise ValueError('Evidence needs a label')
            if url.scheme:
                if url.scheme != 'https' or not url.netloc or url.username or url.password:
                    raise ValueError('Invalid evaluation evidence URL')
            else:
                target = (site / url.path).resolve()
                if url.netloc or not target.is_relative_to(site.resolve()) or not target.is_file():
                    raise ValueError('Missing or escaped evaluation evidence')
    return len(ids)


def _check_localized(value, context):
    if not isinstance(value, dict) or set(value) - {'zh', 'en'}:
        raise ValueError(f'{context}: localized text must map zh/en strings')
    if not all(isinstance(value.get(lang), str) and value[lang].strip() for lang in ('zh', 'en')):
        raise ValueError(f'{context}: localized text requires non-empty zh and en')


def _check_https_url(raw, context):
    url = urlsplit(raw or '')
    if url.scheme != 'https' or not url.netloc or url.username or url.password:
        raise ValueError(f'{context}: invalid HTTPS URL')


def validate_upstream(site):
    """Check upstream PR contributions, append-only status events and evidence links."""
    path = site / 'data/upstream.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported upstream contribution schema')
    datetime.date.fromisoformat(data['updated_at'])
    contributions = data.get('contributions')
    events = data.get('events')
    if not isinstance(contributions, list) or not isinstance(events, list):
        raise ValueError('upstream.json requires contributions and events lists')
    case_ids, eval_ids, repair_ids = set(), set(), set()
    relations_path = site / 'data/relations.json'
    if relations_path.is_file():
        case_ids = {e['id'] for e in json.loads(relations_path.read_text(encoding='utf-8')).get('entities', [])
                    if e.get('kind') == 'case'}
    evals_path = site / 'data/evaluations.json'
    if evals_path.is_file():
        eval_ids = {e['id'] for e in json.loads(evals_path.read_text(encoding='utf-8')).get('evaluations', [])}
    repairs_path = site / 'data/repairs.json'
    if repairs_path.is_file():
        repair_ids = {r['id'] for r in json.loads(repairs_path.read_text(encoding='utf-8')).get('repairs', [])}
    ids = set()
    for item in contributions:
        identity = item.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', identity) or identity in ids:
            raise ValueError('Upstream contribution IDs must be unique stable slugs')
        ids.add(identity)
        for field in ('project', 'repository', 'number'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f'{identity}: missing {field}')
        if item.get('forge') not in ('github', 'gitcode', 'gitlab'):
            raise ValueError(f'{identity}: invalid forge')
        _check_https_url(item.get('url'), f'{identity}.url')
        if item.get('task') not in (None, 'inference', 'sft', 'rl'):
            raise ValueError(f'{identity}: invalid task')
        hardware = item.get('hardware')
        if not isinstance(hardware, list) or any(not isinstance(h, str) or not h.strip() for h in hardware):
            raise ValueError(f'{identity}: hardware must be a list of strings')
        if item.get('discovered_at') is not None:
            datetime.date.fromisoformat(item['discovered_at'])
        _check_localized(item.get('title'), f'{identity}.title')
        _check_localized(item.get('summary'), f'{identity}.summary')
        if item.get('case_id') is not None and case_ids and item['case_id'] not in case_ids:
            raise ValueError(f'{identity}: unknown case_id {item["case_id"]}')
        if item.get('comparison_id') is not None and eval_ids and item['comparison_id'] not in eval_ids:
            raise ValueError(f'{identity}: unknown comparison_id {item["comparison_id"]}')
        if item.get('repair_id') is not None and repair_ids and item['repair_id'] not in repair_ids:
            raise ValueError(f'{identity}: unknown repair_id {item["repair_id"]}')
        verification = item.get('verification')
        if verification is not None:
            if not isinstance(verification, dict):
                raise ValueError(f'{identity}: verification must be an object')
            _check_localized(verification.get('scope'), f'{identity}.verification.scope')
            if verification.get('note') is not None:
                _check_localized(verification['note'], f'{identity}.verification.note')
            metric_ids = set()
            for metric in verification.get('metrics', []):
                mid = metric.get('id')
                if not isinstance(mid, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', mid) or mid in metric_ids:
                    raise ValueError(f'{identity}: invalid verification metric id')
                metric_ids.add(mid)
                _check_localized(metric.get('label'), f'{identity}.verification.metrics.{mid}.label')
                if not isinstance(metric.get('after'), str) or not metric['after'].strip():
                    raise ValueError(f'{identity}: verification metric {mid} needs after value')
                if not isinstance(metric.get('unit'), str) or not metric['unit'].strip():
                    raise ValueError(f'{identity}: verification metric {mid} needs unit')
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f'{identity}: upstream contribution needs evidence')
        for entry in evidence:
            label = entry.get('label')
            if isinstance(label, dict):
                _check_localized(label, f'{identity}.evidence.label')
            elif not isinstance(label, str) or not label.strip():
                raise ValueError(f'{identity}: evidence needs a label')
            url = urlsplit(entry.get('url') or '')
            if url.scheme:
                _check_https_url(entry.get('url'), f'{identity}.evidence')
            else:
                target = (site / url.path).resolve()
                if url.netloc or not target.is_relative_to(site.resolve()) or not target.is_file():
                    raise ValueError(f'{identity}: missing or escaped evidence {entry.get("url")}')
    event_ids = set()
    by_contribution = {identity: [] for identity in ids}
    for event in events:
        eid = event.get('id')
        if not isinstance(eid, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', eid) or eid in event_ids:
            raise ValueError('Upstream event IDs must be unique stable slugs')
        event_ids.add(eid)
        cid = event.get('contribution_id')
        if cid not in ids:
            raise ValueError(f'{eid}: unknown contribution_id {cid}')
        if event.get('status') not in ('submitted', 'merged', 'released', 'closed'):
            raise ValueError(f'{eid}: invalid status')
        event_date = datetime.date.fromisoformat(event['date'])
        datetime.date.fromisoformat(event['observed_at'])
        _check_https_url(event.get('url'), f'{eid}.url')
        commit = event.get('commit')
        if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{7,40}', commit)):
            raise ValueError(f'{eid}: commit must be a 7-40 hex SHA')
        release = event.get('release')
        if release is not None and (not isinstance(release, str) or not release.strip()):
            raise ValueError(f'{eid}: release must be a non-empty string')
        by_contribution[cid].append((event_date, event['status']))
    for cid, entries in by_contribution.items():
        if not any(status == 'submitted' for _, status in entries):
            raise ValueError(f'{cid}: missing submitted event')
        dates = [d for d, _ in entries]
        if dates != sorted(dates):
            raise ValueError(f'{cid}: events must be in chronological order')
    return len(ids)


def main():
    site = Path(__file__).resolve().parents[1] / 'site'
    count = validate(site)
    evaluations = validate_evaluations(site)
    upstream = validate_upstream(site) if (site / 'data/upstream.json').is_file() else 0
    print(f'Validated {count} repair verification records, {evaluations} evaluations and {upstream} upstream contributions with evidence links.')


if __name__ == '__main__':
    raise SystemExit(main())

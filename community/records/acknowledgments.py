"""Extract public credit from a reviewed inbox receipt; never publish the receipt."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .catalog import Catalog
from .submission import attribution, draft_attribution, encode


def entry(site, receipt, case_id, zh, en):
    raw = receipt.read_bytes()
    if receipt.name != f'submission-{sha256(raw).hexdigest()}.json':
        raise ValueError('Receipt filename does not match its content')
    draft = json.loads(raw)
    credit = draft_attribution(draft)
    if credit['mode'] == 'anonymous':
        return None
    bundle = Catalog(site).case_bundle(case_id)
    additions = draft['additions']
    records = [bundle['case'], *bundle['records']]
    accepted = {row['record']['id'] for row in records}
    submitted = {row['id'] for group in ('evaluations', 'repairs', 'relations')
                 for key, rows in additions[group].items() if key in ('evaluations', 'repairs', 'entities')
                 for row in rows}
    if not accepted.intersection(submitted):
        raise ValueError('Case does not include a record from this submission')
    return dict(case_id=case_id, mode=credit['mode'], name=credit['name'],
                contribution={'zh': description(zh), 'en': description(en)})


def description(text):
    if (not isinstance(text, str) or not 1 <= len(text) <= 160 or text != text.strip()
            or not text.isprintable()):
        raise ValueError('Use a short, single-line contribution description')
    return text


def validate(site, catalog):
    path = site / 'data/acknowledgments.json'
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('Invalid acknowledgments catalog')
    seen = set()
    for item in data['entries']:
        mode, name, case_id = item['mode'], item['name'], item['case_id']
        if set(item) != {'case_id', 'mode', 'name', 'contribution'} or mode not in ('github', 'nickname'):
            raise ValueError('Only public credit entries may be published')
        attribution(mode, name)
        catalog.get('case', case_id)
        if set(item['contribution']) != {'zh', 'en'}:
            raise ValueError('Credit needs Chinese and English descriptions')
        for text in item['contribution'].values():
            description(text)
        identity = (case_id, mode, name.casefold() if mode == 'github' else name)
        if identity in seen:
            raise ValueError('Duplicate published credit for this case')
        seen.add(identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--site', type=Path, default=Path(__file__).resolve().parents[1] / 'site')
    parser.add_argument('--case', required=True)
    parser.add_argument('--zh', required=True)
    parser.add_argument('--en', required=True)
    args = parser.parse_args()
    try:
        credit = entry(args.site, args.receipt, args.case, args.zh, args.en)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(encode(credit).decode(), end='')


if __name__ == '__main__':
    main()

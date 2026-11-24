"""Refresh a read-only status feed for explicitly published public GitHub PRs."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PR = re.compile(r'https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,11})/?', re.I)
SHA = re.compile(r'[0-9a-f]{40}')
PUBLIC_FIELDS = ('state', 'checks', 'updated_at', 'fetched_at', 'checks_fetched_at')
DEFAULT_REGISTRY = Path(__file__).parent / 'site/data/upstream.json'
CHECK_INTERVAL = timedelta(minutes=6)


class NotPublic(Exception):
    pass


def canonical_pr(url):
    match = PR.fullmatch(url) if isinstance(url, str) else None
    if match is None:
        return None
    owner, repo, number = match.groups()
    if repo in ('.', '..'):
        return None
    return f'https://github.com/{owner.lower()}/{repo.lower()}/pull/{number}'


def allowlist(registry):
    doc = json.loads(Path(registry).read_bytes())
    if doc.get('schema_version') != 1 or not isinstance(doc.get('contributions'), list):
        raise ValueError('Expected a version 1 published upstream registry')
    return sorted({url for item in doc['contributions'] if item.get('forge') == 'github'
                   if (url := canonical_pr(item.get('url')))})


def clock(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (AttributeError, ValueError):
        return None


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def github(path):
    # No credentials: a private or deleted PR must never become part of the feed.
    req = Request('https://api.github.com' + path, headers={'Accept': 'application/vnd.github+json',
                  'User-Agent': 'NumericBridge-public-pr-status', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urlopen(req, timeout=12) as response:
            return json.loads(response.read(8_000_001))
    except HTTPError as error:
        if error.code in (404, 410):
            raise NotPublic from error
        raise


def checks_category(payload):
    rows = payload.get('check_runs')
    if not isinstance(rows, list) or not rows:
        return 'unknown'
    negative = {'failure', 'timed_out', 'cancelled', 'action_required', 'startup_failure', 'stale'}
    if any(row.get('conclusion') in negative for row in rows):
        return 'failing'
    if any(row.get('status') != 'completed' for row in rows):
        return 'pending'
    if payload.get('total_count', len(rows)) > len(rows):
        return 'unknown'  # Don't presume success from a partial page.
    positive = {'success', 'neutral', 'skipped'}
    return 'passing' if any(row.get('conclusion') == 'success' for row in rows) and all(
        row.get('conclusion') in positive for row in rows) else 'unknown'


def _pr_path(url):
    owner, repo, number = PR.fullmatch(url).groups()
    return f'/repos/{owner}/{repo}', int(number)


def collect(urls, previous, now, *, client=github, budget=2):
    """Use an explicit per-run budget, rotating over least-recently verified URLs."""
    urls = set(urls)
    entries = {key: value for key, value in previous.items() if key in urls and isinstance(value, dict)}
    attempts = 0
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(urls, key=lambda url: (clock(entries.get(url, {}).get('attempted_at')) or oldest, url))
    for url in ordered:
        if attempts >= budget:
            break
        old = entries.get(url, {})
        path, number = _pr_path(url)
        attempted = stamp(now)
        attempts += 1
        try:
            info = client(f'{path}/pulls/{number}')
            # GitHub's public answer, not the ledger, is the authority for forge state.
            if info.get('number') != number or canonical_pr(info.get('html_url')) != url:
                entries.pop(url, None)
                continue
            base_repo = (info.get('base') or {}).get('repo') or {}
            if base_repo.get('private') is not False:
                entries.pop(url, None)
                continue
            updated = clock(info.get('updated_at'))
            if info.get('state') not in ('open', 'closed') or not updated:
                raise ValueError('Incomplete PR status')
            state = ('merged' if info.get('merged_at') else 'closed' if info['state'] == 'closed' else
                     'draft' if info.get('draft') else 'open')
        except NotPublic:
            entries.pop(url, None)
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            entries[url] = dict(old, attempted_at=attempted)
            continue
        head = (info.get('head') or {}).get('sha', '')
        same_head = isinstance(head, str) and SHA.fullmatch(head) and old.get('head') == head
        row = dict(state=state, updated_at=stamp(updated), fetched_at=attempted, attempted_at=attempted,
                   checks=old.get('checks', 'unknown') if same_head else 'unknown',
                   checks_fetched_at=old.get('checks_fetched_at') if same_head else None,
                   head=head if isinstance(head, str) and SHA.fullmatch(head) else None)
        last_check = clock(row['checks_fetched_at'])
        if (attempts < budget and row['head'] and state in ('open', 'draft')
                and (not last_check or now - last_check >= CHECK_INTERVAL)):
            attempts += 1
            try:
                key = row['head']
                row['checks'] = checks_category(client(f'{path}/commits/{key}/check-runs?filter=latest&per_page=100'))
                row['checks_fetched_at'] = attempted
            except (NotPublic, OSError, TypeError, ValueError, AttributeError):
                pass
        entries[url] = row
    return entries


def read_cache(path):
    try:
        data = json.loads(path.read_bytes())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path, data, mode):
    with NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            os.fchmod(handle.fileno(), mode)
            handle.write((json.dumps(data, ensure_ascii=True, sort_keys=True) + '\n').encode())
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def refresh(registry, cache, output, *, now=None, client=github, budget=2):
    now = now or datetime.now(timezone.utc)
    entries = collect(allowlist(registry), read_cache(cache), now, client=client, budget=budget)
    public = {key: {field: row.get(field) for field in PUBLIC_FIELDS} for key, row in entries.items()
              if row.get('state') and clock(row.get('fetched_at'))}
    write_json(cache, entries, 0o600)
    write_json(output, {'schema_version': 1, 'refreshed_at': stamp(now), 'pull_requests': public}, 0o644)
    return len(public)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.cache.resolve() == args.output.resolve():
        parser.error('The private cache and public output must be different files')
    print(f'Published GitHub PRs with verified status: {refresh(args.registry, args.cache, args.output)}')


if __name__ == '__main__':
    main()

"""Refresh the published PR status feed and an optional admin-only PR watch feed."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


GITHUB_PR = re.compile(r'https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})/pull/([1-9][0-9]{0,11})/?', re.I | re.ASCII)
GITCODE_PR = re.compile(r'https://gitcode\.com/([A-Za-z0-9_-]{1,100})/([A-Za-z0-9_.-]{1,100})/merge_requests/([1-9][0-9]{0,11})', re.I | re.ASCII)
FORGES = {'github': (GITHUB_PR, 'pull'), 'gitcode': (GITCODE_PR, 'merge_requests')}
SHA = re.compile(r'[0-9a-f]{40}')
PUBLIC_FIELDS = ('state', 'checks', 'updated_at', 'fetched_at', 'checks_fetched_at')
DEFAULT_REGISTRY = Path(__file__).parent / 'site/data/upstream.json'
CHECK_INTERVAL = timedelta(minutes=6)


class NotPublic(Exception):
    pass


def canonical_pr(url, forge=None):
    if not isinstance(url, str):
        return None
    for host, (pattern, route) in FORGES.items():
        if forge is not None and host != forge:
            continue
        if match := pattern.fullmatch(url):
            owner, repo, number = match.groups()
            if repo not in ('.', '..'):
                return f'https://{host}.com/{owner.lower()}/{repo.lower()}/{route}/{number}'
    return None


def allowlist(registry, *, tracking=False):
    doc = json.loads(Path(registry).read_bytes())
    if (not isinstance(doc, dict) or type(doc.get('schema_version')) is not int
            or doc['schema_version'] != 1 or not isinstance(doc.get('contributions'), list)):
        kind = 'tracking targets' if tracking else 'published upstream'
        raise ValueError(f'Expected a version 1 {kind} registry')
    urls = set()
    for item in doc['contributions']:
        if not isinstance(item, dict):
            raise ValueError('Expected PR registry entries to be objects')
        forge = item.get('forge')
        url = canonical_pr(item.get('url'), forge) if isinstance(forge, str) and forge in FORGES else None
        if url:
            urls.add(url)
        elif tracking:
            raise ValueError('Tracking targets must be valid GitHub or GitCode PR URLs matching their forge')
    return sorted(urls)


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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gitcode(path):
    # No credentials or redirects: only the anonymous GitCode API is authoritative.
    req = Request('https://api.gitcode.com/api/v5' + path, headers={
        'Accept': 'application/json', 'User-Agent': 'NumericBridge-public-pr-status'})
    try:
        with build_opener(_NoRedirect()).open(req, timeout=12) as response:
            return json.loads(response.read(8_000_001))
    except HTTPError as error:
        if 300 <= error.code < 400 or error.code in (401, 403, 404, 410):
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


def _rotation(urls, previous):
    entries = {key: value for key, value in previous.items() if key in urls and isinstance(value, dict)}
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(urls, key=lambda url: (clock(entries.get(url, {}).get('attempted_at')) or oldest, url))
    return entries, ordered


def _collect_github(urls, previous, now, client, budget):
    entries, ordered = _rotation(urls, previous)
    attempts = 0
    for url in ordered:
        if attempts >= budget:
            break
        old = entries.get(url, {})
        owner, repo, number_text = GITHUB_PR.fullmatch(url).groups()
        path, number = f'/repos/{owner}/{repo}', int(number_text)
        attempted = stamp(now)
        attempts += 1
        try:
            info = client(f'{path}/pulls/{number}')
            # GitHub's public answer, not the ledger, is the authority for forge state.
            if info.get('number') != number or canonical_pr(info.get('html_url'), 'github') != url:
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


def _collect_gitcode(urls, previous, now, client, budget):
    entries, ordered = _rotation(urls, previous)
    attempted = stamp(now)
    for url in ordered[:max(0, budget)]:
        owner, repo, number_text = GITCODE_PR.fullmatch(url).groups()
        repository = f'{owner}/{repo}'
        number = int(number_text)
        old = entries.get(url, {})
        try:
            info = client(f'/repos/{repository}/pulls/{number}')
            base_repo = (info.get('base') or {}).get('repo') or {}
            full_name = base_repo.get('full_name')
            # GitCode's PR detail omits base.repo.private; use its explicit public
            # visibility marker as well as the anonymous response and exact identity.
            if (info.get('number') != number or canonical_pr(info.get('html_url'), 'gitcode') != url
                    or info.get('visibility_reason') != 'public'
                    or not isinstance(full_name, str) or not full_name.isascii() or full_name.lower() != repository
                    or base_repo.get('private') is True or base_repo.get('internal') is True
                    or base_repo.get('visibility') not in (None, 'public')):
                raise NotPublic
            updated = clock(info.get('updated_at'))
            state = info.get('state')
            if state not in ('open', 'closed', 'merged') or not updated:
                raise ValueError('Incomplete PR status')
            if state == 'closed' and clock(info.get('merged_at')):
                state = 'merged'
            elif state == 'open' and info.get('draft') is True:
                state = 'draft'
        except NotPublic:
            entries.pop(url, None)
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            entries[url] = dict(old, attempted_at=attempted)
            continue
        # GitCode's status response is not an authoritative CI verdict.
        entries[url] = dict(state=state, updated_at=stamp(updated), fetched_at=attempted,
                            attempted_at=attempted, checks='unknown', checks_fetched_at=None)
    return entries


def collect(urls, previous, now, *, client=github, budget=2, gitcode_client=gitcode, gitcode_budget=2):
    """Rotate over least-recently attempted URLs with independent provider budgets."""
    urls = {url for url in urls if isinstance(url, str)}
    github_urls = {url for url in urls if canonical_pr(url, 'github') == url}
    gitcode_urls = {url for url in urls if canonical_pr(url, 'gitcode') == url}
    return {**_collect_github(github_urls, previous, now, client, budget),
            **_collect_gitcode(gitcode_urls, previous, now, gitcode_client, gitcode_budget)}


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


def _validate_paths(**paths):
    resolved, identities = {}, {}
    for name, path in paths.items():
        if path is None:
            continue
        key = f'--{name.replace("_", "-")}'
        try:
            canonical = Path(path).resolve()
        except RuntimeError as error:
            raise ValueError(f'Cannot resolve {key}') from error
        if canonical in resolved:
            raise ValueError(f'{resolved[canonical]} and {key} must be different files')
        resolved[canonical] = key
        try:
            info = canonical.stat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'{key} must be a regular file')
        identity = info.st_dev, info.st_ino
        if identity in identities:
            raise ValueError(f'{identities[identity]} and {key} must be different files')
        identities[identity] = key


def refresh(registry, cache, output, *, tracking_targets=None, admin_output=None,
            now=None, client=github, budget=2, gitcode_client=gitcode, gitcode_budget=2):
    if (tracking_targets is None) != (admin_output is None):
        raise ValueError('--tracking-targets and --admin-output must be supplied together')
    _validate_paths(registry=registry, cache=cache, output=output,
                    tracking_targets=tracking_targets, admin_output=admin_output)
    published = set(allowlist(registry))
    urls = published if tracking_targets is None else published.union(allowlist(tracking_targets, tracking=True))
    now = now or datetime.now(timezone.utc)
    entries = collect(urls, read_cache(cache), now, client=client, budget=budget,
                      gitcode_client=gitcode_client, gitcode_budget=gitcode_budget)
    verified = {key: {field: row.get(field) for field in PUBLIC_FIELDS} for key, row in entries.items()
                if row.get('state') and clock(row.get('fetched_at'))}
    # Only the current published registry grants public visibility, never the cache or watch list.
    public = {url: row for url, row in verified.items() if url in published}
    feed = {'schema_version': 1, 'refreshed_at': stamp(now)}
    if admin_output is not None:  # A failed admin write must not advance the public feed.
        write_json(admin_output, dict(feed, pull_requests=verified), 0o644)
    write_json(cache, entries, 0o600)
    write_json(output, dict(feed, pull_requests=public), 0o644)
    return len(public)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='Public output containing only published PRs')
    parser.add_argument('--tracking-targets', type=Path, help='Separate v1 registry of admin watch targets')
    parser.add_argument('--admin-output', type=Path, help='Unserved output containing the verified union')
    args = parser.parse_args()
    try:
        count = refresh(args.registry, args.cache, args.output, tracking_targets=args.tracking_targets,
                        admin_output=args.admin_output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Published PRs with verified status: {count}')


if __name__ == '__main__':
    main()

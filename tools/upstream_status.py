#!/usr/bin/env python3
"""Check whether merged upstream code still contains a finding, per ref, from a local clone.
Call: check(repo, refs, paths=(), pattern=None, patch=None, log_grep=()); CLI: python scripts/upstream_status.py
      --repo CLONE --ref main --ref v0.34.0 --ref releases/v0.18.0 [--path FILE ...] [--pattern REGEX]
      [--patch FIX.patch] [--log-grep TEXT ...] [--fetch] [--output NEW.json]
Input: a local clone of the owning repository (fetch it first, or pass --fetch), the refs to
check (current main, latest release, the user's pinned branch or tag) and how to recognize
the finding: a regex over the named files and/or the patch that fixes it.
Output: per ref {commit, present, files, matches, applies, reverse_applies, merged_commits} and a
suggested state per ref (present|fixed|absent|unknown) for diagnosis_state.py decide.
Requires: git on PATH; standard library.

Details:
present: the pattern matches any listed file at that ref (null without --pattern).
applies: `git apply --check` of the patch on that ref's tree (the fix is still needed there).
reverse_applies: the patch is already in that tree (fixed). Only merged history is read:
open issues and unmerged PRs are never consulted. Use authoritative upstream
refs: a short tag selects the tag, and a short branch selects the origin-tracking
branch before a local branch. A full ref or commit explicitly selects that object.
merged_commits lists first-parent commits on the ref whose message matches --log-grep
(for example the function or symptom), newest first, to cite the fixing merge.
Suggested state: fixed when reverse_applies or the pattern disappears after
appearing on another checked ref; present when the pattern
matches or the patch applies; absent when the files do not exist; unknown otherwise.
CLI exit: 0=checked, 2=input error.
"""

__all__ = ['check']
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def _git(repo, *args, payload=None, check=True):
    result = subprocess.run(['git', '-C', str(repo), *args], input=payload, capture_output=True, text=True,
                            timeout=300)
    if check and result.returncode:
        raise ValueError(f"git {' '.join(args)}: {result.stderr.strip()[:300]}")
    return result


def _applies(repo, commit, patch, reverse=False):
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / 'index'
        env_args = ['-c', 'core.quotepath=off']
        subprocess.run(['git', '-C', str(repo), *env_args, 'read-tree', commit],
                       env={'GIT_INDEX_FILE': str(index), 'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin'},
                       capture_output=True, check=True, timeout=300)
        argv = ['git', '-C', str(repo), 'apply', '--check', '--cached'] + (['--reverse'] if reverse else []) + [str(patch)]
        result = subprocess.run(argv, env={'GIT_INDEX_FILE': str(index),
                                           'PATH': '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin'},
                                capture_output=True, text=True, timeout=300)
        return result.returncode == 0


def check(repo, refs, paths=(), pattern=None, patch=None, log_grep=(), fetch=False):
    repo = Path(repo)
    if fetch:
        _git(repo, 'fetch', '--tags', '--quiet', 'origin')
    regex = re.compile(pattern, re.MULTILINE) if pattern else None
    rows = []
    for ref in refs:
        resolved = None
        candidates = (ref,)
        if not ref.startswith('refs/') and not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', ref):
            candidates = ('refs/tags/' + ref, 'refs/remotes/origin/' + ref, ref)
        for candidate in candidates:
            out = _git(repo, 'rev-parse', '--verify', '--quiet', candidate + '^{commit}', check=False)
            if out.returncode == 0:
                resolved = out.stdout.strip()
                break
        if resolved is None:
            rows.append(dict(ref=ref, commit=None, error='ref not found; fetch the repository'))
            continue
        files, matches = {}, []
        for path in paths:
            show = _git(repo, 'show', f'{resolved}:{path}', check=False)
            files[path] = show.returncode == 0
            if show.returncode == 0 and regex:
                for number, line in enumerate(show.stdout.splitlines(), 1):
                    if regex.search(line):
                        matches.append(dict(path=path, line=number, text=line.strip()[:160]))
        row = dict(ref=ref, commit=resolved, files=files,
                   present=(bool(matches) if regex and any(files.values()) else None), matches=matches[:20])
        if patch:
            row['applies'] = _applies(repo, resolved, Path(patch).resolve())
            row['reverse_applies'] = _applies(repo, resolved, Path(patch).resolve(), reverse=True)
        if log_grep:
            args = ['log', '--first-parent', '--format=%H%x09%ad%x09%s', '--date=short', '-n', '20', resolved]
            for term in log_grep:
                args += ['--grep', term]
            row['merged_commits'] = [dict(zip(('commit', 'date', 'subject'), l.split('\t', 2)))
                                     for l in _git(repo, *args, '-i').stdout.splitlines() if l]
        rows.append(row)
    seen_present = any(r.get('present') or r.get('applies') for r in rows)
    for row in rows:
        if row.get('commit') is None:
            row['state'] = 'unknown'
        elif row.get('reverse_applies'):
            row['state'] = 'fixed'
        elif row.get('present') or row.get('applies'):
            row['state'] = 'present'
        elif paths and not any(row['files'].values()):
            row['state'] = 'absent'
        elif row.get('present') is False and seen_present:
            row['state'] = 'fixed'
        else:
            row['state'] = 'unknown'
    return dict(kind='upstream-status', schema_version=1, repository=_git(repo, 'remote', 'get-url', 'origin',
                check=False).stdout.strip() or str(repo), pattern=pattern, patch=str(patch) if patch else None,
                refs=rows, note='merged code only; open issues and unmerged PRs are not consulted')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--ref', action='append', required=True)
    parser.add_argument('--path', action='append', default=[])
    parser.add_argument('--pattern')
    parser.add_argument('--patch')
    parser.add_argument('--log-grep', action='append', default=[])
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(args.repo, args.ref, args.path, args.pattern, args.patch, args.log_grep, args.fetch)
        text = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            with args.output.open('x') as stream:
                stream.write(text)
        print(text, end='')
    except (OSError, ValueError, re.error, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

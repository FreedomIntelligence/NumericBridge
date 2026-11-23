#!/usr/bin/env python3
"""Scan files intended for publication for private identifiers before a community submission.
Call: scan(paths, terms=()); CLI: python scripts/anonymize_scan.py PATH ... [--term TEXT ...] [--output NEW.json]
Input: files or directories to be shared (drafts, evidence, PR bodies, patches, repro scripts).
Output: {"status":"clean"|"findings", "files":N, "findings":[{file, line, kind, match}]}.
Requires: Standard library.

Details:
Built-in kinds: ipv4, ipv6, email, home_path (/Users/NAME, /home/NAME, C:\\Users\\NAME),
private_path (absolute /mnt, /data, /SharePath, /host-model, /nfs, /scratch paths; a
repository-relative path such as site/data/x.json is not one), credential
(token=, password=, api_key=, bearer tokens, private keys). --term adds literal case-insensitive
terms (host aliases, user names, account IDs). Version strings such as 0.18.0 are not IPv4
(exactly four dot-separated octets with no surrounding digits are required); loopback
127.0.0.0/8, 0.0.0.0 and ::1 identify no host and are allowed. Binary files are skipped.
CLI exit: 0=clean, 1=findings, 2=input error.
"""

__all__ = ['scan']
import argparse
import json
from pathlib import Path
import re
import sys

PATTERNS = {
    'ipv4': re.compile(r'(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w.])'),
    'ipv6': re.compile(r'(?<![\w:])(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}(?![\w:])'),
    'email': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'),
    'home_path': re.compile(r'(?:/Users/|/home/|[A-Za-z]:\\Users\\)[A-Za-z0-9._-]+'),
    'private_path': re.compile(r'(?<![\w.~-])/(?:mnt|data|SharePath|host-model|nfs|scratch)/[A-Za-z0-9._-]+'),
    'credential': re.compile(r'(?i)(?:token|password|passwd|secret|api[_-]?key)\s*[=:]\s*\S{6,}|'
                             r'bearer\s+[A-Za-z0-9._~+/-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----'),
}
ALLOWED_EMAIL = re.compile(r'(?i)noreply@|@users\.noreply\.github\.com$|@example\.(?:com|org)$')
ALLOWED_IP = re.compile(r'127(?:\.\d{1,3}){3}|0\.0\.0\.0')


def _files(paths):
    for path in map(Path, paths):
        if path.is_dir():
            yield from (p for p in sorted(path.rglob('*')) if p.is_file())
        elif path.is_file():
            yield path
        else:
            raise ValueError('not found: ' + str(path))


def scan(paths, terms=()):
    findings, count = [], 0
    literal = [(t, re.compile(re.escape(t), re.IGNORECASE)) for t in terms if t]
    for path in _files(paths):
        raw = path.read_bytes()
        if b'\0' in raw[:8192]:
            continue
        count += 1
        for number, line in enumerate(raw.decode('utf-8', errors='replace').splitlines(), 1):
            for kind, pattern in list(PATTERNS.items()) + [('term:' + t, p) for t, p in literal]:
                for match in pattern.finditer(line):
                    text = match.group(0)
                    if kind == 'email' and ALLOWED_EMAIL.search(text):
                        continue
                    if kind == 'ipv4' and ALLOWED_IP.fullmatch(text):
                        continue
                    findings.append(dict(file=str(path), line=number, kind=kind, match=text[:80]))
    return dict(status='clean' if not findings else 'findings', files=count, findings=findings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.partition('\n\nDetails:\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--term', action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        result = scan(args.paths, args.term)
        if args.output:
            with args.output.open('x') as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
                stream.write('\n')
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='error', reason=str(exc))))
        return 2
    print(json.dumps(dict(status=result['status'], files=result['files'], findings=len(result['findings']))))
    return 0 if result['status'] == 'clean' else 1


if __name__ == '__main__':
    sys.exit(main())

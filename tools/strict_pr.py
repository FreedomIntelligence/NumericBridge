"""Render and verify typed v1 PR drafts against explicitly trusted offline Git repositories.

Only a conservative subset of official Markdown templates can be verified. An
unsupported template is still pinned but requires manual formatting; no PR body
or claim of template compliance is generated. Human attestations are never made
by this tool. The Git repositories and attribution come from the caller's trusted
configuration, not the contribution payload. No Git write commands are used.
"""

from collections.abc import Mapping
from hashlib import sha256
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from .contribution_contract import COMMIT
from .pr_draft import DEFAULT_HEADINGS, NUMERICBRIDGE_CASES_URL, NUMERICBRIDGE_REPO_URL


_REPO = re.compile(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/[A-Za-z0-9_.-]{1,100}')
_ATX = re.compile(r'^( {0,3})(#{1,6})[ \t]+(.*)$')
_FENCE = re.compile(r'^(`{3,}|~{3,})(.*)$')
_CHECKBOX = re.compile(r'^([-+*] |\d{1,9}[.)] )\[[ xX]\]( +)(\S.*)$')
_CHECKBOX_ANY = re.compile(r'(?:[-+*]|\d+[.)])\s+\[[ xX]\]')
_HTML = re.compile(r'<(?:!--|[!?]|/?[A-Za-z][A-Za-z0-9-]*(?=\s|/?>))')
_PLACEHOLDER = re.compile(r'\b(?:TODO|TBD|FIXME|XXX)\b|<fill[^>]*>|\{\{[^}]+\}\}|(?:Fixes|Closes)\s+#\s*$', re.I)
_FOOTNOTE = re.compile(r'\[\^[^\]\s]+\]')
_NESTED_STRUCTURE = re.compile(r'^\s*(?:(?:[-*+] |\d+[.)] )\s*)+(?:[#>]|`{3,}|~{3,})')
_FOOTER_MARKER = re.compile(r'<\s*/?sub\s*>|Diagnosed and verified with|Discovered by', re.I)
_SELF_ATTESTATION = re.compile(
    r'(?im)\bsigned-off-by\s*:|^\s*(?:[-*+]\s+)?(?:i|we)\s+(?:have\s+)?(?:'
    r'(?:personally\s+(?:reviewed|review|ran|run|tested|verified)|reviewed\s+every\s+(?:line|change)|'
    r'(?:signed|sign)\s+(?:the\s+)?dco))'
)
_HUMAN = ('human_review', 'human_test', 'dco')
_DEFAULT_CHECKS = ('applies_cleanly', 'repo_style_test', 'native_on_target', 'performance', 'human_review')
_CHECK_ORDER = (*_DEFAULT_CHECKS, 'human_test', 'dco', 'issue_link')
_STATES = ('passed', 'failed', 'pending', 'not_applicable')
_NOT_APPLICABLE = {'performance', 'issue_link'}
_SOURCE_KEYS = {'repository', 'base_branch', 'target_commit', 'title', 'template_path', 'section_contents', 'checks'}
_SCOPE = ['community_site', 'community_pr', 'upstream_pr']
_MAX_TEMPLATE = 128 * 1024
_MAX_SECTION = 128 * 1024


def _slug(value):
    if not isinstance(value, str) or not _REPO.fullmatch(value):
        raise ValueError('repository must be an owner/repo slug')
    owner, repo = value.split('/')
    if len(owner) > 39 or repo in ('.', '..'):
        raise ValueError('repository must be an owner/repo slug')
    return value


def load_registry(path=None):
    """Load the operator's JSON slug-to-local-Git-directory mapping (no config: {})."""
    if path is None:
        return {}
    source = Path(path).expanduser().resolve(strict=True)

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate repository in trusted upstream registry')
            result[key] = value
        return result

    raw = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(raw, dict):
        raise ValueError('Trusted upstream registry must be a slug-to-directory object')
    result = {}
    for slug, value in raw.items():
        _slug(slug)
        if not isinstance(value, str) or not value.strip() or '\x00' in value:
            raise ValueError('Trusted registry values must name local Git directories')
        local = (source.parent / Path(value).expanduser()).resolve(strict=True)
        if not local.is_dir():
            raise ValueError(f'Trusted upstream directory does not exist for {slug}')
        result[slug] = _repo_root(local)
    return result


def _git(repo, *args, allow_fail=False):
    # No lazy promisor fetch, replacement objects, user Git settings, or overrides
    # from the caller's shell. In particular disable all transport protocols even
    # when the installed Git predates GIT_NO_LAZY_FETCH support.
    env = {key: val for key, val in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='')
    try:
        out = subprocess.run(['git', '--no-optional-locks', '-C', os.fspath(repo),
                              '-c', 'protocol.allow=never', *args], env=env, capture_output=True, timeout=15)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Could not read the configured upstream Git repository offline') from exc
    if out.returncode and not allow_fail:
        raise ValueError(f'Could not verify trusted upstream Git data offline ({args[0]})')
    return out


def _repo_root(path):
    repo = Path(path).resolve()
    if _git(repo, 'rev-parse', '--is-bare-repository').stdout.strip() == b'true':
        root = _git(repo, 'rev-parse', '--absolute-git-dir').stdout
    else:
        root = _git(repo, 'rev-parse', '--show-toplevel').stdout
    if Path(os.fsdecode(root.removesuffix(b'\n'))).resolve() != repo:
        raise ValueError('Trusted upstream path must name the root of a Git worktree or bare repository')
    return repo


def _target(repository, target_commit, trusted_repos, base_branch):
    _slug(repository)
    if not isinstance(target_commit, str) or not COMMIT.fullmatch(target_commit):
        raise ValueError('target_commit must be a full lowercase Git object ID')
    if not isinstance(trusted_repos, Mapping) or repository not in trusted_repos:
        raise ValueError(f'No trusted offline upstream repository configured for {repository}')
    repo = _repo_root(trusted_repos[repository])
    resolved = _git(repo, 'rev-parse', '--verify', target_commit).stdout.strip()
    if resolved != target_commit.encode('ascii'):
        raise ValueError('target_commit must be the full object ID for the configured repository')
    if _git(repo, 'cat-file', '-t', target_commit).stdout.strip() != b'commit':
        raise ValueError('target_commit must identify a trusted commit (not an indirect or tagged object)')
    if base_branch is not None:
        if (not isinstance(base_branch, str) or not base_branch or base_branch.startswith(('-', 'refs/'))
                or _git(repo, 'check-ref-format', f'refs/heads/{base_branch}', allow_fail=True).returncode):
            raise ValueError('base_branch must be a valid short Git branch name')
        ref = f'refs/remotes/origin/{base_branch}'
        remote_tip = _git(repo, 'show-ref', '--verify', '--quiet', ref, allow_fail=True)
        if remote_tip.returncode == 1 and b'origin' not in _git(repo, 'remote').stdout.splitlines():
            ref = f'refs/heads/{base_branch}'
        elif remote_tip.returncode:
            raise ValueError('Configured offline origin is missing the authoritative base branch')
        if _git(repo, 'merge-base', '--is-ancestor', target_commit, ref, allow_fail=True).returncode:
            raise ValueError('target_commit is not reachable from the trusted base branch')
    return repo


def _tree(repo, object_id):
    rows = {}
    for line in _git(repo, 'ls-tree', '-z', object_id).stdout.split(b'\0'):
        if not line:
            continue
        meta, _, name = line.partition(b'\t')
        mode, _kind, oid = meta.decode('ascii').split()
        # Unrelated Git paths may not be UTF-8; they are not supported GitHub names.
        rows[os.fsdecode(name)] = (mode, oid)
    return rows


def _sources(repo, commit):
    templates, guides = {}, {}
    root = _tree(repo, commit)
    parents = {'': root}
    for name, (mode, oid) in root.items():
        if name.lower() in ('.github', 'docs'):
            if mode != '040000':
                raise ValueError(f'Cannot verify template absence behind non-tree path {name}')
            parents[name + '/'] = _tree(repo, oid)
    for prefix, entries in parents.items():
        for name, (mode, oid) in entries.items():
            lowered = name.lower()
            if lowered in ('contributing.md', 'contributing.rst', 'contributing.txt'):
                guides[prefix + name] = (mode, oid)
            elif lowered == 'pull_request_template' and mode == '040000':
                discovered = _tree(repo, oid)
                recognized = {sub: val for sub, val in discovered.items()
                              if val[0] == '040000' or sub.lower().endswith(('.md', '.markdown'))}
                for sub, val in (recognized or discovered).items():
                    # Unrecognized assets don't compete with usable official
                    # templates, but unsupported-only directories remain manual.
                    templates[f'{prefix}{name}/{sub}'] = val
            elif lowered == 'pull_request_template' or lowered.startswith('pull_request_template.'):
                templates[prefix + name] = (mode, oid)
    return templates, guides


def _blob(repo, entry, *, allow_symlink=False):
    mode, oid = entry
    if mode not in ('100644', '100755') and not (allow_symlink and mode == '120000'):
        return None
    size = int(_git(repo, 'cat-file', '-s', oid).stdout)
    if size > _MAX_TEMPLATE:
        raise ValueError('Official template or contribution guide exceeds offline verification size limit')
    result = _git(repo, 'cat-file', 'blob', oid).stdout
    if len(result) != size:
        raise ValueError('Trusted upstream Git blob is incomplete')
    return result


def _has_placeholder(text):
    return any(_PLACEHOLDER.search(line) for line in text.splitlines())


def _dangerous_line(line):
    return (_HTML.search(line) or re.match(r'^ {0,3}\[[^\]]+\]:', line)
            or re.match(r'^\s*(?:>+|\[[^\]]+\]\s*<)', line)
            or _FOOTNOTE.search(line)
            or re.fullmatch(r'\s*=(?:\s*=)*\s*', line)
            or re.fullmatch(r'\s*-\s*-\s*', line)
            or re.fullmatch(r'\s*(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})', line))


def _template_structure(raw):
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'Official template is not UTF-8 Markdown'
    if any((ord(c) < 32 and c not in '\n\r\t') or c in '\x7f\x85\u2028\u2029\ufeff' for c in text):
        return None, 'Official template contains unsupported control characters'
    # Official comments are authoring instructions, not public body. Reject
    # inline/non-comment markup that can change the meanings of nearby sections.
    marker = re.compile(r'<!--.*?-->', re.S)
    for m in marker.finditer(text):
        left = text[text.rfind('\n', 0, m.start()) + 1:m.start()]
        end = text.find('\n', m.end())
        right = text[m.end():end if end != -1 else len(text)]
        if left.strip() or right.strip() or '<!--' in m[0][4:]:
            return None, 'Official template has inline or nested comments'
    cleaned = marker.sub('', text)
    sections = []
    preamble = []
    checklist = []
    for line in cleaned.splitlines():
        if not line.strip():
            (sections[-1]['fixed'] if sections else preamble).append('')
            continue
        if (line != line.lstrip() or _FENCE.match(line) or _dangerous_line(line) or _NESTED_STRUCTURE.match(line)
                or _FOOTER_MARKER.search(line) or _has_placeholder(unescape(line)) or _SELF_ATTESTATION.search(line)):
            return None, 'Official template uses unsupported or ambiguous Markdown'
        head = _ATX.fullmatch(line)
        if head:
            title = re.sub(r'[ \t]+#+[ \t]*$', '', head[3]).strip()
            if not _visible(title) or title.startswith('#') or any(s['heading'].casefold() == title.casefold() for s in sections):
                return None, 'Official template has a blank or duplicate heading'
            sections.append(dict(index=len(sections), level=len(head[2]), heading=title, line=line, fixed=[]))
            continue
        if line.lstrip().startswith('#'):
            return None, 'Official template contains a malformed or nested heading'
        checkbox = _CHECKBOX.fullmatch(line)
        if _CHECKBOX_ANY.search(line) and not checkbox:
            return None, 'Official template has an unsupported nested checklist'
        if checkbox:
            label = checkbox[3]
            if not any(c.isalnum() for c in unescape(label)):
                return None, 'Official checklist label is not visible text'
            # A blank line prevents a GitHub task list interrupting adjacent prose.
            previous = sections[-1]['fixed'] if sections else preamble
            if previous and previous[-1] and not (checklist and _CHECKBOX.fullmatch(previous[-1])):
                previous.append('')
            fixed = f'{checkbox[1]}[ ]{checkbox[2]}{label}'
            checklist.append(dict(section_index=sections[-1]['index'] if sections else None, label=label))
        else:
            # Nested lists/quotes and HTML form inputs cannot be projected safely.
            if re.match(r'^\s*(?:[-*+] |\d+[.)] )\s*(?:#|>|[-*+] |\d+[.)] )', line):
                return None, 'Official template contains nested Markdown blocks'
            fixed = line
        (sections[-1]['fixed'] if sections else preamble).append(fixed)
    if not sections:
        return None, 'Official template does not have unambiguous headed content slots'
    return (sections, preamble, checklist), None


def _inspect(*, repository, target_commit, template_path, trusted_repos, base_branch):
    repo = _target(repository, target_commit, trusted_repos, base_branch)
    if template_path is not None and (not isinstance(template_path, str) or not template_path or '\x00' in template_path):
        raise ValueError('template_path must select an official template at target_commit')
    templates, guides = _sources(repo, target_commit)
    if template_path is None and len(templates) > 1:
        raise ValueError('Multiple official templates exist; select an exact template_path: ' + ', '.join(sorted(templates)))
    if template_path is not None and template_path not in templates:
        raise ValueError('template_path is not an official template at the target commit')
    selected = template_path or next(iter(templates), None)
    guidance = set(guides)
    warnings = []
    dco = human_test = issue_link = False
    for line in sorted(guides):
        guide = _blob(repo, guides[line])
        if guide is None:
            warnings.append('A contribution guide is not a regular Git blob; verify manually.')
            continue
        content = guide.decode('utf-8', errors='replace')
        dco |= bool(re.search(r'\bDCO\b|Signed-off-by|git ?\s+commit\s+-s\b', content, re.I))
        human_test |= bool(re.search(r'\b(?:must\s+)?(?:personally|yourself)\s+(?:run|test)|\brun\b[^.!?\n]{0,70}\byourself\b', content, re.I))
        issue_link |= bool(re.search(r'\b(?:must|required\s+to)\s+(?:link|reference)\b[^.!?\n]{0,50}\bissue\b', content, re.I))
    if selected is None:
        proof = dict(target_commit=target_commit, path=None, sha256=None, source='numericbridge_default')
        parsed = ([dict(index=i, level=2, heading=title, line=f'## {title}', fixed=[])
                   for i, title in enumerate(DEFAULT_HEADINGS)], [], [])
        reason = None
    else:
        guidance.add(selected)
        entry = templates[selected]
        raw = _blob(repo, entry, allow_symlink=True)
        proof = dict(target_commit=target_commit, path=selected, sha256=None if raw is None else sha256(raw).hexdigest(),
                     source='repository_template')
        if entry[0] not in ('100644', '100755'):
            parsed, reason = None, 'Selected official template is not a regular Git blob'
        elif not selected.lower().endswith(('.md', '.markdown')):
            parsed, reason = None, 'Selected official template is not a supported Markdown format'
        else:
            parsed, reason = _template_structure(raw)
    if parsed is None:
        sections, preamble, checklist = [], [], []
    else:
        sections, preamble, checklist = parsed
    try:
        urls = [f'https://github.com/{repository}/blob/{target_commit}/{quote(path)}' for path in sorted(guidance)]
    except UnicodeError as exc:
        raise ValueError('Official template or contribution guide path is not UTF-8') from exc
    return dict(format_status='manual_review' if reason else 'verified', template_proof=proof, manual_reason=reason,
                slots=[{k: s[k] for k in ('index', 'level', 'heading')} for s in sections], checklist=checklist,
                guidance_urls=urls, guidance_warnings=warnings, human_test=human_test, dco=dco, issue_link=issue_link), sections, preamble


def inspect_template(*, repository, target_commit, template_path=None, trusted_repos, base_branch=None):
    """Return public-safe template identity, slots, checklist and format/origin diagnostics."""
    return _inspect(repository=repository, target_commit=target_commit, template_path=template_path,
                    trusted_repos=trusted_repos, base_branch=base_branch)[0]


def _credit(attribution):
    if not isinstance(attribution, dict) or set(attribution) - {'mode', 'name', 'scope'}:
        raise ValueError('Attribution must come from the submission envelope')
    mode, name = attribution.get('mode'), attribution.get('name')
    if mode == 'anonymous' and name is None and 'scope' not in attribution:
        return {'mode': mode}
    if (not isinstance(name, str) or not 1 <= len(name) <= 40 or name.strip() != name
            or attribution.get('scope', _SCOPE) != _SCOPE):
        raise ValueError('Invalid authorized contributor name or attribution scope')
    if mode == 'github' and re.fullmatch(r'@[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', name):
        return {'mode': mode, 'name': name, 'scope': list(_SCOPE)}
    if mode == 'nickname' and any(c.isalnum() for c in name) and all(c.isalnum() or c in ' _-.' for c in name):
        return {'mode': mode, 'name': name, 'scope': list(_SCOPE)}
    raise ValueError('Invalid authorized contributor name or mode')


def _footer(credit):
    identity = credit.get('name', '').replace('_', '\\_')
    suffix = f' · Discovered by {identity}' if identity else ''
    return (f'---\n<sub>Diagnosed and verified with [NumericBridge]({NUMERICBRIDGE_REPO_URL}) · '
            f'[Case Library]({NUMERICBRIDGE_CASES_URL}){suffix}</sub>')


def _visible(line):
    # Do not treat image URLs, reference labels, or character-entity spelling
    # as visible text. Code paragraphs can of course contain literal syntax.
    line = re.sub(r'!?\[([^\]\[]*)\]\([^\n]*\)', r'\1', line)
    return any(c.isalnum() for c in unescape(line))


def _contents(text):
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > _MAX_SECTION:
        raise ValueError('Every template slot needs bounded non-empty visible content')
    if _FOOTER_MARKER.search(text) or any((ord(c) < 32 and c not in '\n\t') or c in '\x7f\x85\u2028\u2029' for c in text):
        raise ValueError('Content contains reserved footer, human attestations, or control characters')
    text = text.strip('\n').rstrip()
    fence, visible = None, False
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            else:
                visible |= bool(line.strip())
            continue
        if marker:
            if marker[1][0] == '`' and '`' in marker[2]:
                raise ValueError('Content contains an invalid Markdown fence')
            fence = marker[1]
        elif (line.lstrip().startswith('#') or _dangerous_line(line) or _CHECKBOX_ANY.search(line)
                or _has_placeholder(unescape(line)) or _NESTED_STRUCTURE.match(line)
                or _SELF_ATTESTATION.search(line) or line and line[0].isspace() and line.strip()):
            raise ValueError('Slot contains unsupported Markdown, extra heading, checklist, or placeholder')
        else:
            visible |= _visible(line)
    if fence or not visible:
        raise ValueError('Every template slot needs visible content and balanced code fences')
    return text


def _checks(checks, metadata, repository):
    if not isinstance(checks, dict) or set(checks) - set(_CHECK_ORDER):
        raise ValueError('Unknown or system-generated PR check cannot be attested by a submitter')
    known = set(checks) | set(_DEFAULT_CHECKS)
    known |= {name for name in ('dco', 'human_test', 'issue_link') if metadata[name]}
    clean = {}
    for name in _CHECK_ORDER:
        if name not in known:
            continue
        check = checks.get(name, {'status': 'pending'})
        if not isinstance(check, dict) or set(check) - {'status', 'evidence'} or check.get('status') not in _STATES:
            raise ValueError(f'Invalid check state for {name}')
        status = check['status']
        evidence = check.get('evidence')
        if name in _HUMAN and status != 'pending':
            raise ValueError(f'{name} requires action by the actual upstream filer')
        if status == 'not_applicable' and (name not in _NOT_APPLICABLE or name == 'issue_link' and metadata[name]):
            raise ValueError(f'{name} cannot be marked not applicable')
        if evidence is None and status != 'pending':
            raise ValueError(f'{name} needs an evidence reference or specific explanation')
        if 'evidence' in check and (not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000
                                    or _has_placeholder(evidence) or _SELF_ATTESTATION.search(evidence)
                                    or _FOOTER_MARKER.search(evidence)):
            raise ValueError(f'Invalid check evidence for {name}')
        if name == 'issue_link' and status == 'passed' and not re.fullmatch(
                rf'https://github\.com/{re.escape(repository)}/issues/[1-9]\d*', evidence):
            raise ValueError('issue_link passed needs an issue URL in the target repository')
        clean[name] = dict(check)
    return clean


def _claim_consistency(checks, contents):
    claim = re.compile(r'\b(?:all (?:required |target |native |regression )?tests (?:passed|pass)|'
                       r'(?:fully|completely) verified|ready to merge|(?:passed|green)\s+(?:all|every)\s+checks?)\b', re.I)
    if any(checks.get(name, {}).get('status') != 'passed' for name in ('repo_style_test', 'native_on_target')):
        if any(claim.search(text) for text in contents):
            raise ValueError('Unqualified passing or merge-ready claim conflicts with pending/failed checks')
    if checks.get('human_review', {}).get('status') == 'pending' and any(
            re.search(r'\b(?:ready to merge|human review (?:is )?(?:complete|passed|approved))\b', text, re.I)
            for text in contents):
        raise ValueError('A draft cannot claim readiness or human approval')


def render_pr(*, repository, base_branch, target_commit, title, template_path=None,
              section_contents, checks, attribution, trusted_repos):
    """Build one strict draft; section_contents are indexed in inspect_template order."""
    if (not isinstance(title, str) or not title.strip() or title.strip() != title or len(title) > 120
            or any(ord(c) < 32 or ord(c) == 127 for c in title) or _has_placeholder(title)
            or _FOOTER_MARKER.search(title) or _HTML.search(title)):
        raise ValueError('PR title must be non-empty single-line text without placeholders')
    if not isinstance(base_branch, str) or not base_branch:
        raise ValueError('base_branch is required for a strict draft')
    credit = _credit(attribution)
    metadata, sections, preamble = _inspect(repository=repository, target_commit=target_commit,
                                            template_path=template_path, trusted_repos=trusted_repos,
                                            base_branch=base_branch)
    proof = metadata['template_proof']
    clean = _checks(checks, metadata, repository)
    pending = [name for name in _CHECK_ORDER if name in clean and clean[name]['status'] in ('failed', 'pending')]
    if metadata['format_status'] == 'manual_review':
        if section_contents is not None and section_contents != []:
            raise ValueError('Unsupported official template requires manual review; do not supply an unofficial body')
        contents, body = None, None
        pending.append('template_body')
    else:
        if not isinstance(section_contents, list) or len(section_contents) != len(sections):
            raise ValueError('Supply exactly one content block per indexed official template slot')
        contents = [_contents(item) for item in section_contents]
        _claim_consistency(clean, [title, *contents])
        parts = ['\n'.join(preamble).strip()]
        for section, content in zip(sections, contents):
            fixed = '\n'.join(section['fixed']).strip('\n')
            parts.append('\n\n'.join(part for part in (section['line'], content, fixed) if part))
        parts.append(_footer(credit))
        body = '\n\n'.join(part for part in parts if part) + '\n'
    if metadata['format_status'] == 'manual_review' or metadata['checklist']:
        pending.append('upstream_checklist')
    source = dict(repository=repository, base_branch=base_branch, target_commit=target_commit, title=title,
                  template_path=proof['path'], section_contents=contents, checks=clean)
    return dict(schema_version=1, kind='strict_upstream_pr_draft', source=source, template_proof=proof,
                format_status=metadata['format_status'], manual_reason=metadata['manual_reason'], repository=repository,
                base_branch=base_branch, title=title, body_markdown=body, guidance_urls=metadata['guidance_urls'],
                pending_checks=pending, template_source=proof['source'],
                fallback_used=proof['source'] == 'numericbridge_default',
                contribution_requirements=dict(commit=target_commit, ref=base_branch, checked=True,
                                               template_headings=[s['heading'] for s in metadata['slots']],
                                                requires_dco=metadata['dco'], requires_checklist=bool(metadata['checklist']),
                                               requires_issue_link=metadata['issue_link'],
                                               requires_human_test=metadata['human_test']),
                attribution=credit)


def verify_pr(document, *, attribution, trusted_repos):
    """Re-read trusted Git and require exact equality with a deterministic re-render."""
    source = document.get('source') if isinstance(document, dict) else None
    if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
        raise ValueError('Strict PR verification requires the complete structured source')
    expected = render_pr(**source, attribution=attribution, trusted_repos=trusted_repos)
    # Python equality treats e.g. True and 1 as equal; compare exact JSON types.
    try:
        canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
        same = canonical(expected) == canonical(document)
    except (TypeError, ValueError) as exc:
        raise ValueError('Strict PR draft must contain only valid JSON values') from exc
    if not same:
        raise ValueError('Strict PR draft does not match trusted template and rendered structured source')
    return expected

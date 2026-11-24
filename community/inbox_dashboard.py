"""Render the private, read-only NumericBridge inbox as a standalone HTML page."""

from collections import Counter
from datetime import datetime
from html import escape
import json
from urllib.parse import urlencode

from .inbox_view import PAGE_SIZE, QUEUE_FILTERS, STATES, visible_rows


GROUP_LABELS = {
    'evaluations': '评测',
    'repairs': '问题与修复',
    'relations': '关联记录',
    'evidence': '证据',
}
COUNT_LABELS = {
    'evaluations': '评测', 'repairs': '修复', 'library_groups': '库分组',
    'cases': '案例', 'issues': '问题', 'candidates': '候选方案',
    'verifications': '验证', 'workloads': '工作负载', 'links': '关联',
    'evidence_files': '随稿证据文件', 'upstream_pr_drafts': '上游 PR 草稿',
    'operator_optimizations': '算子优化',
}
NORMALIZED_GROUPS = {
    'evaluations': '评测结果', 'repairs': '修复记录', 'cases': '案例',
    'issue_classifications': '问题分类', 'candidates': '候选方案',
    'operator_optimizations': '算子优化', 'upstream_pr_drafts': '上游 PR 草稿',
    'other_evidence': '其他证据', 'unindexed_records': '未归入案例的记录',
}

STYLE = '''
:root{color-scheme:light;--ink:#172b2a;--muted:#526663;--line:#d6e1dd;
  --paper:#fff;--ground:#f1f5f2;--accent:#17644c;--focus:#1165a8}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font:1rem/1.55 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
h1,h2,h3,h4,p,dl,dd{margin:0}h1{font-size:clamp(1.8rem,5vw,2.85rem);line-height:1.18}
h2{font-size:1.3rem}h3{font-size:1.1rem}h4{font-size:1rem}
a{color:var(--accent)}a:focus-visible,input:focus-visible,button:focus-visible,summary:focus-visible{
  outline:3px solid var(--focus);outline-offset:3px}
.skip{position:absolute;left:1rem;top:1rem;transform:translateY(-180%);padding:.6rem 1rem;
  border-radius:.5rem;background:white;z-index:1}.skip:focus{transform:none}
.hero{background:#15362f;color:#fff;border-bottom:4px solid #75c6a4}
.wrap{width:min(100%,82rem);margin:auto;padding-inline:clamp(1rem,4vw,2.75rem)}
.hero .wrap{padding-block:.65rem}.brandline{display:flex;align-items:center;justify-content:space-between;
  gap:.6rem 1rem;flex-wrap:wrap}.brand{font-size:.82rem;letter-spacing:.11em;font-weight:750;
  display:flex;align-items:center;gap:.7rem}.brand-mark{width:1.5rem;height:1.5rem;display:inline-grid;
  place-items:center;border:1px solid #91beae;border-radius:.35rem;background:#1d5848;letter-spacing:0}
.pagehead{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;
  gap:.6rem 1.2rem;margin-bottom:1rem}.pagehead p{margin-top:.35rem}.pagehead h1{font-size:clamp(1.5rem,4vw,2.2rem)}
.refresh{display:inline-flex;align-items:center;min-height:2.5rem;padding:.35rem .75rem;background:var(--paper);
  border:1px solid var(--line);border-radius:.5rem;font-weight:700;text-decoration:none}.refresh:hover{border-color:var(--accent)}
.private{border:1px solid #729b8e;border-radius:99px;padding:.12rem .64rem;color:#e2f7ec;font-size:.8rem}
.hero p{color:#d3e9e0;margin-top:.55rem}
main{padding-block:1.35rem 2.6rem}.controls,.panel{background:var(--paper);border:1px solid var(--line);
  border-radius:1rem;box-shadow:0 3px 12px #172b2a08;min-width:0;overflow-wrap:anywhere}
.controls{padding:clamp(.9rem,2vw,1.4rem);margin-bottom:1rem}
.filters,.queue-list,.timeline{margin:0;padding:0;list-style:none}
.filters{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.6rem}.filter{display:flex;align-items:center;gap:.5rem;
  border:1px solid var(--line);background:var(--paper);color:var(--ink);text-decoration:none;
  padding:.27rem .65rem;min-height:2.2rem;border-radius:99px;font-size:.88rem;max-width:100%}
.filters li{min-width:0;max-width:100%}.filter:hover{border-color:var(--accent);background:#edf8f1}
.filter[aria-current]{background:#17644c;color:white;border-color:#17644c;font-weight:700}
.filter-label{display:flex;align-items:center;gap:.5rem;padding:.27rem .65rem;color:var(--muted);font-size:.88rem}
.count{font-variant-numeric:tabular-nums;font-weight:750;background:#eaf1ee;border-radius:99px;padding:0 .4rem}
.filter[aria-current] .count{background:#fff;color:#17644c}
form{margin-top:1rem}label{display:block;font-weight:700;font-size:.9rem;margin-bottom:.35rem}
.search{display:flex;flex-wrap:wrap;gap:.55rem;align-items:center}.search input,.search button{font:inherit;
  min-height:2.65rem;border-radius:.55rem;padding:.4rem .75rem}.search input{flex:1 1 14rem;min-width:0;
  border:1px solid #829892;background:#fff;color:var(--ink)}.search button{background:#17644c;border:1px solid #17644c;
  color:white;font-weight:700;cursor:pointer}.search button:hover{background:#124e3c}.reset{padding:.4rem}
.layout{display:grid;grid-template-columns:minmax(14rem,1fr) minmax(0,2.15fr);align-items:start;gap:1rem}
.panel-head,.content{padding:clamp(1rem,2.5vw,1.55rem)}.panel-head{border-bottom:1px solid var(--line)}
.jump-detail{display:inline-flex;align-items:center;min-height:2.25rem;font-size:.84rem;margin-top:.2rem}
.eyebrow{font-size:.78rem;font-weight:750;color:var(--accent);letter-spacing:.08em;margin-bottom:.25rem}
.muted{color:var(--muted);font-size:.88rem}.queue-list{padding:.45rem}.queue-list li+li{border-top:1px solid var(--line)}
.queue-item{display:block;text-decoration:none;color:var(--ink);border-radius:.6rem;border:1px solid transparent;
  padding:.72rem;margin:.25rem}.queue-item:hover{border-color:#8ab8a7;background:#f0f9f4}
.queue-item[aria-current]{background:#e7f5ed;border-color:#80b29e;box-shadow:inset .2rem 0 var(--accent)}
.queue-title{display:block;font-weight:700}.short-id{display:block;color:var(--muted);font-size:.76rem}.queue-meta,.event-meta{display:flex;flex-wrap:wrap;
  align-items:center;gap:.25rem .6rem;margin-top:.35rem}
.badge{display:inline-block;padding:.09rem .55rem;color:#3d5354;background:#e9efef;border-radius:99px;
  font-size:.8rem;font-weight:750}.badge.pending_review{background:#fef1cd;color:#6c4300}
.badge.needs_information{background:#f4eafb;color:#642973}.badge.accepted{background:#dff2e7;color:#14543f}
.badge.published{background:#e5f0fb;color:#224f75}.badge.declined{background:#f9e9e7;color:#833629}
.empty{margin-top:.4rem;color:var(--muted);padding:.75rem;background:#f2f6f4;border-radius:.55rem}
.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem;margin-top:1rem}
.meta .wide{grid-column:1/-1}.receipt-id{user-select:all}.meta dt,.stats dt{color:var(--muted);font-size:.8rem}.meta dd{font-size:.92rem}
.detail h2{margin-bottom:.45rem}.detail-section+.detail-section{border-top:1px solid var(--line);
  padding-top:1.1rem;margin-top:1.1rem}.detail-section>h3{margin-bottom:.55rem}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));gap:.5rem;margin:.65rem 0}
.stats>div{background:#f2f6f4;border:1px solid var(--line);border-radius:.55rem;padding:.48rem .65rem}
.stats dd{font-variant-numeric:tabular-nums;font-weight:750;font-size:1.15rem}
.group{margin-top:.75rem}.group h4{margin-bottom:.25rem}details{margin-top:.35rem;border:1px solid var(--line);
  border-radius:.55rem}summary{cursor:pointer;padding:.48rem .66rem;color:var(--accent);font-weight:650}
summary:hover{background:#edf8f1;border-radius:.55rem}pre{margin:0;padding:.7rem;border-top:1px solid var(--line);
  background:#f3f6f5;white-space:pre-wrap;overflow-wrap:anywhere;font: .83rem/1.5 ui-monospace,monospace}
.timeline li{border-left:2px solid #a0bbad;margin-left:.22rem;padding:.12rem 0 .75rem .78rem}
.timeline li:last-child{padding-bottom:.12rem}.event-meta{margin-top:.25rem}.reason{margin-top:.3rem;white-space:pre-wrap}
.filter-note{margin-top:.7rem}.queue-meta .minor{color:#5e5750;font-size:.78rem;border-left:1px solid var(--line);padding-inline-start:.55rem}
.detail-top{display:flex;justify-content:space-between;align-items:center;gap:.6rem;margin:.25rem 0 .65rem}
.detail-top nav,.local-nav{display:flex;gap:.3rem;flex-wrap:wrap}.detail-top a,.local-nav a{font-size:.86rem;
  padding:.4rem .56rem;border-radius:.35rem;font-weight:650;text-decoration:none}.detail-top a:hover,.local-nav a:hover{
  background:#e5f1eb;text-decoration:underline}.local-nav{position:sticky;top:0;z-index:2;padding:.4rem clamp(1rem,2.5vw,1.55rem);background:#f7faf8;
  border-bottom:1px solid var(--line)}.related{margin-top:.8rem;border:1px solid #dac68d;border-radius:.5rem;
  background:#fff9e8;color:#604500;padding:.55rem .7rem;font-size:.86rem}.related ul{list-style:none;margin:0;
  padding:0;display:inline-flex;flex-wrap:wrap;gap:.5rem;margin-inline-start:.5rem}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:.6rem;margin:1rem 0 .8rem}
.table-wrap:focus-visible{outline:3px solid var(--focus);outline-offset:2px}table{width:100%;border-collapse:collapse;
  text-align:left;font-size:.87rem}caption{text-align:left;font-weight:750;padding:.52rem .7rem}thead{background:#edf3ef}
th,td{padding:.5rem .7rem;border-top:1px solid var(--line);vertical-align:top}thead th{color:var(--muted)}
.acceptance{display:flex;flex-wrap:wrap;gap:.22rem}.verdict{display:inline-block;border-radius:.25rem;padding:.1rem .4rem;background:#e9efef;color:#3d5354;
  font-weight:700;white-space:nowrap}.verdict.good{background:#dff2e7;color:#14543f}.verdict.bad{background:#f9e9e7;color:#833629}
.verdict.warn{background:#fef1cd;color:#6c4300}.detail-section{scroll-margin-top:4.5rem}
.detail-section:focus-visible,.detail:focus-visible,#queue:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
.comparison-body{padding:.55rem .75rem .8rem;border-top:1px solid var(--line)}.scope{margin-top:.25rem}
.comparison-meta{display:inline-block;color:var(--muted);font-size:.8rem;font-weight:500;margin-inline-start:.65rem}
.measure{font-variant-numeric:tabular-nums}.measure.missing{color:var(--muted)}.back-results{display:none}
.coverage-flag{display:inline-block;white-space:nowrap;margin-inline-start:.45rem;background:#fff1cd;
  color:#674200;border-radius:.25rem;font-size:.75rem;font-weight:700;padding:.05rem .28rem}
pre{max-height:26rem;overflow:auto}pre:focus-visible{outline:3px solid var(--focus);outline-offset:-3px}
.relation-list{list-style:none;padding:0;margin:.55rem 0 0}.relation-list>li{border:1px solid var(--line);
  border-radius:.55rem;margin-bottom:.5rem;padding:.65rem .75rem}.rel-edge{display:flex;align-items:baseline;
  gap:.15rem .5rem;flex-wrap:wrap;margin-bottom:.15rem}.ref-kind,.rel-verb{font-size:.8rem;font-weight:700;color:var(--accent)}
.rel-verb{background:#edf3ef;border-radius:.25rem;padding:.1rem .35rem}.relation-list ul{margin:.3rem 0 0;padding-inline-start:1rem}
.group summary[id]{scroll-margin-top:4.5rem}.group summary:target{outline:2px solid var(--focus)}
.queue-head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem}
.queue-context{font-size:.78rem;font-weight:650;color:var(--accent);margin-top:.3rem}
.sort{display:inline-flex;align-items:center;gap:.35rem;min-height:2rem;padding:.1rem .4rem;
  border-radius:.35rem;font-size:.8rem;text-decoration:none;font-weight:650}.sort:hover{background:#e5f1eb}
.global-label{font-size:.73rem;color:var(--muted);padding:.15rem .4rem;vertical-align:middle;
  background:#edf3ef;border-radius:.3rem;margin-inline-start:.25rem}
.queue-item:target{outline:2px solid var(--focus);outline-offset:-2px;scroll-margin-block:.35rem}
.pagination{display:flex;align-items:center;justify-content:space-between;gap:.35rem;border-top:1px solid var(--line);
  padding:.55rem .75rem;background:var(--paper);font-size:.82rem}.pagination a,.pagination [aria-disabled]{
  color:var(--accent);white-space:nowrap;display:inline-grid;place-items:center;min-height:2.75rem;
  padding:.2rem .5rem;border:1px solid var(--line);border-radius:.45rem;text-decoration:none}
.recovery{max-width:42rem;margin:2rem auto;padding:clamp(1.15rem,4vw,2.2rem)}
.recovery h1{font-size:clamp(1.35rem,4vw,2rem);margin-block:.35rem .65rem}.recovery .refresh{margin-top:1rem}
.pagination a:hover{background:#e5f1eb;border-color:var(--accent)}.pagination [aria-disabled]{color:var(--muted);background:#f3f6f4}
.page-index{text-align:center;font-weight:750;white-space:nowrap;font-variant-numeric:tabular-nums}
.page-index small{display:block;font-weight:500;color:var(--muted)}
@media(min-width:70rem){.queue-panel{position:sticky;top:1rem;max-height:calc(100vh - 2rem);
  overflow:auto;scrollbar-gutter:stable}}
@media(min-width:78rem){.controls{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem}
  .controls form{margin-top:0;flex:0 0 18rem}.controls .filters{gap:.38rem}.controls .filter{padding-inline:.56rem}
  .search input{flex-basis:9rem}}
@media(max-width:49rem){.layout{grid-template-columns:minmax(0,1fr)}.panel-head,.content{padding:1rem}
  .jump-detail,.back-results{display:inline-block;margin-top:.35rem}.queue-list{max-height:20rem;overflow:auto}
  .filter,.detail-top a,.local-nav a,.sort{min-height:2.75rem;display:inline-flex;align-items:center}
  .comparison-body table{min-width:25rem}.table-wrap th,.table-wrap td{padding:.45rem .5rem}}
@media(max-width:26rem){.meta{grid-template-columns:minmax(0,1fr)}.controls{padding:.85rem}
  .local-nav{position:static}.detail-section{scroll-margin-top:1rem}}
@media(prefers-color-scheme:dark){
  :root{color-scheme:dark;--ink:#e7f0ec;--muted:#acc1b8;--line:#354944;--paper:#1a2523;
    --ground:#111a18;--accent:#9de1c2;--focus:#87c6ff;--soft:#23332f;--selected:#25483e}
  .hero{background:#0e241e;border-color:#477d6a}.skip,.search input{background:var(--paper)}
  .search input{border-color:#667e75}.search input::placeholder{color:#a4b9b0}
  .filter:hover,.queue-item:hover,summary:hover,.refresh:hover,.sort:hover,.pagination a:hover,
  .detail-top a:hover,.local-nav a:hover{background:var(--soft);border-color:#7db99f}
  .filter[aria-current],.search button{background:#9de1c2;color:#132920;border-color:#9de1c2}
  .filter[aria-current] .count{background:#284c40;color:#e1f9ed}.search button:hover{background:#baf0d6}
  .count,.global-label,.empty,.stats>div,thead,pre,.rel-verb,.pagination [aria-disabled]{background:var(--soft)}
  .local-nav{background:#1e2d29}.queue-item[aria-current]{background:var(--selected);border-color:#7db99f}
  .queue-meta .minor{color:var(--muted)}
  .badge,.verdict{background:#344642;color:#def0e9}
  .badge.pending_review,.verdict.warn,.coverage-flag{background:#493918;color:#ffe3a2}
  .badge.needs_information{background:#432e4c;color:#f0c7fd}
  .badge.accepted,.verdict.good{background:#1e4438;color:#b3efcf}
  .badge.published{background:#273c53;color:#c2e1ff}
  .badge.declined,.verdict.bad{background:#502f2b;color:#ffd5cd}
  .related{background:#2d281a;border-color:#836b37;color:#ffe2a2}
}
@media(forced-colors:active){.queue-item[aria-current],.filter[aria-current]{border:2px solid Highlight}
  .badge,.verdict{border:1px solid currentColor}.queue-item:target{outline-color:Highlight}}
@media(prefers-reduced-motion:no-preference){.queue-item,.filter,.refresh{transition:background-color .14s,border-color .14s}}
'''


def _text(value, fallback=''):
    return fallback if value is None or value == '' else str(value)


def _html(value, fallback=''):
    return escape(_text(value, fallback), quote=True)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _url(query='', state='', receipt='', order='newest', page=1):
    params = [(key, value) for key, value in (
        ('receipt', receipt), ('q', query), ('status', state),
        ('order', order if order != 'newest' else ''), ('page', page if page > 1 else '')
    ) if value]
    return _html('/?' + urlencode(params) if params else '/')


def _state_label(state):
    return STATES.get(state, state or '状态未知')


def _badge(state):
    color = ' ' + state if state in STATES else ''
    return f'<span class="badge{color}">{_html(_state_label(state))}</span>'


def _time(value):
    raw = _text(value)
    if not raw:
        return '时间未知'
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return _html(raw)
    formatted = raw.replace('T', ' ').removesuffix('+00:00')
    if raw.endswith(('+00:00', 'Z')):
        formatted = formatted.removesuffix('Z') + ' UTC'
    return f'<time datetime="{_html(dt.isoformat())}">{_html(formatted)}</time>'


def _json_details(label, value, *, anchor=None):
    payload = _html(json.dumps(value, ensure_ascii=False, indent=2))
    location = f' id="{_html(anchor)}"' if anchor else ''
    return (f'<details><summary{location}>{_html(label)}</summary>'
            f'<pre tabindex="0" aria-label="原始 JSON">{payload}</pre></details>')


def _group_records(key, group):
    if key == 'evidence':
        return list(_mapping(group).items())
    records = []
    for kind, items in _mapping(group).items():
        if isinstance(items, list):
            for item in items:
                fields = _mapping(item)
                label = fields.get('title') or fields.get('name') or fields.get('id') or kind
                records.append((label, item))
    return records


def _evidence_table(caption, columns, rows, *, flagged=()):
    if not rows:
        return ''
    heading = ''.join(f'<th scope="col">{_html(column)}</th>' for column in columns)
    items = []
    for index, (name, *values) in enumerate(rows):
        cells = ''.join(f'<td class="measure{" missing" if value is None else ""}">'
                        f'{_html(value, "未提供")}</td>' for value in values)
        flag = '<span class="coverage-flag">低于预期</span>' if index in flagged else ''
        items.append(f'<tr><th scope="row">{_html(name, "未命名")}{flag}</th>{cells}</tr>')
    return (f'<div class="table-wrap" role="region" aria-label="{_html(caption)}" tabindex="0">'
            f'<table><caption>{_html(caption)}</caption><thead><tr>{heading}</tr></thead>'
            f'<tbody>{"".join(items)}</tbody></table></div>')


def _comparison_groups(draft):
    evaluations = _mapping(_mapping(draft.get('additions')).get('evaluations')).get('evaluations') or []
    cards = []
    for record in evaluations:
        if not isinstance(record, dict):
            continue
        model = _display_name(record.get('model')) or record.get('id') or '未命名评测'
        side_labels = {side['id']: side.get('label') or side.get('hardware') or side['id']
                       for side in record.get('sides') or []
                       if isinstance(side, dict) and isinstance(side.get('id'), str)}
        for comparison in record.get('comparisons') or []:
            if not isinstance(comparison, dict):
                continue
            title = comparison.get('title') or comparison.get('id') or '未命名对比'
            side_ids = [side for side in comparison.get('side_ids') or [] if isinstance(side, str)]
            sides = ' ↔ '.join(_text(side_labels.get(side, side)) for side in side_ids)
            metric_records = [m for m in comparison.get('metrics') or [] if isinstance(m, dict)]
            metrics = [(m.get('label') or m.get('id'), m.get('value'), m.get('unit')) for m in metric_records]
            side_metrics = [(m.get('label') or m.get('id'), side_labels.get(side, side), value, m.get('unit'))
                            for m in metric_records for side, value in _mapping(m.get('side_values')).items()]
            coverage = [(c.get('label'), c.get('observed'), c.get('expected'), c.get('unit'))
                        for c in comparison.get('coverage') or [] if isinstance(c, dict)]
            flagged = {index for index, (_, observed, expected, _) in enumerate(coverage)
                       if type(observed) in (int, float) and type(expected) in (int, float) and observed < expected}
            tables = (_evidence_table('记录指标', ('指标', '值', '单位'), metrics) +
                      _evidence_table('各对照端记录值', ('指标', '对照端', '值', '单位'), side_metrics) +
                      _evidence_table('采样覆盖', ('项目', '实测', '预期', '单位'), coverage, flagged=flagged))
            meta = f'{len(metrics)} 项指标 · {len(coverage)} 项覆盖'
            hint = f'<span class="coverage-flag">{len(flagged)} 项覆盖低于预期</span>' if flagged else ''
            cards.append(f'<details class="comparison"><summary><span>{_html(model)} · {_html(title)}</span>'
                         f'<small class="comparison-meta">{meta}</small>{hint}</summary>'
                         f'<div class="comparison-body"><p class="muted">对照端：{_html(sides, "未提供")}</p>'
                         f'<p class="scope">比较范围：{_html(comparison.get("scope"), "未提供")}</p>'
                         f'{tables or "<p class=empty>未提供指标或覆盖记录。</p>"}</div></details>')
    return ''.join(cards) or '<p class="empty">此稿件未提供结构化对比数据。可继续查看原始投稿。</p>'


RELATIONS = {'includes': '包含', 'derived_from': '源自', 'addresses': '处理', 'verifies': '验证',
             'uses_workload': '使用工作负载', 'documents': '记录', 'investigates': '调查',
             'measures': '测量', 'confirms': '确认'}
KINDS = {'case': '案例', 'issue': '问题', 'candidate': '候选方案', 'verification': '验证',
         'workload': '工作负载', 'investigation': '调查', 'comparison': '对比', 'repair': '修复'}


def _reference(ref):
    ref = _mapping(ref)
    identity, kind = _text(ref.get('id')), _text(ref.get('kind'))
    if not identity:
        return '<span class="muted">未提供</span>'
    return f'<span class="ref-kind">{_html(KINDS.get(kind, kind))}</span> <code>{_html(identity)}</code>'


def _declared_relations(draft, anchors):
    links = _mapping(_mapping(draft.get('additions')).get('relations')).get('links') or []
    items = []
    for link in links:
        if not isinstance(link, dict):
            continue
        relation = _text(link.get('relation'), '未注明')
        references = []
        for ref in link.get('evidence') or []:
            file = _text(_mapping(ref).get('file'))
            if not file:
                continue
            anchor = anchors.get(file)
            label = f'<code>{_html(file)}</code>'
            if anchor:
                label = f'<a href="#{anchor}">{label}</a>'
            references.append(f'<li>{label}</li>')
        sources = f'<ul>{"".join(references)}</ul>' if references else '<p class="muted">未附证据引用</p>'
        items.append(f'<li><div class="rel-edge">{_reference(link.get("from"))}'
                     f'<span class="rel-verb">{_html(RELATIONS.get(relation, relation))} →</span>'
                     f'{_reference(link.get("to"))}</div>'
                     f'<p class="muted">{_html(link.get("scope"), "未注明关联范围")}</p>{sources}</li>')
    if not items:
        return ''
    return (f'<p class="muted">{len(items)} 条投稿中明确声明的关联，非系统推断。</p>'
            f'<ul class="relation-list" role="list">{"".join(items)}</ul>')


def _draft_groups(draft, anchors):
    groups = _mapping(draft.get('additions'))
    parts = []
    for key, label in GROUP_LABELS.items():
        records = _group_records(key, groups.get(key))
        if records:
            panels = ''.join(_json_details(heading, record, anchor=anchors.get(heading) if key == 'evidence' else None)
                             for heading, record in records)
            parts.append(f'<div class="group"><h4>{label} · {len(records)}</h4>{panels}</div>')
    return ''.join(parts) or '<p class="empty">暂无投稿内容。</p>'


VERDICTS = {'pass': ('good', '达标'), 'passed': ('good', '达标'),
            'fail': ('bad', '未达标'), 'failed': ('bad', '未达标'),
            'gap': ('warn', '有差距'), 'skipped': ('neutral', '未评估')}
RESULT_VERDICTS = {'pass', 'passed', 'fail', 'failed', 'gap'}
TASKS = {'inference': '推理', 'sft': '监督微调', 'rl': '强化学习'}


def _display_name(value):
    if isinstance(value, dict):
        return value.get('name') or value.get('id') or value.get('model_id')
    return value if isinstance(value, (str, int)) else None


def _acceptance(item):
    verdict = _text(item.get('acceptance_verdict'))
    color, label = VERDICTS.get(verdict, ('neutral', verdict))
    passed = item.get('acceptance_passed')
    explicit = type(_mapping(item.get('acceptance_raw')).get('passed')) is bool
    result = ''
    if type(passed) is bool and (explicit or verdict in RESULT_VERDICTS):
        result = ('<span class="verdict good">通过</span>' if passed else
                  '<span class="verdict bad">未通过</span>')
    declared = (f'<span class="verdict {"warn" if color == "warn" else "neutral"}" '
                f'title="原始判定：{_html(verdict)}">判定：{_html(label)}</span>' if verdict else '')
    return f'<div class="acceptance">{result}{declared or ("" if result else "<span class=muted>未提供</span>")}</div>'


def _evaluation_table(items):
    rows = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        model = _display_name(item.get('model')) or item.get('id')
        task = _text(item.get('task'))
        hardware = [name for side in item.get('sides') or () if isinstance(side, dict)
                    if (name := _display_name(side.get('hardware')))]
        devices = ' / '.join(dict.fromkeys(str(name) for name in hardware))
        rows.append(f'<tr><th scope="row">{_html(model, "—")}</th>'
                    f'<td>{_html(TASKS.get(task, task), "—")}</td>'
                    f'<td>{_acceptance(item)}</td>'
                    f'<td>{_html(devices, "—")}</td></tr>')
    if not rows:
        return ''
    return ('<div class="table-wrap" role="region" aria-label="评测速览表" tabindex="0">'
            '<table><caption>评测速览</caption><thead><tr><th scope="col">模型</th>'
            '<th scope="col">任务</th><th scope="col">投稿验收</th><th scope="col">硬件</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _environment_summary(items):
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        model = _display_name(item.get('model')) or item.get('id')
        for side in item.get('sides') or []:
            if not isinstance(side, dict):
                continue
            frameworks = ', '.join(_text(value) for value in side.get('frameworks') or [])
            libraries = []
            for library in side.get('libraries') or []:
                if not isinstance(library, dict):
                    continue
                fields = [_text(library.get(key)) for key in ('name', 'version', 'commit')]
                if any(fields):
                    libraries.append(' '.join(value for value in fields if value))
            runtime = side.get('has_runtime_environment') is True
            if frameworks or libraries or runtime:
                label = ' · '.join(str(value) for value in (model, side.get('id')) if value)
                rows.append((label, _display_name(side.get('hardware')), frameworks,
                             '；'.join(libraries), '已记录' if runtime else None))
    table = _evidence_table('提交时声明的运行配置', ('模型与对照端', '硬件', '框架', '库 / 版本 / 提交', '运行环境'), rows)
    return f'<details><summary>对照端与运行配置 · {len(rows)}</summary>{table}</details>' if rows else ''


def _normalized_summary(normalized):
    if not normalized:
        return '<p class="empty">暂无归一化摘要。</p>'
    if isinstance(normalized, str):
        return f'<p class="reason">{_html(normalized)}</p>'
    counts = _mapping(_mapping(normalized).get('counts'))
    counts = {key: value for key, value in counts.items() if value != 0}
    evaluation_items = _mapping(normalized).get('evaluations')
    evaluations = _evaluation_table(evaluation_items)
    environments = _environment_summary(evaluation_items)
    stats = ''.join(f'<div><dt>{_html(COUNT_LABELS.get(key, key))}</dt><dd>{_html(value, "—")}</dd></div>'
                    for key, value in counts.items())
    cards = f'<dl class="stats">{stats}</dl>' if stats else ''
    groups = []
    for key, label in NORMALIZED_GROUPS.items():
        value = _mapping(normalized).get(key)
        if value:
            groups.append(_json_details(f'{label} · {len(value)}' if isinstance(value, (dict, list)) else label,
                                        value))
    return cards + evaluations + environments + ''.join(groups) + _json_details('查看完整归一化 JSON', normalized)


def _history(history):
    events = []
    for event in history or ():
        if not isinstance(event, dict):
            continue
        status = _badge(_text(event.get('state')))
        reviewer = _html(event.get('reviewer'), '审核人未知')
        timestamp = _time(event.get('created_at') or event.get('at'))
        reason = _html(event.get('reason'), '未填写原因。')
        events.append(f'<li>{status}<p class="event-meta muted">{reviewer} · {timestamp}</p>'
                      f'<p class="reason">{reason}</p></li>')
    if not events:
        return '<p class="empty">暂无审阅历史。</p>'
    return f'<ol class="timeline" role="list">{"".join(events)}</ol>'


def _controls(rows, query, state, order):
    counts = Counter(_text(row.get('state')) for row in rows)
    choices = [('', '全部投稿', len(rows)),
               ('active_pending', QUEUE_FILTERS['active_pending'], sum(bool(r.get('active_pending')) for r in rows)),
               ('superseded', QUEUE_FILTERS['superseded'], sum(bool(r.get('superseded_by')) for r in rows))]
    choices.extend((key, label, counts[key]) for key, label in STATES.items())
    filters = []
    for key, label, count in choices:
        current = ' aria-current="page"' if key == state else ''
        filters.append(f'<li><a class="filter" href="{_url(query, key, order=order)}"{current}>'
                       f'{_html(label)} <span class="count">{count}</span></a></li>')
    for key in sorted(counts.keys() - STATES.keys()):
        # The inbox endpoint only accepts known filter states.
        filters.append(f'<li><span class="filter-label">{_html(_state_label(key))} '
                       f'<span class="count">{counts[key]}</span></span></li>')
    hidden = f'<input type="hidden" name="status" value="{_html(state)}">' if state else ''
    if order != 'newest':
        hidden += f'<input type="hidden" name="order" value="{_html(order)}">'
    reset = '<a class="reset" href="/">清除筛选</a>' if query or state else ''
    return (f'<nav aria-label="按审核状态筛选"><h2>状态概览 <span class="global-label">全库</span></h2>'
            f'<ul class="filters" role="list">{"".join(filters)}</ul></nav>'
            f'<form method="get" action="/" role="search">{hidden}'
            f'<label for="query">搜索投稿</label><div class="search">'
            f'<input id="query" type="search" name="q" value="{_html(query)}" '
            f'maxlength="120" autocomplete="off" spellcheck="false" placeholder="输入标题或回执 ID">'
            f'<button type="submit">搜索</button>{reset}</div></form>')


def _row_hints(row):
    if row.get('superseded_by'):
        return '<span class="minor">有更新稿</span>'
    count = _mapping(row.get('evidence')).get('evidence_files')
    if type(count) is int:
        return f'<span class="minor">{count} 个随稿证据文件</span>'
    return ''


def _queue(rows, total, selected, query, state, order, page, pages, match_count, title_counts):
    items = []
    for row in rows:
        receipt = _text(row.get('id'))
        raw_title = _text(row.get('title'), '未命名投稿')
        title = _html(raw_title)
        brief_id = (f'<code class="short-id" title="{_html(receipt)}">'
                    f'{_html(receipt.removeprefix("submission-")[:12])}</code>'
                    if title_counts[raw_title] > 1 else '')
        timestamp = _time(row.get('received'))
        status = _badge(_text(row.get('state')))
        current = ' aria-current="page"' if receipt and receipt == selected else ''
        items.append(f'<li><a class="queue-item" id="{_html(receipt)}" href="{_url(query, state, receipt, order, page)}#receipt-detail"{current}>'
                     f'<strong class="queue-title">{title}</strong>{brief_id}'
                     f'<span class="queue-meta">{status}<span class="muted">{timestamp}</span>{_row_hints(row)}</span>'
                     '</a></li>')
    if items:
        body = f'<ul class="queue-list" role="list">{"".join(items)}</ul>'
    else:
        message = '暂无匹配投稿。' if query or state else '暂无投稿。'
        reset = '<a class="reset" href="/">清除条件</a>' if query or state else ''
        body = f'<div class="content"><p class="empty">{message} {reset}</p></div>'
    jump = '<a class="jump-detail" href="#receipt-detail">跳至所选投稿详情</a>' if selected else ''
    context = []
    if state:
        context.append(QUEUE_FILTERS.get(state) or _state_label(state))
    if query:
        context.append(f'搜索：{query}')
    context_label = ' · '.join(context)
    queue_context = f'<p class="queue-context">{_html(context_label)}</p>' if context else ''
    pagination = _pagination(query, state, order, page, pages, match_count)
    next_order = 'newest' if order == 'oldest' else 'oldest'
    order_label = '最早优先' if order == 'oldest' else '最新优先'
    sort = (f'<a class="sort" href="{_url(query, state, order=next_order)}" aria-label="切换排序；当前{order_label}">'
            f'{order_label} <span aria-hidden="true">⇅</span></a>' if match_count > 1 else '')
    return (f'<nav id="queue" class="panel queue-panel" tabindex="-1" aria-labelledby="queue-title"><div class="panel-head">'
            f'<div class="queue-head"><h2 id="queue-title">投稿队列</h2>{sort}</div><p class="muted">匹配 {match_count} / 全库 {total} 份</p>'
            f'{queue_context}{jump}</div>{body}{pagination}</nav>')


def _pagination(query, state, order, page, pages, match_count):
    if pages <= 1:
        return ''
    start, end = (page - 1) * PAGE_SIZE + 1, min(page * PAGE_SIZE, match_count)
    links = []
    for target, label in ((page - 1, '上一页'), (page + 1, '下一页')):
        if 1 <= target <= pages:
            links.append(f'<a href="{_url(query, state, order=order, page=target)}#queue">{label}</a>')
        else:
            links.append(f'<span aria-disabled="true">{label}</span>')
    return (f'<nav class="pagination" aria-label="投稿队列分页">{links[0]}'
            f'<span class="page-index">第 {page} / {pages} 页<small>第 {start}–{end} 条</small></span>'
            f'{links[1]}</nav>')


def _related(row, query, state, order, page):
    groups = []
    for key, label in (('superseded_by', '已有更新稿'), ('supersedes', '替代旧稿')):
        ids = row.get(key) or []
        if isinstance(ids, list) and ids:
            items = ''.join(f'<li><a href="{_url(query, state, r, order, page)}#receipt-detail">'
                            f'{_html(_text(r).removeprefix("submission-")[:12])}</a></li>'
                            for r in ids if isinstance(r, str))
            groups.append(f'<div><strong>{label}</strong><ul>{items}</ul></div>')
    return f'<aside class="related" aria-label="稿件版本">{"".join(groups)}</aside>' if groups else ''


def _detail(data, row, selected, outside_filter, query, state, visible, order, page):
    if row is None:
        message = '未找到这份投稿。' if selected else '请从投稿队列选择一份投稿。'
        return f'<div class="content"><h2 id="detail-title">投稿详情</h2><p class="empty">{message}</p></div>'
    draft = _mapping(data.get('draft'))
    credit = _html(_mapping(draft.get('attribution')).get('name'), '匿名')
    title = _html(row.get('title'), '未命名投稿')
    status = _badge(_text(row.get('state')))
    received = _time(row.get('received'))
    note = (f'<p class="empty filter-note">此投稿不在当前筛选结果中。 '
            f'<a href="{_url(receipt=selected, order=order)}#receipt-detail">在全部投稿中定位</a></p>'
            if outside_filter else '')
    normalized_data = data.get('normalized')
    normalized = ('<p class="empty">暂无法生成归一化摘要。 '
                  '<a href="#evidence">查看投稿原始内容</a></p>'
                  if data.get('normalization_unavailable') else _normalized_summary(normalized_data))
    commit = _text(_mapping(normalized_data).get('base_commit'))
    baseline = (f'<div><dt>投稿基线</dt><dd><code title="{_html(commit)}">{_html(commit[:12])}</code></dd></div>'
                if commit else '')
    evidence = _mapping(_mapping(draft.get('additions')).get('evidence'))
    anchors = {path: f'source-{index}' for index, path in enumerate(evidence, 1)}
    additions = _draft_groups(draft, anchors)
    relations = _declared_relations(draft, anchors)
    relation_link = '<a href="#relations">关联</a>' if relations else ''
    relation_section = (f'<section id="relations" class="detail-section" tabindex="-1">'
                        f'<h3>明确声明的关联</h3>{relations}</section>' if relations else '')
    events = data.get('history') or []
    history = _history(reversed(events))
    latest = _mapping(events[-1]) if events else {}
    last_review = (f'<div><dt>最近审阅</dt><dd><a href="#history">'
                   f'{_time(latest.get("created_at") or latest.get("at"))}</a></dd></div>' if latest else '')
    related = _related(row, query, state, order, page)
    index = next((i for i, entry in enumerate(visible) if entry.get('id') == selected), None)
    previous = visible[index - 1]['id'] if index is not None and index > 0 else None
    following = visible[index + 1]['id'] if index is not None and index + 1 < len(visible) else None
    back_id = _html(selected) if index is not None else 'queue'
    neighbors = ''.join(f'<a href="{_url(query, state, rid, order, page)}#receipt-detail">{label}</a>'
                        for label, rid in (('← 上一份', previous), ('下一份 →', following)) if rid)
    return (f'<header class="panel-head"><a class="back-results" href="#{back_id}">← 返回投稿队列</a><p class="eyebrow">投稿详情</p>'
            f'<div class="detail-top">{status}<nav aria-label="相邻投稿">{neighbors}</nav></div>'
            f'<h2 id="detail-title">{title}</h2>{note}{related}'
            f'<dl class="meta"><div><dt>投稿署名</dt><dd>{credit}</dd></div>'
            f'<div><dt>收件时间</dt><dd>{received}</dd></div>{last_review}{baseline}'
            f'<div class="wide"><dt>回执 ID</dt><dd><code class="receipt-id">{_html(selected)}</code></dd></div></dl></header>'
            '<nav class="local-nav" aria-label="详情导航"><a href="#summary">审查摘要</a>'
            f'<a href="#measurements">指标与覆盖</a>{relation_link}<a href="#evidence">原始内容</a>'
            '<a href="#history">审阅历史</a></nav>'
            f'<div class="content">'
            f'<section id="summary" class="detail-section" tabindex="-1"><h3>归一化摘要</h3>{normalized}</section>'
            f'<section id="measurements" class="detail-section" tabindex="-1"><h3>对比指标与覆盖</h3>{_comparison_groups(draft)}</section>'
            f'{relation_section}<section id="evidence" class="detail-section" tabindex="-1">'
            f'<h3>投稿原始内容</h3>{additions}</section>'
            f'<section id="history" class="detail-section" tabindex="-1"><h3>审阅历史</h3>{history}</section></div>')


def render_unavailable(*, remote):
    advice = ('检查社区服务器的 SSH 连接和读取权限，然后重试。' if remote else
              '检查本机私有状态目录及其读取权限，然后重试。')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>暂时无法读取收件箱 · NumericBridge</title><style>{STYLE}</style></head>
<body><header class="hero"><div class="wrap brandline"><span class="brand">NUMERICBRIDGE</span>
<span class="private">私有 · 只读</span></div></header>
<main class="wrap"><section class="panel recovery" aria-labelledby="recovery-title">
<p class="eyebrow">读取未完成</p><h1 id="recovery-title">暂时无法读取收件箱</h1>
<p class="muted">{_html(advice)} 这不会修改投稿或审阅记录。</p>
<a class="refresh" href="">重试当前视图</a></section></main></body></html>'''


def render(data, *, query='', state='', order='newest', page=None):
    """Render the requested queue view and the server-selected receipt.

    Counts cover all supplied rows; search and status filters affect only the queue.
    The optional normalized summary and review history belong to the selected receipt.
    """
    rows = [row for row in data.get('rows') or () if isinstance(row, dict)]
    selected = _text(data.get('selected'))
    query, state = _text(query).strip(), _text(state)
    visible = visible_rows(rows, query, state, order)
    row = next((row for row in rows if selected and _text(row.get('id')) == selected), None)
    pages = max(1, (len(visible) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(data.get('page', 1) if page is None else page, 1), pages)
    page_rows = visible[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    controls = _controls(rows, query, state, order)
    title_counts = Counter(_text(item.get('title'), '未命名投稿') for item in rows)
    queue = _queue(page_rows, len(rows), selected if row is not None else '', query, state, order,
                   page, pages, len(visible), title_counts)
    detail = _detail(data, row, selected, row is not None and row not in visible, query, state, visible, order, page)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>投稿审查工作台 · NumericBridge</title>
<style>{STYLE}</style>
</head>
<body>
<a class="skip" href="#main">跳至主要内容</a>
<header class="hero"><div class="wrap">
<div class="brandline"><span class="brand"><span class="brand-mark" aria-hidden="true">N</span> NUMERICBRIDGE</span>
<span class="private">私有 · 只读</span></div>
</div></header>
<main id="main" class="wrap" tabindex="-1">
<div class="pagehead"><div><p class="eyebrow">社区后台 / 数据审查</p><h1>投稿审查工作台</h1>
<p class="muted">核对评测、证据与审阅记录。此页面不会修改投稿状态。</p></div>
<a class="refresh" href="{_url(query, state, selected, order, page)}">刷新收件箱</a></div>
<section class="controls" aria-label="投稿筛选">{controls}</section>
<div class="layout">{queue}
<section id="receipt-detail" class="panel detail" aria-labelledby="detail-title" tabindex="-1">{detail}</section>
</div>
</main>
</body>
</html>'''

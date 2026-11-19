#!/usr/bin/env python3
"""Build a local, offline map of a personal-toolkit checkout, without importing it."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

GROUPS = {
    'Coordination': {'personal-orchestrator'},
    'Engineering': {'engineering-delivery', 'code-assurance', 'project-contracts', 'delivery-operations', 'shopify-ruby'},
    'Frontend and browser': {'design-taste-frontend', 'ui-ux-pro-max', 'impeccable', 'clone-any-website', 'playwright-cli'},
    'Research and science': {'math-research', 'sympy', 'experimental-design', 'ml-experiment-standards', 'research-software-engineering', 'performance-speedup'},
    'Knowledge and writing': {'project-knowledge', 'codebase-map', 'research-summarizer', 'humanizer', 'writing-clearly-and-concisely', 'internal-comms'},
    'External tools and discovery': {'context7-mcp', 'exa-research', 'supabase', 'supabase-postgres-best-practices', 'find-skills'},
    'Explicit workflows': {'grill-me', 'grilling', 'codex-autoresearch', 'open-ralph-wiggum'},
}
IGNORED = {'.git', 'node_modules', '__pycache__', '.venv', 'installation-backups', '.work'}
GENERATED = {'toolkit-map.json', 'toolkit-map.md', 'toolkit-map.html'}


def inventory(root):
    """Include tracked and visible untracked sources; omit ignored/generated output."""
    try:
        output = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root)
        paths = [root / name for name in output.decode().split('\0') if name]
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        paths, revision = list(root.rglob('*')), None
    files = {}
    for path in sorted(set(paths)):
        rel = path.relative_to(root)
        if set(rel.parts) & IGNORED or path.name in GENERATED or path.is_symlink() or not path.is_file():
            continue
        files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files, revision


def imports(root, files):
    """Static local Python imports only; no claim about dynamic call reachability."""
    edges, parsed, errors = [], [], []
    available = set(files)
    for name in sorted(available):
        if not name.endswith('.py'):
            continue
        try:
            tree = ast.parse((root / name).read_bytes(), filename=name)
        except (SyntaxError, UnicodeError) as error:
            errors.append({'path': name, 'error': type(error).__name__})
            continue
        parsed.append(name)
        path = Path(name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            roots = [path.parent]
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                roots += list(path.parent.parents)
            elif node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                roots = [base]
                modules = [node.module] if node.module else [alias.name for alias in node.names]
            else:
                modules = [node.module] if node.module else []
                roots += list(path.parent.parents)
            for module in modules:
                for base in roots:
                    stem = base.joinpath(*module.split('.'))
                    matches = [stem.as_posix() + '.py', (stem / '__init__.py').as_posix()]
                    target = next((candidate for candidate in matches if candidate in available), None)
                    if target:
                        if target != name:
                            edges.append({'from': name, 'to': target, 'relation': 'imports',
                                          'evidence': {'path': name, 'line': node.lineno}, 'certainty': 'static-source'})
                        break
    unique = {(edge['from'], edge['to'], edge['evidence']['line']): edge for edge in edges}
    return list(unique.values()), parsed, errors


def skill_description(entry):
    """Read the scalar forms used by this bundle without adding a YAML runtime."""
    lines = entry.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('Missing skill frontmatter: ' + str(entry))
    for index, line in enumerate(lines[1:], 1):
        if line == '---':
            break
        if not line.startswith('description:'):
            continue
        value = line.split(':', 1)[1].strip()
        if value in ('>', '>-', '>+', '|', '|-', '|+'):
            parts = []
            for continuation in lines[index + 1:]:
                if continuation and not continuation[0].isspace():
                    break
                parts.append(continuation.strip())
            return ' '.join(part for part in parts if part)
        if value.startswith('"') and value.endswith('"'):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value[1:-1]
        return value.strip("'")
    raise ValueError('Missing skill description: ' + str(entry))


def build(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    files, revision = inventory(root)
    edges, parsed, errors = imports(root, files)
    skills = []
    for item in manifest['skills']:
        folder = item['bundle_path']
        entry = root / folder / 'SKILL.md'
        if not entry.is_file():
            raise ValueError('Missing skill entrypoint: ' + folder)
        description = skill_description(entry)
        groups = [group for group, members in GROUPS.items() if item['name'] in members]
        if len(groups) != 1:
            raise ValueError('Skill needs one map group: ' + item['name'])
        skills.append({'id': item['name'], 'group': groups[0], 'path': folder, 'entrypoint': folder + '/SKILL.md',
                       'description': description, 'default': item.get('enabled', True),
                       'source': item.get('source'), 'pin': item.get('commit', item.get('package', item.get('version'))),
                       'file_count': sum(name.startswith(folder + '/') for name in files)})
    implementation = 'skills/personal-orchestrator/scripts/orchestrator/'
    metadata = json.loads((root / implementation / 'metadata.json').read_text())
    policy = json.loads((root / implementation / 'policy.json').read_text())
    services = [{'id': item['name'], 'kind': 'hosted MCP', 'url': item['url'], 'enabled': item.get('enabled', True)}
                for item in manifest.get('mcp_servers', [])]
    tools = [{'id': item['name'], 'kind': 'npm runtime', 'pin': item['version'], 'enabled': item.get('enabled', True)}
             for item in manifest.get('npm', [])]
    tools += [{'id': 'Supabase CLI', 'kind': 'external program', 'pin': manifest['supabase']['version'], 'enabled': True},
              {'id': 'Codex', 'kind': 'model engine and account', 'pin': 'host-managed', 'enabled': True},
              {'id': 'Python / science libraries', 'kind': 'external runtime', 'pin': 'see runtime locks', 'enabled': True},
              {'id': 'Browser', 'kind': 'external program', 'pin': 'reuse installed browser', 'enabled': True}]
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {'schema_version': 1, 'generated_utc': datetime.now(timezone.utc).isoformat(), 'revision': revision,
            'worktree_fingerprint': fingerprint, 'skills': skills, 'roles': policy['roles'], 'limits': policy['limits'],
            'runtime_metadata': metadata, 'external': services + tools, 'files': files,
            'imports': edges, 'python_parsed': parsed, 'parse_errors': errors,
            'coverage': 'File inventory and static Python import edges. Workflow/grouping is a curated design view. '
                        'No whole-repository semantic call graph, dynamic imports, JS parsing, or live enforcement proof.',
            'excluded': sorted(IGNORED | GENERATED)}


def markdown(data):
    prefix = data.get('repository_link', '..') + '/'
    docs = prefix + 'docs/'
    lines = ['# Personal toolkit map', '', 'Generated from the current source tree. Installed state and live execution are separate.', '',
             '- Source revision: `' + str(data['revision']) + '`; worktree fingerprint: `' + data['worktree_fingerprint'] + '`.',
             '- ' + str(len(data['skills'])) + ' local skills; ' + str(len(data['files'])) + ' inventoried files; '
             + str(len(data['python_parsed'])) + ' Python files parsed; ' + str(len(data['imports'])) + ' local import edges.',
             '- Live orchestration verified: **' + str(data['runtime_metadata'].get('live_execution_verified', False)) + '**.',
             '- [Searchable map](toolkit-map.html) and [complete inventory and edge evidence](toolkit-map.json).', '',
             '## Task flow', '', 'This is the intended workflow; it does not prove the execution adapter is live.', '',
             '```mermaid', 'flowchart TD', '  User[Your request] --> C[Coordinator]',
             '  C -->|Small known task| D[Direct edit and checks]', '  C -->|Delegation helps| P[Scoped packet and relevant skill]',
             '  P --> Gate[Accepted prerequisites, ownership and budget checks]', '  Gate --> I[Implementer]',
             '  Gate --> Q[Read-only investigator]', '  Gate --> R[Read-only reviewer]',
             '  I --> E[Evidence and handoff]', '  Q --> E', '  R --> E', '  D --> E',
             '  E --> C', '  C -->|Acceptance verified or limit reached| Stop[Finish or report incomplete]',
             '  Tools[External engine, services and programs] -.-> I', '  Tools -.-> Q', '  Tools -.-> R', '```', '',
             'The roles are alternatives selected when useful. Defaults are one active worker, '
             + str(data['limits']['max_calls']) + ' launches and ' + str(data['limits']['max_retries'])
             + ' retries per packet. Observed tokens are not an in-flight provider cap.', '', '## Skill groups', '']
    for group in GROUPS:
        lines += ['### ' + group, '', '| Skill | Selection | Local files |', '|---|---|---|']
        for skill in data['skills']:
            if skill['group'] == group:
                lines += ['| [' + skill['id'] + '](' + prefix + quote(skill['entrypoint']) + ') | '
                          + ('Default' if skill['default'] else 'Optional') + ' | ' + str(skill['file_count']) + ' |']
        lines += ['']
    lines += ['## External dependencies', '', '| Dependency | Kind | Pin / requirement |', '|---|---|---|']
    lines += ['| ' + item['id'] + ' | ' + item['kind'] + ' | ' + item.get('pin', item.get('url', '')) + ' |'
              for item in data['external']]
    lines += ['', '## Interpretation and limits', '', data['coverage'], '',
              'A file existing is not proof it is installed or usable with this account. The graph stores '
              'dependent → dependency import edges with source file and line. Missing edges are not proof of independence.', '',
              'Graphify/GitNexus engines and an Obsidian plugin are not bundled. Codebase Map and Project Knowledge '
              'provide the corresponding source-mapping and Markdown workflows. Hosted services remain external.', '',
              '[Execution design and current boundaries](' + docs + 'owned-orchestrator.md) · '
              '[Capability coverage](' + docs + 'capability-coverage.md) · [Expert-practice review]('
              + docs + 'references/orchestration-practice-review-2026-10-25.md) · '
              '[Nine-workflow research and bounded improvements](' + docs + 'research/workflow-design-2026-10-25.md)', '']
    return '\n'.join(lines)


def webpage(data):
    payload = json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Personal toolkit map</title><style>
:root{font-family:system-ui,sans-serif;color:#1f2933;background:#f5f4ef}body{max-width:1200px;margin:auto;padding:36px 24px}h1{font-size:36px;margin-bottom:8px}h2{margin-top:32px}.muted{color:#56636e}.notice{border-left:4px solid #bd6a23;background:#fff4df;padding:14px 18px;border-radius:4px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:24px 0}button,input{font:inherit;border:1px solid #b9c3c6;background:white;border-radius:6px;padding:10px 14px}button{cursor:pointer}button[aria-selected=true]{background:#174d4b;color:white}input{width:min(500px,90%)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.card{background:white;padding:18px;border:1px solid #d7deda;border-radius:8px}.card h3{margin:0 0 8px;font-size:18px}.card p{font-size:14px;line-height:1.5}.tag{font-size:12px;color:#174d4b;font-weight:600}.flow{display:flex;align-items:stretch;gap:12px;flex-wrap:wrap}.flow .card{flex:1;min-width:170px}.arrow{align-self:center;color:#527774}a{color:#125b74;overflow-wrap:anywhere}li{margin:8px 0}code{overflow-wrap:anywhere;font-size:12px}section[hidden]{display:none}summary{cursor:pointer}.small{font-size:12px}footer{border-top:1px solid #c8d0cd;margin-top:36px;padding-top:16px}
</style><h1>Your personal toolkit</h1><p class="muted">Local skill forks, one coordinator, and explicit connections to outside tools.</p>
<p class="notice" id="readiness"></p><nav aria-label="Map layers"><button data-tab="workflow" aria-selected="true">Workflow</button><button data-tab="skills" aria-selected="false">All skills</button><button data-tab="code" aria-selected="false">Code dependencies</button><button data-tab="external" aria-selected="false">External tools</button></nav>
<section id="workflow"><h2>Choose the amount of process the task needs</h2>
<svg viewBox="0 0 1040 430" role="img" aria-labelledby="flow-title flow-description" style="width:100%;min-width:300px;background:white;border:1px solid #d7deda;border-radius:8px">
<title id="flow-title">Request, coordinator, direct work or bounded worker, then verified evidence</title>
<desc id="flow-description">A small known task goes directly from the coordinator to checks. A complex task is split into scoped packets and passes a policy gate before one implementer, investigator, or reviewer works. Evidence returns to the coordinator. Retry needs new evidence and remaining budget.</desc>
<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#527774"/></marker></defs>
<g fill="none" stroke="#527774" stroke-width="2" marker-end="url(#arrow)"><path d="M165 210H205"/><path d="M385 210H430V80H490"/><path d="M430 210V290H490"/><path d="M700 290H750"/><path d="M860 250V120H790"/><path d="M700 80H750"/><path d="M790 40V22H295V150"/></g>
<g font-family="system-ui,sans-serif" text-anchor="middle" fill="#1f2933">
<rect x="25" y="180" width="140" height="60" rx="8" fill="#e6f0ed"/><text x="95" y="215" font-size="18">Your request</text>
<rect x="205" y="150" width="180" height="120" rx="8" fill="#174d4b"/><text x="295" y="190" fill="white" font-size="20">Coordinator</text><text x="295" y="220" fill="white" font-size="14">Scope + acceptance</text><text x="295" y="244" fill="white" font-size="14">Owns dispatch</text>
<rect x="490" y="40" width="210" height="80" rx="8" fill="#e6f0ed"/><text x="595" y="73" font-size="18">Direct work</text><text x="595" y="100" font-size="14">Small, known task</text>
<rect x="490" y="240" width="210" height="100" rx="8" fill="#e6f0ed"/><text x="595" y="273" font-size="18">Scoped packets</text><text x="595" y="299" font-size="14">Ownership + relevant skill</text><text x="595" y="321" font-size="14">Policy + budget gate</text>
<rect x="750" y="250" width="230" height="100" rx="8" fill="#e6f0ed"/><text x="865" y="280" font-size="18">One worker</text><text x="865" y="307" font-size="14">Implement / investigate / review</text><text x="865" y="332" font-size="14">No child dispatch</text>
<rect x="750" y="40" width="230" height="80" rx="8" fill="#e6f0ed"/><text x="865" y="73" font-size="18">Checks + evidence</text><text x="865" y="100" font-size="14">Compact handoff</text>
<text x="520" y="170" font-size="13">Return evidence; coordinator decides</text><text x="520" y="193" font-size="13">Finish, next packet, or bounded retry</text>
<text x="520" y="395" font-size="14">Retry only with new evidence. Stop on acceptance, a limit, or a real blocker.</text></g></svg>
<h2>Available worker roles</h2><div class="grid" id="roles"></div><p id="limits"></p><p class="muted">Role permissions shown here are requested execution settings; their presence in a profile is not proof of enforcement.</p>
<h2>Capability map</h2><p class="muted">Select a group to explore its editable skills. These are capabilities loaded when needed, not extra agents.</p><div class="grid" id="groups"></div></section>
<section id="skills" hidden><h2>Editable local skill folders</h2><input id="search" aria-label="Search skills" placeholder="Find a skill or capability"><p class="muted" id="skill-count"></p><div class="grid" id="skill-grid"></div></section>
<section id="code" hidden><h2>Source-backed import map</h2><p class="muted">Each item shows a Python file and the local modules it imports. Expand to inspect the evidence. Dynamic imports and non-Python code are outside this parse.</p><input id="code-search" aria-label="Search source dependencies" placeholder="Filter source paths"><p id="code-count"></p><div id="dependencies"></div></section>
<section id="external" hidden><h2>Outside the skill folders</h2><p>These engines, programs and services keep their own installation, account and access requirements. Local forks do not replace them.</p><div class="grid" id="external-grid"></div></section>
<footer><p id="coverage"></p><p class="small" id="snapshot"></p><a href="toolkit-map.md">Readable map</a> · <a href="toolkit-map.json">Full evidence JSON</a> · <a href="owned-orchestrator.md">Execution boundaries</a></footer>
<script id="data" type="application/json">''' + payload + '''</script><script>
const data=JSON.parse(document.getElementById('data').textContent);const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const link=(path,label)=>'<a href="'+esc(data.repository_link||'..')+'/'+path.split('/').map(encodeURIComponent).join('/')+'">'+esc(label)+'</a>';
document.getElementById('readiness').textContent=data.runtime_metadata.live_execution_verified?'Live execution is marked verified in source metadata. Check the current verification report for scope.':'Local implementation is present; live orchestration is not verified. Use the direct workflow until its account, permission and platform gates pass.';
document.getElementById('limits').textContent='Default limits: '+data.limits.max_active_workers+' active worker; '+data.limits.max_calls+' launches; '+data.limits.max_retries+' retries per packet. Token limits use observed usage, not a hard server cap.';
document.getElementById('roles').innerHTML=Object.entries(data.roles).filter(([k])=>k!=='coordinator').map(([name,role])=>'<div class="card"><h3>'+esc(name)+'</h3><p>'+esc(role.sandbox)+'</p><p>Presets: '+Object.entries(role.presets).map(([p,v])=>esc(p)+': '+esc(v.model||'inherit selected model')).join('; ')+'</p><span class="tag">No dispatch authority</span></div>').join('');
function skills(){const query=document.getElementById('search').value.toLowerCase();const rows=data.skills.filter(x=>[x.id,x.group,x.description].join(' ').toLowerCase().includes(query));document.getElementById('skill-count').textContent=rows.length+' of '+data.skills.length+' skills';document.getElementById('skill-grid').innerHTML=rows.map(x=>'<article class="card"><span class="tag">'+esc(x.group)+' · '+(x.default?'default':'optional')+'</span><h3>'+link(x.entrypoint,x.id)+'</h3><p>'+esc(x.description)+'</p><p class="small">'+x.file_count+' local files · source pin '+esc(x.pin||'local')+'</p></article>').join('');}
function code(){const query=document.getElementById('code-search').value.toLowerCase();const groups={};data.imports.forEach(x=>{(groups[x.from]??=[]).push(x)});const names=Object.keys(groups).filter(x=>x.toLowerCase().includes(query)).sort();document.getElementById('code-count').textContent=data.python_parsed.length+' parsed Python files; '+data.imports.length+' observed local import edges; '+names.length+' matching dependent files.';document.getElementById('dependencies').innerHTML=names.map(name=>'<details class="card"><summary>'+esc(name)+'</summary><ul>'+groups[name].map(edge=>'<li>'+link(edge.to,edge.to)+' <span class="muted">← imported at line '+edge.evidence.line+'</span></li>').join('')+'</ul></details>').join('');}
document.getElementById('external-grid').innerHTML=data.external.map(x=>'<article class="card"><span class="tag">'+esc(x.kind)+(x.enabled?'':' · optional')+'</span><h3>'+esc(x.id)+'</h3><p>'+esc(x.pin||x.url||'')+'</p></article>').join('');
function tab(id){document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===id)));document.querySelectorAll('section').forEach(s=>s.hidden=s.id!==id)}
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>tab(button.dataset.tab)));
document.getElementById('groups').innerHTML=[...new Set(data.skills.map(x=>x.group))].map(group=>'<button class="card" data-group="'+esc(group)+'"><strong>'+esc(group)+'</strong><p>'+data.skills.filter(x=>x.group===group).length+' skills</p></button>').join('');
document.querySelectorAll('[data-group]').forEach(button=>button.addEventListener('click',()=>{document.getElementById('search').value=button.dataset.group;skills();tab('skills');document.getElementById('search').focus()}));
document.querySelector('footer a[href="owned-orchestrator.md"]').href=(data.repository_link||'..')+'/docs/owned-orchestrator.md';
document.getElementById('search').addEventListener('input',skills);document.getElementById('code-search').addEventListener('input',code);skills();code();document.getElementById('coverage').textContent=data.coverage;document.getElementById('snapshot').textContent='Generated '+data.generated_utc+' · source fingerprint '+data.worktree_fingerprint;
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.repository.resolve()
    output = (args.output or root / 'docs').resolve()
    data = build(root)
    data['repository_link'] = quote(Path(os.path.relpath(root, output)).as_posix(), safe='/')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'toolkit-map.json').write_text(json.dumps(data, indent=2) + '\n')
    (output / 'toolkit-map.md').write_text(markdown(data))
    (output / 'toolkit-map.html').write_text(webpage(data))
    print(json.dumps({'skills': len(data['skills']), 'files': len(data['files']), 'python_files': len(data['python_parsed']),
                      'import_edges': len(data['imports']), 'parse_errors': data['parse_errors'], 'output': str(output)}))


if __name__ == '__main__':
    main()

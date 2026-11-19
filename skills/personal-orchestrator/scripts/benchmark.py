#!/usr/bin/env python3
"""Offline paired admission benchmark. No model execution, network or global writes."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path, PurePosixPath
import platform
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time


def proposal(identifier='next', role='implementer', paths=None):
    return {'schema_version': 1, 'id': identifier, 'packet': identifier,
            'requester': {'role': 'coordinator', 'depth': 0}, 'role': role,
            'objective': 'Implement the specified bounded change.',
            'scope': {'paths': paths or ['src/parser.py'], 'bounded': True,
                      'consequence': 'low', 'uncertainty': 'low'},
            'acceptance_check': 'Run the fixed regression and inspect affected consumers.', 'evidence': []}


def cases():
    rows = []
    def add(name, expected, current=None, prior=None, capacity=2, status='running', guidance=None, category='shared-schema'):
        rows.append({'name': name, 'expected': expected, 'proposal': current or proposal(),
                     'prior': prior, 'capacity': capacity, 'status': status,
                     'guidance': guidance or [], 'category': category})
    add('empty session', 'allowed')
    add('exact writer overlap', 'denied', prior=proposal('prior'))
    add('directory writer overlaps reader', 'denied', proposal(role='reviewer'), proposal('prior', paths=['src']))
    add('reader directory overlaps writer', 'denied', proposal(paths=['src']), proposal('prior', 'investigator'))
    add('case alias overlap', 'denied', proposal(paths=['SRC/Parser.py']), proposal('prior'))
    add('root scope overlap', 'denied', proposal(paths=['.']), proposal('prior'))
    add('shared read-only scope', 'allowed', proposal(role='reviewer'), proposal('prior', 'investigator'))
    add('independent writer', 'allowed', proposal(paths=['docs/guide.md']), proposal('prior'))
    add('prefix neighbors are independent', 'allowed', proposal(paths=['src/ab']), proposal('prior', paths=['src/a']))
    add('default one-worker limit', 'denied', proposal(paths=['docs/guide.md']), proposal('prior'), capacity=1)
    add('pending reservation blocks', 'denied', proposal(paths=['docs/guide.md']), proposal('prior'), status='pending')
    add('accepted scope is released', 'allowed', prior=proposal('prior'), status='completed')
    oversized = proposal()
    oversized['evidence'] = [{'fingerprint': format(i, '064x'), 'summary': 'x' * 1000} for i in range(16)]
    add('oversized full prompt', 'denied', oversized, guidance=[{'name': 'fixture', 'text': 'x' * 16000}])
    child = proposal(); child['requester'] = {'role': 'implementer', 'depth': 1}
    add('child delegation denied', 'denied', child)
    risky = proposal(); risky['preset'] = 'fast'; risky['scope']['consequence'] = 'high'
    add('risky cheap preset denied', 'denied', risky)
    dep = proposal(); dep['depends_on'] = ['prior']
    add('accepted prerequisite', 'allowed', dep, proposal('prior'), status='completed', category='new-schema')
    add('missing prerequisite', 'denied', dep, category='new-schema')
    a, b = proposal(paths=['docs/a.md']), proposal('prior')
    a['scope']['resources'] = ['fixture-db']; b['scope']['resources'] = ['fixture-db']
    add('exclusive resource overlap', 'denied', a, b, category='new-schema')
    compact = proposal()
    compact['context'] = {'snapshot': 'fixture revision; revalidate current source', 'summary': 'The parser fails for empty input.',
                          'references': [{'path': 'src/parser.py', 'start_line': 1, 'end_line': 30, 'reason': 'Inspect the guard.'}],
                          'failed_approaches': ['Caller-only guard missed another consumer.']}
    add('compact continuity context', 'allowed', compact, category='new-schema')
    return rows


def worker(scripts, repetitions):
    sys.path.insert(0, str(scripts))
    from orchestrator import __version__, runtime
    from orchestrator.execution import packet_prompt
    from orchestrator.policy import load_policy
    rows = cases()
    def prepare(case):
        policy = load_policy(); policy['limits']['max_active_workers'] = case['capacity']
        document = {'calls': [], 'launches': {}, 'policy': policy, 'model': 'selected-model', 'effort': 'high', 'revision': 0}
        if case['prior']:
            prior = case['prior']
            document['calls'].append({'id': prior['id'], 'packet': prior['packet'], 'agent': prior['role'],
                                      'status': case['status'], 'usage': None, 'preset': 'standard', 'evidence': []})
            document['launches'][prior['id']] = {'proposal': prior}
        return document, runtime._guidance(case['guidance'])
    prepared = [prepare(row) for row in rows]
    def evaluate(case, pair):
        try:
            decision, _ = runtime._new_launch(pair[0], case['proposal'], pair[1])
            return {'outcome': 'allowed' if decision['allowed'] else 'denied', 'reasons': decision['stop_reasons']}
        except ValueError as error:
            unsupported = case['category'] == 'new-schema' and 'invalid fields' in str(error)
            oversized = case['name'] == 'oversized full prompt' and str(error) == 'worker prompt exceeds 32000-character budget; narrow the packet'
            return {'outcome': 'unsupported' if unsupported else 'denied' if oversized else 'error', 'reasons': [str(error)]}
    results = [dict(name=row['name'], category=row['category'], expected=row['expected'], **evaluate(row, pair))
               for row, pair in zip(rows, prepared)]
    # Timing only supported common-schema cases; repeat counts are not correctness trials.
    shared = [(row, pair) for row, pair in zip(rows, prepared) if row['category'] == 'shared-schema']
    for _ in range(10):
        for row, pair in shared: evaluate(row, pair)
    samples = []
    for _ in range(7):
        start = time.perf_counter_ns()
        for _ in range(repetitions):
            for row, pair in shared: evaluate(row, pair)
        samples.append((time.perf_counter_ns() - start) / (repetitions * len(shared)) / 1000)
    allowed = [(row, pair) for row, pair in shared if row['expected'] == 'allowed']
    allowed_samples = []
    for _ in range(7):
        start = time.perf_counter_ns()
        for _ in range(repetitions):
            for row, pair in allowed: evaluate(row, pair)
        allowed_samples.append((time.perf_counter_ns() - start) / (repetitions * len(allowed)) / 1000)
    prompt = packet_prompt({'proposal': proposal(), 'guidance': runtime._guidance([])})
    return {'version': __version__, 'cases': results, 'common_prompt_characters': len(prompt),
            'common_prompt_utf8_bytes': len(prompt.encode()), 'admission_us_per_call_batches': samples,
            'median_admission_us': statistics.median(samples), 'repetitions_per_batch': repetitions,
            'allowed_us_per_call_batches': allowed_samples, 'allowed_case_count': len(allowed),
            'median_allowed_us': statistics.median(allowed_samples),
            'case_count': len(rows), 'token_usage': None, 'model_calls': 0}


def archive_skill(repo, revision, destination):
    prefix = 'skills/personal-orchestrator/'
    raw = subprocess.check_output(['git', '-C', str(repo), 'archive', revision, 'skills/personal-orchestrator'])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not member.name.startswith(prefix):
                if member.isdir(): continue
                raise ValueError('unexpected archive path')
            if member.isdir(): continue
            if not member.isfile(): raise ValueError('benchmark archives accept regular source files only')
            target = destination.joinpath(*PurePosixPath(member.name[len(prefix):]).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def report(data):
    old, new = data['baseline'], data['candidate']
    shared = [row for row in new['cases'] if row['category'] == 'shared-schema']
    score = lambda run: sum(x['outcome'] == x['expected'] for x in run['cases'] if x['category'] == 'shared-schema')
    lines = ['# Offline orchestrator comparison', '',
             'This measures deterministic controller admission, not model quality, completed coding tasks or provider cost.', '',
             f"- Baseline: `{data['baseline_revision']}` ({old['version']}).",
             f"- Candidate: `{data['candidate_revision']}` ({new['version']}).",
             f"- Environment: {data['environment']}; Python {data['python']}.",
             f"- Shared-schema scenarios satisfying the stated contract: **{score(old)}/{len(shared)} → {score(new)}/{len(shared)}**.",
             f"- Identical minimal task prompt: **{old['common_prompt_characters']} → {new['common_prompt_characters']} characters**.",
             '- No tokenizer measurements or paid model calls; usage remains unknown.', '',
             '| Scenario | Expected | Baseline | Candidate |', '|---|---|---|---|']
    for a, b in zip(old['cases'], new['cases']):
        lines.append(f"| {b['name']} ({b['category']}) | {b['expected']} | {a['outcome']} | {b['outcome']} |")
    lines += ['', '## Admission timing', '',
              f"Seven batches, {new['repetitions_per_batch']} repetitions of each of {len(shared)} shared cases per batch. "
              'Timings include in-memory validation, copies and prompt serialization; exclude imports, filesystem, process/model startup and execution.',
              f"Mixed admitted/rejected cases, median batch mean: **{old['median_admission_us']:.1f} → {new['median_admission_us']:.1f} microseconds per admission**. "
              'This mix performs different work because the candidate rejects cases the baseline admits.',
              f"For the {new['allowed_case_count']} identical cases admitted by both versions: **{old['median_allowed_us']:.1f} → {new['median_allowed_us']:.1f} microseconds** (median batch mean).",
              'The parent alternates baseline/candidate order across three isolated process pairs; raw samples and per-process results are in JSON.', '',
              '## Interpretation', '',
              'The fixtures deliberately exercise changed controls, so this score is a diagnostic coverage result, not a representative task-success rate. '
              'Each scenario counts once; timing repetitions do not create independent correctness evidence.',
              'Unknown new fields are reported as unsupported for the baseline, rather than penalized as failures. '
              'Prompt overhead may increase because the candidate carries explicit freshness guidance. '
              'Context limits prevent oversized requests; they do not establish token savings.',
              'Scope matching is lexical and declared. These cases do not test filesystem containment, aliases through links, '
              'live account behavior, native Windows execution, source freshness, or human review effort.',
              'A real quality/cost comparison still needs matched coding tasks, fixed acceptance checks, '
              'same model/settings, repair/review accounting and observed usage.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--baseline-ref')
    parser.add_argument('--candidate-ref', default='HEAD')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--repetitions', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 1000: parser.error('repetitions must be 1..1000')
    if args.worker:
        print(json.dumps(worker(args.worker, args.repetitions))); return
    if not args.repo or not args.baseline_ref or not args.output: parser.error('repo, baseline-ref and output are required')
    repo = args.repo.resolve()
    def commit(ref):
        return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', '--end-of-options', ref+'^{commit}'], text=True).strip()
    revisions = {'baseline': commit(args.baseline_ref), 'candidate': commit(args.candidate_ref)}
    runs = {'baseline': [], 'candidate': []}
    with tempfile.TemporaryDirectory(prefix='orchestrator-benchmark-') as temporary:
        root = Path(temporary)
        for name, revision in revisions.items(): archive_skill(repo, revision, root/name)
        for order in (('baseline','candidate'), ('candidate','baseline'), ('baseline','candidate')):
            for name in order:
                command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', str(root/name/'scripts'), '--repetitions', str(args.repetitions)]
                runs[name].append(json.loads(subprocess.check_output(command, text=True, timeout=45)))
    data = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'environment': platform.platform(),
            'python': platform.python_version(), 'baseline_revision': revisions['baseline'], 'candidate_revision': revisions['candidate'],
            'process_runs': runs}
    for name in runs:
        data[name] = deepcopy(runs[name][0])
        samples = [value for run in runs[name] for value in run['admission_us_per_call_batches']]
        data[name]['median_admission_us'] = statistics.median(samples)
        data[name]['median_allowed_us'] = statistics.median([value for run in runs[name] for value in run['allowed_us_per_call_batches']])
        assert all(run['cases'] == data[name]['cases'] for run in runs[name]), 'nondeterministic case outcomes'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix('.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(report(data), encoding='utf-8')
    print(report(data))


if __name__ == '__main__':
    main()

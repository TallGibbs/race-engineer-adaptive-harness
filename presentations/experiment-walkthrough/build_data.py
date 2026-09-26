"""Build an offline, report-backed presentation. Does not execute the harness."""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
OUTPUT = REPO / 'docs' / 'experiment-walkthrough' / 'index.html'
# Keep the historical evidence and its GitHub citations reproducible as main advances.
SOURCE_COMMIT = '059e075b1eb6467b5acf871b77e431f934f9bf30'

def section(text, heading, next_heading='## '):
    start = text.index(heading) + len(heading)
    end = text.find('\n' + next_heading, start)
    return text[start:end if end >= 0 else len(text)].strip()

def table_after(text, heading):
    block = text[text.index(heading) + len(heading):]
    lines = block.splitlines()
    table = []
    began = False
    for line in lines:
        if line.startswith('|'):
            began = True
            cells = [c.strip() for c in line.strip('|').split('|')]
            if not all(re.fullmatch(r'[: -]+', c) for c in cells):
                table.append(cells)
        elif began:
            break
    return [dict(zip(table[0], row)) for row in table[1:]]

sources = {}
for f in ['reports/exp1.md', 'reports/exp2.md', 'reports/exp2.notes.json',
          'configs/v1.json', 'data/cases.json', 'SPECIFICATION.md', 'CONTRACT.md',
          'adaptive_harness/runner/coordinator.py', 'adaptive_harness/runner/context.py',
          'adaptive_harness/runner/recorder.py', 'adaptive_harness/dmaic/history.py',
          'adaptive_harness/dmaic/improve.py', 'adaptive_harness/store/mongo.py',
          'adaptive_harness/store/records.py']:
    sources[f] = subprocess.check_output(
        ['git', 'show', f'{SOURCE_COMMIT}:{f}'], cwd=REPO,
        text=True, encoding='utf-8',
    )

experiments = {}
for eid in ['exp1', 'exp2']:
    raw = sources[f'reports/{eid}.md']
    analyze = section(raw, '## Analyze')
    why = re.search(r'Why-chain:\s*(.*?)\nOrigin event', analyze, re.S).group(1)
    source_events = re.search(r'Source events: (.*)', analyze).group(1)
    proposal = section(raw, '### Proposal\n', '### ')
    experiments[eid] = {
        'id': eid, 'candidate': 'v2' if eid == 'exp1' else 'v3',
        'runs': table_after(raw, 'Run ids by arm:'),
        'scoreRows': table_after(raw, '### Checks by case and arm'),
        'scores': table_after(raw, 'Checks passed per case set:'),
        'rules': table_after(raw, '### Acceptance rules'),
        'cost': table_after(raw, '### Cost per arm'),
        'baselineCost': table_after(raw, '### Cost per baseline run'),
        'agentCost': table_after(raw, 'Improvement-agent calls (outside the arms):'),
        'lesson': {
            'id': re.search(r'### Root cause `([^`]+)`', analyze).group(1),
            'text': analyze.split('\nLesson: ', 1)[1].strip(),
            'why': re.findall(r'^\d\. (.*)', why, re.M),
            'origin': re.search(r'Origin event `([^`]+)`', analyze).group(1),
            'refs': re.findall(r'`([^`]+)`', source_events),
        },
        'proposal': {
            'changes': table_after(proposal, 'lesson retrieval: vector.'),
            'rationale': re.search(r'- Rationale: (.*)', proposal).group(1),
            'expected': re.search(r'- Expected effect: (.*)', proposal).group(1),
            'risks': re.search(r'- Risks: (.*)', proposal).group(1),
        },
    }

data = {
    'commit': SOURCE_COMMIT,
    'repo': 'https://github.com/TallGibbs/race-engineer-adaptive-harness',
    'date': 'September 26, 2026',
    'sources': sources,
    'experiments': experiments,
    'config': json.loads(sources['configs/v1.json']),
    'cases': json.loads(sources['data/cases.json']),
}

for e in experiments.values():
    assert len(e['runs']) == 18
    assert len(e['scoreRows']) == 36
    assert len(e['rules']) == 5
    assert len(e['cost']) == 3
    assert all(int(row['candidate']) == 11 for row in e['scores'] if row['case set'] == 'development')

page = (ROOT / 'page.html').read_text(encoding='utf-8')
page = page.replace('/* SITE_CSS */', (ROOT / 'style.css').read_text(encoding='utf-8'))
page = page.replace('/* SITE_JS */', (ROOT / 'app.js').read_text(encoding='utf-8'))
page = page.replace('/* EVIDENCE_DATA */', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(page, encoding='utf-8', newline='\n')
print('Evidence verified: 36 runs, 72 applicable score rows, 10 acceptance rulings.')

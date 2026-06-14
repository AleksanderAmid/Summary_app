"""Regression tests for the de-anonymization stage.

Run from the repo root:  .venv\\Scripts\\python.exe app\\tests\\test_deanonymization.py

Covers the failure modes found during review: chained fake->real
replacement, garbage partial-name pairs from OCR-glued code-book entries,
cross-patient detection, and restoration of the full patient-1 identifiers.
Requires the study data in data/deidentified/.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import deanonymization as d

src = json.loads(Path('data/deidentified/1/journal.json').read_text(encoding='utf-8'))
source_text = src.get('full_text') or '\n'.join(p.get('text', '') for p in src['pages'])

failures = []

# 1. Chained replacement: fake 'Stockholm' -> real 'Mölndal' must NOT continue
#    through fake 'Mölndal' -> real 'Hönö'.
r = d.deanonymize('Patienten remitterades till Stockholm.', source_text)
print('chain test 1:', r['text'])
if 'Mölndal' not in r['text'] or 'Hönö' in r['text']:
    failures.append('chained replacement Stockholm->Mölndal failed')

r = d.deanonymize('Patienten vårdades på Karlskoga.', source_text)
print('chain test 2:', r['text'])
if 'Mölndal Sjukhus' not in r['text']:
    failures.append('chained replacement Karlskoga->Mölndal Sjukhus failed')

# 2. Garbage partial pairs must be gone: 'dr Norberg' must not become
#    'dr Vårdcentral'; 'Lotta kontaktades' must not become 'Dr kontaktades'.
r = d.deanonymize('Remiss skickad av dr Norberg.', source_text)
print('garbage test 1:', r['text'])
if 'Vårdcentral' in r['text']:
    failures.append('garbage partial Norberg->Vårdcentral still fires')

r = d.deanonymize('Lotta kontaktades.', source_text)
print('garbage test 2:', r['text'])
if r['text'].startswith('Dr '):
    failures.append('garbage partial Lotta->Dr still fires')

# 3. Legitimate restorations still work.
r = d.deanonymize(
    'Josefin Christina Lovisa Andersson (19380708-5786) besökte Havets Vårdcentral.',
    source_text)
print('restore test:', r['text'])
if 'Dagmar' not in r['text'] or '19381013-1684' not in r['text'] \
        or 'Hönö Vårdcentral' not in r['text']:
    failures.append('legitimate restoration broken')

# 4. Detection regression across the whole corpus.
maps = d.load_all_mappings()
ok, nomatch, wrong = 0, 0, 0
for pdir in sorted(Path('data/deidentified').iterdir()):
    if not pdir.is_dir():
        continue
    for f in sorted(pdir.glob('*.json')):
        if f.name.startswith('_'):
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        text = data.get('full_text') or '\n'.join(
            p.get('text', '') for p in data.get('pages', []))
        if not text.strip():
            continue
        pid, _ = d.detect_patient(text, maps)
        if pid == pdir.name:
            ok += 1
        elif pid is None:
            nomatch += 1
        else:
            wrong += 1
print(f'detection: correct={ok} no-match={nomatch} wrong={wrong}')
if wrong:
    failures.append(f'{wrong} cross-patient detections')

print()
print('FAILURES:', failures if failures else 'none — all tests pass')

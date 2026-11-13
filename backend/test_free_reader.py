"""Free code reader + /extract wiring. Offline: no API key, the cloud reader is stubbed.

    cd backend && python3.12 test_free_reader.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ['CAPTURE_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='dq_free_'), 'c.db')
os.environ.pop('DATABASE_URL', None)
os.environ.setdefault('ANTHROPIC_API_KEY', 'placeholder-not-used-by-tested-endpoints')
sys.path.insert(0, str(HERE))
import free_reader                                                # noqa: E402

FIX = HERE / 'tests' / 'fixtures'
DEMO = FIX / 'TMC_1_UnionCityBlvd_at_RegentsBlvd.pdf'

CSV_HEAD = 'Time,NL,NT,NR,SL,ST,SR,EL,ET,ER,WL,WT,WR,TOTAL\n'
CSV_ROWS = ['07:00,1,10,1,1,10,1,1,10,1,1,10,1,48', '07:15,2,20,2,2,20,2,2,20,2,2,20,2,96',
            '07:30,2,20,2,2,20,2,2,20,2,2,20,2,96', '07:45,2,20,2,2,20,2,2,20,2,2,20,2,96',
            '08:00,2,20,2,2,20,2,2,20,2,2,20,2,96',
            '16:00,3,30,3,3,30,3,3,30,3,3,30,3,144', '16:15,3,30,3,3,30,3,3,30,3,3,30,3,144',
            '16:30,3,30,3,3,30,3,3,30,3,3,30,3,144', '16:45,3,30,3,3,30,3,3,30,3,3,30,3,144']


def _tmp(name, data):
    p = Path(tempfile.mkdtemp(prefix='dq_free_')) / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


def test_demo_pdf_read_exactly():
    r, why = free_reader.read(DEMO)
    assert r, why
    i = r['intersections'][0]
    assert i['name'] == 'Union City Blvd & Regents Blvd'
    assert (i['am_peak_start'], i['pm_peak_start']) == ('07:15', '16:45')
    # values printed on the sheet's Peak Hr rows
    assert i['volumes']['AM'] == {'EBU': 0, 'EBL': 46, 'EBT': 29, 'EBR': 49, 'WBU': 0, 'WBL': 87, 'WBT': 51,
                                  'WBR': 90, 'NBU': 0, 'NBL': 97, 'NBT': 1302, 'NBR': 72, 'SBU': 0,
                                  'SBL': 80, 'SBT': 1089, 'SBR': 67}
    assert i['volumes']['PM']['SBT'] == 1571 and sum(i['volumes']['PM'].values()) == 3534
    assert i['phf'] == {'AM': round(3059 / (4 * 829), 3), 'PM': round(3534 / (4 * 963), 3)}
    for f in FIX.glob('TMC_*.pdf'):
        assert free_reader.read(f)[0], f


def test_scan_goes_to_cloud():
    import pypdfium2
    img = pypdfium2.PdfDocument(str(DEMO))[0].render(scale=2).to_pil().convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='PDF')
    r, why = free_reader.read(_tmp('scan.pdf', buf.getvalue()))
    assert r is None and 'no text layer' in why


def test_csv_good_and_bad():
    r, why = free_reader.read(_tmp('c.csv', CSV_HEAD + '\n'.join(CSV_ROWS)))
    assert r, why
    i = r['intersections'][0]
    assert i['am_peak_start'] == '07:15' and i['volumes']['AM']['NBT'] == 80
    assert i['volumes']['AM']['NBU'] is None                      # no U-turn column -> null
    assert i['phf']['AM'] == 1.0
    bad = CSV_ROWS.copy()
    bad[2] = bad[2].replace(',96', ',97')                         # a misread row total
    r, why = free_reader.read(_tmp('c.csv', CSV_HEAD + '\n'.join(bad)))
    assert r is None and why.startswith('check failed (AM): 15-min rows'), why
    r, why = free_reader.read(_tmp('c.csv', CSV_HEAD + '\n'.join(CSV_ROWS[:5])))
    assert r is None and 'no PM counts' in why, why


def test_synthetic_samples_exact_or_handed_off():
    # backend/tests/fixtures/samples (synthetic look-alikes of vendor formats, answers in expected.json):
    # every free read must match exactly; anything else must go to the cloud, never be wrong.
    import json
    samples = FIX / 'samples'
    exp = json.loads((samples / 'expected.json').read_text())['files']
    free = []
    for name, want in exp.items():
        r, why = free_reader.read(samples / name)
        if r is None:
            continue
        got, w = r['intersections'][0], want['intersections'][0]
        for k in ('am_peak_start', 'pm_peak_start', 'volumes', 'phf', 'hv_pct'):
            assert got.get(k) == w.get(k), (name, k, got.get(k), w.get(k))
        free.append(name)
    assert {'tabular15_1.xlsx', 'tabular15_2.xlsx'} <= set(free), free   # NDS-style workbooks read for free


def test_extract_route_free_then_cloud():
    from fastapi.testclient import TestClient
    import app as app_module
    client = TestClient(app_module.app)
    demo = ('files', (DEMO.name, DEMO.read_bytes(), 'application/pdf'))
    scan = ('files', ('scan.csv', b'nothing,to,read\n', 'text/csv'))
    calls = []

    def fake_extract(path, key):
        calls.append(path)
        return {'vendor_detected': 'Unknown', 'intersections': [{'name': 'x'}]}

    real_extract, real_key = app_module.extract, app_module.API_KEY
    app_module.extract = fake_extract
    try:
        r = client.post('/extract', files=[demo]).json()
        assert r['results'][0]['reader'] == 'free' and not calls
        assert r['results'][0]['source_file'] == DEMO.name
        r = client.post('/extract', files=[demo, scan]).json()
        assert [x['reader'] for x in r['results']] == ['free', 'cloud'] and len(calls) == 1
        assert r['results'][1]['reader_reason'].startswith('free reader: unknown layout')
        assert client.get('/health').json()['cloud_reader'] == 'on'
        app_module.API_KEY = None                                 # no key: free still works
        assert client.get('/health').json()['cloud_reader'].startswith('off')
        r = client.post('/extract', files=[demo, scan])
        assert r.status_code == 200 and r.json()['results'][0]['reader'] == 'free'
        assert 'ANTHROPIC_API_KEY is not set' in r.json()['errors'][0]['error'] and len(calls) == 1
    finally:
        app_module.extract, app_module.API_KEY = real_extract, real_key


if __name__ == '__main__':
    tests = [v for k, v in dict(globals()).items() if k.startswith('test_')]
    for t in tests:
        t()
    print(f'test_free_reader: {len(tests)} passed')

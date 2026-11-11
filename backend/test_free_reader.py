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
# Owner's real NDS workbook (gitignored Test/ folder); its confirmed reading is in training_data.jsonl.
XLSX = Path(os.environ.get('DQ_TEST_XLSX', HERE.parent / 'Test' / '24-080045-001 Oakwood Dr & El Camino Real.xlsx'))

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


def test_real_nds_excel_matches_confirmed_reading():
    if not XLSX.exists():
        print('  (skipped: owner workbook not on this machine)')
        return
    r, why = free_reader.read(XLSX)
    assert r, why
    i = r['intersections'][0]
    # confirmed answer: backend/training_data.jsonl, Oakwood Dr & El Camino Real .xlsx
    assert (i['am_peak_start'], i['pm_peak_start']) == ('07:45', '15:45')
    assert i['volumes']['AM'] == {'EBU': 36, 'EBL': 37, 'EBT': 1898, 'EBR': 20, 'WBU': 37, 'WBL': 52,
                                  'WBT': 943, 'WBR': 23, 'NBU': 0, 'NBL': 71, 'NBT': 24, 'NBR': 88,
                                  'SBU': 0, 'SBL': 161, 'SBT': 15, 'SBR': 39}
    assert i['volumes']['PM'] == {'EBU': 59, 'EBL': 74, 'EBT': 1266, 'EBR': 25, 'WBU': 49, 'WBL': 61,
                                  'WBT': 1675, 'WBR': 36, 'NBU': 0, 'NBL': 40, 'NBT': 19, 'NBR': 54,
                                  'SBU': 0, 'SBL': 60, 'SBT': 7, 'SBR': 32}
    assert i['phf'] == {'AM': 0.956, 'PM': 0.97} and i['hv_pct'] == {'AM': 2.6, 'PM': 1.9}


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

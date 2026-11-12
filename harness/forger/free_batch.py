"""Score the free code reader on Forger sheets. No API, no cost:
    python -m harness.forger.free_batch --n 50

Styles T and D, plus T-scanned and D-scanned: the same sheet rasterized to an
image-only PDF (no text layer), which the free reader must hand to the cloud.
Per style it counts sheets read exactly right (16 movements x 2 periods, both
peak-hour starts, PHF), sheets WRONG but passed by the checks (must be 0; of
those, how many were partly right) and sheets handed to the cloud reader.
"""

import argparse
import io
import sys
import tempfile
from pathlib import Path

from . import operate  # noqa: F401 — puts backend/ on sys.path
from . import sheets

import free_reader                                                # noqa: E402
import read_checks                                                # noqa: E402

U_TURNS = ('EBU', 'WBU', 'NBU', 'SBU')


def expected(sheet):
    """What an exact reading returns, from the answer key."""
    vols, phf = {}, {}
    for period in ('AM', 'PM'):
        u = 0 if sheet['style'] == 'T' else None    # T prints zero U-turn columns; D has none
        vols[period] = dict(sheet['periods'][period], **{mv: u for mv in U_TURNS})
        if sheet['style'] == 'T':
            rows = [sum(b[i] for b in sheet['bins'][period].values()) for i in range(4)]
            phf[period] = round(sheet['grand_total'][period] / (4 * max(rows)), 3)
        else:
            phf[period] = None                     # D prints no 15-min data
    return {'am_peak_start': '07:15', 'pm_peak_start': '16:45', 'volumes': vols, 'phf': phf}


def score(sheet, result):
    """'right', 'partly' (some fields right), 'wrong' or 'cloud'."""
    if result is None:
        return 'cloud'
    want, got = expected(sheet), result['intersections'][0]
    fields = [(want[k], got.get(k)) for k in ('am_peak_start', 'pm_peak_start')]
    for period in ('AM', 'PM'):
        fields += [(v, got['volumes'][period].get(mv)) for mv, v in want['volumes'][period].items()]
        fields.append((want['phf'][period], got['phf'][period]))
    n_right = sum(a == b for a, b in fields)
    return 'right' if n_right == len(fields) else 'partly' if n_right else 'wrong'


def scan(pdf_bytes, dpi=150):
    """Image-only PDF of the same page(s): what a scanner gives."""
    import pypdfium2
    pages = [p.render(scale=dpi / 72).to_pil().convert('RGB') for p in pypdfium2.PdfDocument(pdf_bytes)]
    buf = io.BytesIO()
    pages[0].save(buf, format='PDF', save_all=True, append_images=pages[1:], resolution=dpi)
    return buf.getvalue()


def run(n, start_seed=1):
    """{style: {'right', 'partly', 'wrong', 'cloud': count, 'reasons': {reason: count}}}"""
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sheet.pdf'
        for style in ('T', 'D'):
            for variant in (style, f'{style}-scanned'):
                tally = out[variant] = {'right': 0, 'partly': 0, 'wrong': 0, 'cloud': 0, 'reasons': {}}
                for seed in range(start_seed, start_seed + n):
                    sheet = sheets.generate_sheet(seed, style)
                    pdf = sheets.render_pdf(sheet)
                    path.write_bytes(scan(pdf) if variant.endswith('scanned') else pdf)
                    result, reason = free_reader.read(path)
                    tally[score(sheet, result)] += 1
                    if result is None:
                        tally['reasons'][reason] = tally['reasons'].get(reason, 0) + 1
    return out


def _swap(a, b):
    def f(v):
        v[a], v[b] = v[b], v[a]
        return v
    return f


# One wrong number on the peak-hour row, as a misreading reader would produce it.
MISREADS = {
    'one digit wrong (EBT +10)': lambda v: dict(v, EBT=v['EBT'] + 10),
    'left/right swapped (EBL<->EBR)': _swap('EBL', 'EBR'),
    'wrong approach (NBT<->SBT)': _swap('NBT', 'SBT'),
}


def misreads_caught(n, start_seed=1):
    """{(style, misread): sheets of n the checks sent to the cloud}. The AM peak-hour
    values are altered after reading, before the checks run."""
    out, real_run = {}, read_checks.run
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sheet.pdf'
        for style in ('T', 'D'):
            for name, bad in MISREADS.items():
                def run(p, period, bad=bad):
                    if period == 'AM':
                        p = dict(p, volumes=bad(dict(p['volumes'])),
                                 stated_hour=p['stated_hour'] and bad(dict(p['stated_hour'])))
                    return real_run(p, period)
                read_checks.run, caught = run, 0
                try:
                    for seed in range(start_seed, start_seed + n):
                        path.write_bytes(sheets.render_pdf(sheets.generate_sheet(seed, style)))
                        caught += free_reader.read(path)[0] is None
                finally:
                    read_checks.run = real_run
                out[(style, name)] = caught
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description='Score the free reader on Forger sheets (no API).')
    p.add_argument('--n', type=int, default=50)
    p.add_argument('--start-seed', type=int, default=1)
    args = p.parse_args(argv)
    for variant, t in run(args.n, args.start_seed).items():
        print(f'{variant:10s} {args.n} sheets: {t["right"]} read exactly right, '
              f'{t["partly"] + t["wrong"]} wrong but passed the checks ({t["partly"]} partly right), '
              f'{t["cloud"]} handed to the cloud {t["reasons"] or ""}')
    print('Misread on purpose (checks must catch these):')
    for (style, name), caught in misreads_caught(args.n, args.start_seed).items():
        print(f'  {style}: {name}: {caught} of {args.n} caught')
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Regression guard for an upstream parser hang on a synthetic malformed PDF."""
import subprocess
import sys
from pathlib import Path


def test_pdf_sans_trailer_ne_bloque_pas_extraction(tmp_path: Path):
    # Minimal public upstream reproducer from py-pdf/pypdf PR 4289, no client data.
    pdf = tmp_path / 'invalide.pdf'
    pdf.write_bytes(b'%PDF-1.4\nstartxref\n28\n%%EOF\nxref\n1 0\naaa')
    script = (
        'from pathlib import Path; '
        'from seamtech_search.extractors import extract_file; '
        'import sys; '
        'r=extract_file(Path(sys.argv[1])); '
        'assert r.status in ("error", "unavailable")'
    )
    resultat = subprocess.run([sys.executable, '-c', script, str(pdf)], capture_output=True, timeout=10, check=False)
    assert resultat.returncode == 0

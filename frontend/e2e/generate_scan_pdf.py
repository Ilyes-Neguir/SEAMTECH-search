"""Generate image-only PDF for e2e scan-browser-ocr spec."""
import sys
from pathlib import Path

# Ensure repo root is in path for tests.helpers_ocr_scan
repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo_root))

from tests.helpers_ocr_scan import gen_pdf_fr, gen_pdf_en, gen_pdf_multi, gen_pdf_degrade

def main():
    if len(sys.argv) < 3:
        print("usage: generate_scan_pdf.py <tmp_dir> <variant>", file=sys.stderr)
        sys.exit(1)
    tmp_dir = Path(sys.argv[1])
    variant = sys.argv[2]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if variant == "fr":
        pdf = gen_pdf_fr(tmp_dir)
    elif variant == "en":
        pdf = gen_pdf_en(tmp_dir)
    elif variant == "multi":
        pdf = gen_pdf_multi(tmp_dir)
    elif variant == "degrade":
        pdf = gen_pdf_degrade(tmp_dir)
    else:
        print(f"unknown variant {variant}", file=sys.stderr)
        sys.exit(1)
    print(str(pdf))

if __name__ == "__main__":
    main()

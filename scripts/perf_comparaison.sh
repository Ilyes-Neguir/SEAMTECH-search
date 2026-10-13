#!/usr/bin/env bash
# Comparaison de latence à conditions identiques : départ 8dd5ae7, SHA
# applicatif a3506c8 (échec CI perf), HEAD courant.
#
# Ne desserre PAS le critère produit (p50 < 100 ms, p95 produit 100 ms).
# Le seuil d'environnement CI 250 ms reste étiqueté. Aucun test n'est
# désélectionné. Usage : scripts/perf_comparaison.sh
set -euo pipefail
trap 'echo "::error title=perf-comparaison/abort::échec ligne ${LINENO}"' ERR

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RACINE"

: "${SEAMTECH_TEST_DATABASE_URL:?SEAMTECH_TEST_DATABASE_URL requis}"

BASELINE_SHA="${BASELINE_SHA:-8dd5ae7340a0eb1023bd73ef5d884c4538efbc09}"
INTER_SHA="${INTER_SHA:-a3506c84892e97a8993fe6a9657cc93019f002d3}"
HEAD_SHA="$(git rev-parse HEAD)"
N_RUNS="${N_RUNS:-3}"
SEUIL_CI="${SEAMTECH_PERF_P95_CI_MS:-250}"
DOSSIER="${SEAMTECH_PERF_COMPARAISON_DIR:-/tmp/seamtech-perf-comparaison}"
export N_RUNS SEAMTECH_PERF_COMPARAISON_DIR="$DOSSIER" SEAMTECH_PERF_P95_CI_MS="$SEUIL_CI"
mkdir -p "$DOSSIER"

echo "=== perf comparaison $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "baseline=$BASELINE_SHA"
echo "a3506c8=$INTER_SHA"
echo "HEAD=$HEAD_SHA"
echo "n_runs=$N_RUNS seuil_ci=${SEUIL_CI} ms critère produit p50<100 / p95<100"

if ! git cat-file -e "${BASELINE_SHA}^{commit}"; then
    echo "::error title=perf-comparaison/sha::baseline $BASELINE_SHA absent du clone (fetch-depth 0 requis)"
    exit 1
fi
if ! git cat-file -e "${INTER_SHA}^{commit}"; then
    echo "::error title=perf-comparaison/sha::a3506c8 $INTER_SHA absent du clone"
    exit 1
fi

_extraire_sha() {
    local sha="$1" dest="$2"
    rm -rf "$dest"
    mkdir -p "$dest"
    if ! git archive "$sha" | tar -C "$dest" -x; then
        echo "::error title=perf-comparaison/archive::git archive $sha → $dest a échoué"
        exit 1
    fi
    echo "extrait $sha → $dest ($(git rev-parse --short "$sha"))"
}

WT_BASE="$DOSSIER/wt-baseline"
WT_INTER="$DOSSIER/wt-a3506c8"
_extraire_sha "$BASELINE_SHA" "$WT_BASE"
_extraire_sha "$INTER_SHA" "$WT_INTER"

# Un seul environnement de dépendances : on bascule le paquet éditable.
python -m pip install -q -e "$RACINE"

_executer_suite() {
    local etiquette="$1" racine_code="$2" run="$3"
    local json="$DOSSIER/${etiquette}-run${run}.jsonl"
    rm -f "$json"
    echo "-- $etiquette run $run (cwd=$racine_code)"
    python -m pip install -q -e "$racine_code" --no-deps
    (
        cd "$racine_code"
        set +e
        SEAMTECH_TEST_DATABASE_URL="$SEAMTECH_TEST_DATABASE_URL" \
        SEAMTECH_PERF_JSON="$json" \
        SEAMTECH_PERF_P95_CI_MS="$SEUIL_CI" \
            python -m pytest -q -m perf -rf --tb=line 2>&1 | tee "$DOSSIER/${etiquette}-run${run}.log"
        echo "${PIPESTATUS[0]}" > "$DOSSIER/${etiquette}-run${run}.rc"
        set -e
    )
    if [ ! -s "$json" ]; then
        echo "ATTENTION : $json vide (pytest n'a pas publié SEAMTECH_PERF_JSON)"
    fi
}

for run in $(seq 1 "$N_RUNS"); do
    _executer_suite "baseline" "$WT_BASE" "$run"
    _executer_suite "a3506c8" "$WT_INTER" "$run"
    _executer_suite "head" "$RACINE" "$run"
done

python -m pip install -q -e "$RACINE" --no-deps

python - <<'PY'
import json
import os
import statistics
import sys
from pathlib import Path

dossier = Path(os.environ.get("SEAMTECH_PERF_COMPARAISON_DIR", "/tmp/seamtech-perf-comparaison"))
n_runs = int(os.environ.get("N_RUNS", "3"))
seuil_ci = float(os.environ.get("SEAMTECH_PERF_P95_CI_MS", "250"))
attendus = {
    "50 requêtes synthétique",
    "1 500 fiches (mi-échelle)",
    "fonds réel 7792-SO (13 requêtes)",
    "recherche par dimension (Phase 1)",
}

def charger(etiquette: str) -> list[dict]:
    runs = []
    for i in range(1, n_runs + 1):
        chemin = dossier / f"{etiquette}-run{i}.jsonl"
        mesures = []
        if chemin.is_file():
            for ligne in chemin.read_text(encoding="utf-8").splitlines():
                if ligne.strip():
                    mesures.append(json.loads(ligne))
        rc_path = dossier / f"{etiquette}-run{i}.rc"
        rc = int(rc_path.read_text(encoding="utf-8").strip()) if rc_path.is_file() else 99
        runs.append({"run": i, "rc": rc, "mesures": {m["nom"]: m for m in mesures}})
    return runs

jeux = {k: charger(k) for k in ("baseline", "a3506c8", "head")}

def mediane(etiquette: str, nom: str, champ: str) -> float | None:
    vals = []
    for run in jeux[etiquette]:
        m = run["mesures"].get(nom)
        if m and champ in m:
            vals.append(float(m[champ]))
    return statistics.median(vals) if vals else None

def brut(etiquette: str, nom: str, champ: str) -> list[float]:
    vals = []
    for run in jeux[etiquette]:
        m = run["mesures"].get(nom)
        if m and champ in m:
            vals.append(float(m[champ]))
    return vals

erreurs: list[str] = []
notices: list[str] = []

for etiquette, runs in jeux.items():
    for run in runs:
        publies = set(run["mesures"])
        manquants = attendus - publies
        if manquants:
            erreurs.append(
                f"{etiquette} run {run['run']} : mesures absentes {sorted(manquants)} "
                f"(rc={run['rc']})"
            )

resume = {"n_runs": n_runs, "seuil_ci_ms": seuil_ci, "jeux": {}}
for etiquette in ("baseline", "a3506c8", "head"):
    resume["jeux"][etiquette] = {}
    for nom in sorted(attendus):
        p50s = brut(etiquette, nom, "p50_ms")
        p95s = brut(etiquette, nom, "p95_ms")
        maxs = brut(etiquette, nom, "max_ms")
        resume["jeux"][etiquette][nom] = {
            "p50_ms": p50s,
            "p95_ms": p95s,
            "max_ms": maxs,
            "p50_median": statistics.median(p50s) if p50s else None,
            "p95_median": statistics.median(p95s) if p95s else None,
        }
        msg = (
            f"{etiquette} | {nom} : p50={p50s} p95={p95s} max={maxs} "
            f"(médiane p50={resume['jeux'][etiquette][nom]['p50_median']} "
            f"p95={resume['jeux'][etiquette][nom]['p95_median']})"
        )
        notices.append(msg)
        print(msg)

(dossier / "resume.json").write_text(
    json.dumps(resume, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# Preuve de régression a3506c8 vs baseline (même runner, même protocole).
for nom in sorted(attendus):
    b = mediane("baseline", nom, "p50_ms")
    a = mediane("a3506c8", nom, "p50_ms")
    h = mediane("head", nom, "p50_ms")
    if b is None or a is None:
        erreurs.append(f"comparaison p50 impossible pour {nom} (baseline={b} a3506c8={a})")
        continue
    ratio = a / b if b > 0 else float("inf")
    print(f"ratio a3506c8/baseline p50 [{nom}] = {ratio:.2f} ({a:.1f}/{b:.1f})")
    if b < 30 and a >= 100:
        erreurs.append(
            f"RÉGRESSION mesurée (pas un bruit p95 isolé) : {nom} p50 "
            f"baseline {b:.1f} ms → a3506c8 {a:.1f} ms (×{ratio:.1f}) "
            f"sur le même runner"
        )
    if h is not None:
        print(f"HEAD p50 [{nom}] = {h:.1f} ms")
        if h >= 100.0:
            erreurs.append(
                f"critère d'architecture violé sur HEAD : {nom} p50 = {h:.1f} ms ≥ 100 ms"
            )
        p95_h = mediane("head", nom, "p95_ms")
        if p95_h is not None and p95_h >= seuil_ci:
            erreurs.append(
                f"seuil CI {seuil_ci:.0f} ms violé sur HEAD : {nom} p95 = {p95_h:.1f} ms "
                f"(critère produit 100 ms non desserré)"
            )
        if p95_h is not None and p95_h >= 100.0:
            print(
                f"RESERVE produit : {nom} p95 HEAD = {p95_h:.1f} ms ≥ 100 ms "
                f"— le critère atelier n'est pas démontré"
            )

gha = os.environ.get("GITHUB_ACTIONS")
for n in notices:
    if gha:
        print(f"::notice title=perf-comparaison::{n}")
    else:
        print(n)

if erreurs:
    for e in erreurs:
        print(f"::error title=perf-comparaison::{e}" if gha else f"ERREUR {e}")
    sys.exit(1)
print("comparaison terminée : HEAD p50 < 100 ms sur les mesures attendues")
PY

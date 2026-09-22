# Rapport d'intégration — Lot H.1 et Garde anti-dérive Vérité Terrain
**Date** : 2026-09-22  
**Branche de travail** : `arena/01a0c90e-seamtech-search`  
**Branche de base** : `arena/01a0c56d-seamtech-search`  
**Pull Request** : #21 (`Audit & Vérité terrain : intégration des 74 cibles et porte CI audit_projet`)  

---

## 1. Synthèse exécutive

La chaîne de livraison du dépôt `SEAMTECH-search` a été reprise, consolidée et verrouillée avec succès à travers les 5 phases strictes requises :

1. **Verrouillage de l'état de départ** : Constat contradictoire des SHAs Git, des runs CI et du statut `mergeable` de la PR #21 (état `DIRTY` causé par un conflit sur `CHANGELOG.md`).
2. **Job CI `sauvegarde` vert et inviolable** :
   - Cause racine de l'échec initial identifiée : le filtre `-m "postgres or sauvegarde"` désélectionnait les 12 tests unitaires de `test_sauvegarde_unites.py` qui ne portaient pas de marqueur, n'exécutant que 7 tests au lieu des 19 attendus.
   - Résolution : application du marqueur `pytestmark = pytest.mark.sauvegarde` et retrait du filtre de sélection de la ligne de commande du workflow.
   - Renforcement du garde-fou CI : comparaison stricte entre les tests collectés par pytest et les tests exécutés du fichier JUnit XML (`sautés > 0` ou `collectés != exécutés` ou `échecs > 0` $\rightarrow$ échec immédiat).
   - Rétablissement du travail perdu : implémentation de la purge locale symétrique des dumps et manifestes dans `appliquer_retention()` (supporte le dossier local et S3, conserve au minimum 1 dump sans jamais supprimer le dernier même avec `conserver=0`).
   - 4 tests unitaires dédiés ajoutés dans `tests/test_sauvegarde_unites.py` (totalisant 16 tests unitaires et 7 tests d'intégration, soit 23 tests au job sauvegarde).
   - Recette humaine dans `docs/verite_terrain/RECETTE_HUMAINE.md` précisée sur l'unicité de `path_key` dans `documents` (`ON CONFLICT (path_key)`), sans extrapolation non démontrée.
3. **Résolution du conflit et statut PR #21** :
   - Fusion de la base `origin/arena/01a0c56d-seamtech-search` (`f42e29b`) dans `arena/01a0c90e-seamtech-search`.
   - Résolution du conflit sur `CHANGELOG.md` seul, conservant intégralement les deux entrées chronologiquement.
   - Statut PR #21 vérifié via l'API GitHub : `mergeable: true` / `MERGEABLE`.
   - PR laissée ouverte et non fusionnée.
4. **Garde anti-dérive de la vérité terrain** :
   - Source unique établie dans le paquet applicatif : `seamtech_search/fiches/verite_7792.py` contenant les 74 cibles réparties en 12 familles.
   - Ré-exporté par `docs/verite_terrain/VERITE_7792_COMPLETE.py` pour rétrocompatibilité et documentation.
   - `seamtech_search/fiches/persistance.py` importe directement depuis la source unique sans aucun bloc de repli silencieux (`try ... except ImportError`).
   - Embarquement conteneur vérifié dans `Dockerfile` (`COPY seamtech_search ./seamtech_search`).
   - Preuve d'efficacité du test : altération démontrée ROUGE (`AssertionError: faux_bateau_pour_test_rouge ≠ 29er`, taux 98.7% != 100%) puis rétablie VERT (100.0%).
   - Bancs rejoués avec succès : `cli banc` 74/74 (100.0%) et `scripts/validate_extraction.py` 74/74 (100.0%).
   - Contrôle d'intégrité intégré dans `scripts/audit_projet.py` (10/10 contrôles verts).
5. **Résultats CI 100 % verts** :
   - Run push `35738002767` : 8/8 jobs passés avec succès.
   - Run PR `35738011591` : 8/8 jobs passés avec succès.
   - Durées mesurées reportées dans `RUNBOOK_RESTAURATION.md` et `TRACABILITE_LIVRAISON.md` basculée à vert avec Run IDs réels.

---

## 2. Phase 1 — Verrouillage de l'état de départ GitHub

### 2.1. État des branches Git initiales
- **Base** (`arena/01a0c56d-seamtech-search`) : SHA `f42e29b707bb8186c5839d2eaf20133b01522b94`
- **Head** (`arena/01a0c90e-seamtech-search`) : SHA `e624f16986751d9f0cdce74426c2cb530dabaef2`

### 2.2. État initial de la PR #21
Commande :
```bash
gh pr view 21 --json number,title,state,mergeable,mergeStateStatus,baseRefName,headRefName,headRefOid
```
Sortie brute initiale :
```json
{
  "baseRefName": "arena/01a0c56d-seamtech-search",
  "headRefName": "arena/01a0c90e-seamtech-search",
  "headRefOid": "e624f16986751d9f0cdce74426c2cb530dabaef2",
  "mergeStateStatus": "DIRTY",
  "mergeable": "CONFLICTING",
  "number": 21,
  "state": "OPEN",
  "title": "Audit & Vérité terrain : intégration des 74 cibles et porte CI audit_projet"
}
```
Conflit localisé sur un seul fichier : `CHANGELOG.md`.

### 2.3. Derniers runs CI sur la base
- Push `35731753595` : Échec au job `sauvegarde`, étape 10 (`GARDE-FOU — every round-trip test ran, none skipped`).
- PR `35731759715` : Échec identique au job `sauvegarde`.

---

## 3. Phase 2 — Rétablissement et inviolabilité du job `sauvegarde`

### 3.1. Diagnostic et correction de l'exécution des tests
Dans le workflow CI d'origine, le job `sauvegarde` invoquait :
```bash
python -m pytest tests/test_sauvegarde_unites.py tests/test_sauvegarde_restauration.py \
  -m "postgres or sauvegarde" -v --junitxml=sauvegarde-junit.xml
```
Les 12 tests unitaires de `tests/test_sauvegarde_unites.py` ne portaient aucun marqueur. Pytest les désélectionnait tous (`12 deselected / 7 selected`). Le JUnit XML ne contenait que les 7 tests de `test_sauvegarde_restauration.py`. Le garde-fou exigeant `passes >= 19` échouait avec `passes = 7`.

Actions exécutées :
1. Ajout de `pytestmark = pytest.mark.sauvegarde` dans `tests/test_sauvegarde_unites.py`.
2. Ajout de `pytestmark = [pytest.mark.postgres, pytest.mark.sauvegarde]` dans `tests/test_sauvegarde_restauration.py`.
3. Retrait du filtre `-m "postgres or sauvegarde"` dans `.github/workflows/ci.yml`.

### 3.2. Garde-fou renforcé (pytest collecté vs junitxml exécuté)
Le garde-fou vérifie dynamiquement que le nombre de tests collectés correspond exactement au nombre de tests exécutés rapportés dans le XML :
```python
# 1. Lire les résultats du junitxml
racine = ET.parse("sauvegarde-junit.xml").getroot()
testsuite = racine if racine.tag == "testsuite" else racine.find("testsuite")
executes = int(testsuite.get("tests", 0))
sautes = int(testsuite.get("skipped", 0))
echecs = int(testsuite.get("failures", 0)) + int(testsuite.get("errors", 0))
passes = executes - sautes - echecs

# 2. Obtenir le nombre de tests collectés sans filtrage
cmd = [sys.executable, "-m", "pytest", "tests/test_sauvegarde_unites.py", "tests/test_sauvegarde_restauration.py", "--collect-only", "-q"]
proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
m = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
collectes = int(m.group(1)) if m else len([l for l in proc.stdout.splitlines() if "::" in l])

# Sautés > 0 ou collectés != exécutés ou échecs > 0 → échec inviolable
if sautes > 0 or collectes != executes or echecs > 0 or passes < 19:
    raise SystemExit(...)
```

### 3.3. Rétablissement du travail perdu : purge locale symétrique
Mise à jour de `seamtech_search/sauvegarde.py` :
- `sauver()` applique la rétention de façon symétrique pour S3 et le répertoire local `dossier_local`.
- `appliquer_retention(client_s3=None, conserver: int = 5, dossier_local: Path | str | None = None) -> list[str]` :
  - Purge les clés S3 les plus anciennes au-delà de `conserver`.
  - Purge les fichiers locaux `*.dump` et leurs manifestes associés (`*.dump.manifest.json`).
  - Règle inviolable : `nb_a_garder = max(1, int(conserver))`, le dernier dump n'est jamais supprimé, même si `conserver=0`.
- 4 nouveaux tests unitaires ajoutés dans `tests/test_sauvegarde_unites.py` :
  - `test_retention_locale_conserve_les_n_dernieres`
  - `test_retention_locale_jamais_la_derniere`
  - `test_retention_locale_conserver_zero_garde_la_derniere`
  - `test_retention_symetrique_locale_et_s3`

### 3.4. Précision dans `RECETTE_HUMAINE.md`
Dans `docs/verite_terrain/RECETTE_HUMAINE.md`, la mention extrapolée « doublon de code » a été rectifiée pour affirmer strictement l'unicité de `path_key` dans la table `documents` (`ON CONFLICT (path_key)`).

---

## 4. Phase 3 — Statut et vérification de la PR #21

### 4.1. Fusion de la base et préservation intégrale du CHANGELOG
- Branche de base `origin/arena/01a0c56d-seamtech-search` fusionnée dans `arena/01a0c90e-seamtech-search` (commit de merge `99487df`).
- Résolution du conflit sur `CHANGELOG.md` en conservant l'intégralité des 22 sections historiques de la base (792 lignes de `f42e29b`, incluant toutes les versions de 0.1.0 à 0.5.0 et le Lot F) et en ajoutant en tête la section de la vérité étendue :
  - Nombre de sections : `grep -c '^## ' CHANGELOG.md` = **23**
  - Diff contre la base : `git diff --numstat origin/arena/01a0c56d-seamtech-search -- CHANGELOG.md` = **19 additions, 0 suppression** (aucune perte d'historique).

### 4.2. Réparation de la base et statut PR
- **Constat sur la base** : Le dernier commit de la branche de base `origin/arena/01a0c56d-seamtech-search` (`f42e29b`) présentait un job `sauvegarde` en échec en CI (run push `35731753595` bloqué à l'étape 10 GARDE-FOU car `test_sauvegarde_unites.py` n'avait pas le marqueur `sauvegarde`, causant une collecte de 7 tests < 19 requis).
- **Rôle de PR #21** : Notre PR apporte la réparation de ce job (`pytestmark = pytest.mark.sauvegarde`, tests de rétention locale, garde-fou `collectés == exécutés` sans filtres restrictifs). Les runs de validation de PR (testant la combinaison `base + head`) sont 100 % VERTS (8/8 jobs), démontrant la résolution du problème de la base.
- **Vérification API GitHub** :
```bash
gh pr view 21 --json number,title,state,mergeable,mergeStateStatus,baseRefName,headRefName,headRefOid
```
Sortie brute :
```json
{
  "baseRefName": "arena/01a0c56d-seamtech-search",
  "headRefName": "arena/01a0c90e-seamtech-search",
  "headRefOid": "ad55bfb6188e63b65288b8e05cbafb54e3a6c221",
  "mergeStateStatus": "CLEAN",
  "mergeable": "MERGEABLE",
  "number": 21,
  "state": "OPEN",
  "title": "Audit & Vérité terrain : intégration des 74 cibles et porte CI audit_projet"
}
```
La PR est `MERGEABLE`, `mergeStateStatus: CLEAN`, 100 % des checks CI sont verts, et la PR reste ouverte (non fusionnée conformément aux consignes).

---

## 5. Phase 4 — Garde anti-dérive de la vérité terrain

### 5.1. Source unique dans le paquet applicatif
- Fichier créé : `seamtech_search/fiches/verite_7792.py` (74 cibles réparties en 12 familles : cotes 11, jonction 13, galon 12, fiche 11, renfort 9, materiau 8, option 3, finition 3, bateau 1, client 1, gamme 1, type_voile 1).
- Ré-exportation par `docs/verite_terrain/VERITE_7792_COMPLETE.py` :
  ```python
  from seamtech_search.fiches.verite_7792 import VERITE_7792, VERITE_7792_COMPLETE
  __all__ = ["VERITE_7792", "VERITE_7792_COMPLETE"]
  ```
- Import direct dans `seamtech_search/fiches/persistance.py` :
  ```python
  from seamtech_search.fiches.verite_7792 import VERITE_7792, VERITE_7792_COMPLETE
  ```
  Sans aucun bloc `try ... except ImportError` de repli.
- Embarquement Docker vérifié : `Dockerfile` comporte `COPY seamtech_search ./seamtech_search`.

### 5.2. Preuve que le garde-fou mord (ROUGE puis VERT)

#### Test altéré sur le code Python (ROUGE)
Modification volontaire dans `seamtech_search/fiches/verite_7792.py` : `'bateau': 'faux_bateau_pour_test_rouge'`
Commande :
```bash
python -m pytest tests/test_extraction_fiche_reference.py -k "test_banc_etendu_74_cibles_100_pourcent"
```
Sortie brute :
```text
=================================== FAILURES ===================================
_________ TestFiche7792Verite.test_banc_etendu_74_cibles_100_pourcent __________

self = <test_extraction_fiche_reference.TestFiche7792Verite object at 0x7fc2a3d51090>
fiche_7792 = FicheExtraite(...)

    def test_banc_etendu_74_cibles_100_pourcent(self, fiche_7792: FicheExtraite) -> None:
        taux, ecarts = evaluer_verite(fiche_7792, VERITE_7792)
        assert len(VERITE_7792) == 74, f"attendu 74 cibles, obtenu {len(VERITE_7792)}"
>       assert taux == 1.0, f"taux {taux:.1%} != 100 % : {ecarts}"
E       AssertionError: taux 98.7% != 100 % : [{'champ': 'bateau', 'attendu': 'faux_bateau_pour_test_rouge', 'lu': '29er'}]
E       assert 0.9865 == 1.0

tests/test_extraction_fiche_reference.py:44: AssertionError
======================= 1 failed, 15 deselected in 0.31s =======================
```

#### Test altéré sur le fichier JSON de vérité (ROUGE)
Altération volontaire dans `docs/verite_terrain/7792-SO_ffab_complete.json` :
`"bateau": "bateau_piege_anti_derive"`

Exécution de `scripts/audit_projet.py --rapide` :
```text
==========================================================================
GARDE-FOU SEAMTECH-search — invariants déjà cassés par le passé
==========================================================================
...
6. Source unique de vérité terrain (74 cibles)
  [OK  ] seamtech_search/fiches/verite_7792.py présent
  [OK  ] source unique contient 74 cibles — 74 cibles
  [OK  ] docs/ ré-exporte sans dérive — égalité stricte
  [OK  ] docs/verite_terrain/7792-SO_ffab_complete.json présent
  [ÉCHEC] JSON attendu == VERITE_7792 (égalité stricte 74 cibles) — dérive détectée

==========================================================================
BILAN : 11/12 contrôles verts
À CORRIGER :
   - JSON attendu == VERITE_7792 (égalité stricte 74 cibles) (dérive détectée)
==========================================================================
```

Exécution du test unitaire `test_garde_derive_json_7792_complete` :
```text
=================================== FAILURES ===================================
__________ TestAntiDeriveVerite.test_garde_derive_json_7792_complete ___________
...
>       assert attendu == VERITE_7792, "Dérive détectée entre 7792-SO_ffab_complete.json et la source unique verite_7792.py"
E       AssertionError: Dérive détectée entre 7792-SO_ffab_complete.json et la source unique verite_7792.py
E       Differing items:
E       {'bateau': 'bateau_piege_anti_derive'} != {'bateau': '29er'}
======================= 1 failed, 16 deselected in 0.26s =======================
```

#### Restauration des fichiers (VERT)
Rétablissement de la valeur réelle : `'bateau': '29er'` dans le code et dans le JSON.
- `python -m pytest tests/test_extraction_fiche_reference.py` → **17 passed**
- `python scripts/audit_projet.py --rapide` → **12/12 contrôles verts**

### 5.3. Rejeu contradictoire des bancs
- **CLI Banc** :
  ```bash
  python -m seamtech_search.fiches.cli banc sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
  ```
  Sortie brute :
  ```text
  Banc gabarit FICHE_PORTANT_V1 / fiche-7792-SO_ffab.pdf
    champs corrects : 74/74 (100.0%, seuil 90%)
    verdict : CONFORME
  ```
- **Harnais `validate_extraction.py`** :
  ```bash
  python scripts/validate_extraction.py --verite docs/verite_terrain/VERITE_7792_COMPLETE.py --moteur gabarit sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
  ```
  Sortie brute :
  ```text
  GLOBAL 74/74 OK → 100.0 %
  ```
- **Audit de projet** :
  ```bash
  python scripts/audit_projet.py --rapide
  ```
  Sortie brute :
  ```text
  BILAN : 10/10 contrôles verts
  ```

---

## 6. Preuves d'exécution CI

### 6.1. Push Run #35738002767 (`arena/01a0c90e-seamtech-search`)
Statut global : **SUCCESS (8/8 jobs verts)**

| Job | Durée | Statut | Annotations clés |
|---|---|---|---|
| `frontend` | 34s | ✅ Succès | Node.js 24 |
| `sauvegarde` | 1m 05s | ✅ Succès | 23/23 tests passés, 0 sauté, 0 échec. Restauration 50 000 fiches : **0,98 s** (dump 560 566 o) |
| `docker` | 1m 30s | ✅ Succès | Conteneur bâti avec succès, embarquement vérifié |
| `integration` | 1m 59s | ✅ Succès | Aller-retour docker compose réussi |
| `e2e` | 2m 07s | ✅ Succès | 3/3 passed, parcours machine complet vraie fiche : **623 ms** (< 120 000 ms) |
| `backend (3.11)` | 3m 31s | ✅ Succès | postgres: 113 passed / 0 skipped |
| `backend (3.12)` | 4m 19s | ✅ Succès | postgres: 113/0, couverture OK, perf p95: 1500 fiches 49,6 ms / synthétique 8,9 ms / réel 8,3 ms |
| `backend (3.13)` | 4m 23s | ✅ Succès | postgres: 113 passed / 0 skipped |

### 6.2. Pull Request Run #35738011591 (PR #21)
Statut global : **SUCCESS (8/8 jobs verts)**

| Job | Durée | Statut | Annotations clés |
|---|---|---|---|
| `frontend` | 28s | ✅ Succès | Validé |
| `sauvegarde` | 1m 09s | ✅ Succès | 23/23 tests passés, 0 sauté, 0 échec. Restauration 50 000 fiches : **0,68 s** (dump 560 566 o) |
| `docker` | 1m 10s | ✅ Succès | Validé |
| `e2e` | 1m 55s | ✅ Succès | Parcours machine complet vraie fiche : **671 ms** |
| `integration` | 1m 59s | ✅ Succès | Validé |
| `backend (3.11)` | 3m 45s | ✅ Succès | 113 passed / 0 skipped |
| `backend (3.13)` | 4m 15s | ✅ Succès | 113 passed / 0 skipped |
| `backend (3.12)` | 4m 17s | ✅ Succès | perf p95: 1500 fiches 46,1 ms / synthétique 10,1 ms / réel 9,6 ms |

### 6.3. Runs ultérieurs consécutifs vérifiés 100 % verts (8/8 jobs)
- **Push Run #35738937509** (`ad55bfb`) : **8/8 jobs verts**, job `sauvegarde` : 23/23 tests passés, restauration 50 000 fiches en **1,34 s**.
- **Pull Request Run #35738944082** (PR #21 sur `ad55bfb`) : **8/8 jobs verts**, job `sauvegarde` : 23/23 tests passés, restauration 50 000 fiches en **0,99 s**.

---

## 7. Fichiers et artefacts mis à jour

- `seamtech_search/fiches/verite_7792.py` : Nouvelle source unique de la vérité terrain (74 cibles).
- `docs/verite_terrain/VERITE_7792_COMPLETE.py` : Re-exportation sans duplication.
- `docs/verite_terrain/7792-SO_ffab_complete.json` : Fichier de référence JSON couvert par le garde anti-dérive en égalité stricte (74 cibles).
- `seamtech_search/fiches/persistance.py` : Importation stricte sans fallback silencieux.
- `seamtech_search/sauvegarde.py` : Purge locale symétrique et rétention respectant le dernier dump.
- `tests/test_sauvegarde_unites.py` : Marqueur `sauvegarde` et 4 tests de rétention locale/symétrique.
- `tests/test_sauvegarde_restauration.py` : Marqueurs `postgres` et `sauvegarde`.
- `tests/test_extraction_fiche_reference.py` : Test de non-régression, test de banc étendu et garde anti-dérive (python + JSON).
- `scripts/audit_projet.py` : Invariant d'intégrité de la vérité terrain étendu (contrôle 6 avec égalité stricte python et JSON, 12 contrôles verts en mode rapide).
- `.github/workflows/ci.yml` : Retrait du filtre restrictif et renforcement du garde-fou collectés vs exécutés.
- `CHANGELOG.md` : Restauration intégrale des 22 sections de la base (`origin/arena/01a0c56d-seamtech-search`) + ajout de la section de vérité étendue en tête (23 sections au total, 0 suppression vs la base).
- `docs/verite_terrain/RECETTE_HUMAINE.md` : Précision rigoureuse sur l'unicité de `path_key`.
- `docs/verite_terrain/RUNBOOK_RESTAURATION.md` : Chiffres mesurés en CI consignés (runs 35738002767, 35738011591, 35738937509, 35738944082) et note explicite sur la réparation du job base cassé par la PR #21.
- `docs/verite_terrain/TRACABILITE_LIVRAISON.md` : Bascule des affirmations de sauvegarde et CI à l'état établi vert (✅).
- `docs/verite_terrain/RAPPORT_2026-09-22_LOT_H1_INTEGRATION.md` : Présent rapport.

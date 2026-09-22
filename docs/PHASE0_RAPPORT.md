# Rapport Phase 0 — Inventaire de l'archive & banc d'essai d'extraction

Branche : `phase0/inventaire-banc-essai` (base `main` @ `4efe2ad`)
Statut : **outillage livré et mesuré ; chiffres sur archive réelle en attente des entrées listées en fin de rapport.**

## 1. Ce qui est livré (réel, testé)

| Livrable | Fichier | Tests |
| --- | --- | --- |
| Inventaire d'archive en lecture seule | `scripts/inventaire_archive.py` | `tests/test_inventaire_archive.py` (14 tests) |
| Banc d'essai champ par champ (mode `--verite`) | `scripts/validate_extraction.py` | `tests/test_validate_extraction_mesure.py` (19 tests) |
| Recensement des familles de gabarits (empreintes libellés + positions) | intégré à `scripts/inventaire_archive.py` | idem inventaire |

Note d'écart assumé vis-à-vis du plan v3.0 §17.8 : le `benchmark_gabarit.py` prévu est réalisé comme
le mode `--verite` de `validate_extraction.py` (une seule porte d'entrée, un seul format de rapport de
calibration), plutôt qu'un second script.

### Garanties vérifiées par les tests

- **Aucune modification de l'archive** : empreintes SHA-256 + tailles + horodatages de chaque fichier,
  et arborescence complète, identiques avant/après exécution (`test_archive_inchangee_apres_inventaire`).
- Le dossier de rapport est **refusé** s'il est à l'intérieur d'une racine analysée (ou si une racine est
  dedans) — l'archive ne reçoit jamais d'écriture.
- Aucune dépendance ajoutée aux trois fichiers d'exigences (règle « une version par paquet » intacte) ;
  aucun appel réseau ; OCR désactivé (le texte est dans les PDF récents).

## 2. Chiffres mesurés aujourd'hui

### 2.1 Banc de mesure sur la fiche d'exemple du dépôt (`sample_data/CLIENT-123`)

Vérité terrain écrite à la main d'après la fiche (fixture de référence du dépôt) :

| Champ | Taux | Temps |
| --- | --- | --- |
| reference · material · quantity · description · dimensions.length · dimensions.width | **6/6 = 100 %** | **69 ms/fiche** |

> **Portée de ce chiffre (correction du 21/09) :** il est mesuré sur la fixture
> SYNTHÉTIQUE du dépôt (`sample_data/CLIENT-123`), pas sur un document client réel.
> Sur la vraie fiche 7792-SO, l'audit indépendant mesure 1/6 (16,7 %) avant réglage
> du gabarit. Un taux sur fixture synthétique est un test d'ingénierie, jamais une
> validation — voir `docs/DETECTION_FICHES.md`, règle du document réel.

### 2.2 Inventaire sur une archive de démonstration (synthétique, 10 fichiers)

Construite hors dépôt (`~/essai_phase0/archive-demo`) pour éprouver le pipeline complet : 2 familles de
gabarits de fiches, un plan, un scan sans couche texte, un doublon (copie de fiche), xlsx/txt/.XIN.

| Mesure | Valeur |
| --- | --- |
| Fichiers / dossiers / volume | 10 / 7 / 11,0 ko |
| Fiches techniques localisées | 3 (top : `2023/Sailonet-29er`, 2 fiches) |
| Scans probables détectés | 1/7 PDF (14,3 %) |
| Doublons probables | 1 groupe, 1 fichier redondant (1,7 ko) |
| Familles de gabarits | 3 (spi ×3 dont la copie ; génois n°1 ; génois n°2) |
| Durée d'exécution | 0,7 s (10 fichiers) → extrapolation linéaire : ~2 h pour 100 000 fichiers, empreintes SHA-256 incluses (20 Mo max/fichier) |

### 2.3 Constat important relevé par le banc (à traiter en lot B)

Les fiches « génois » de la démonstration portent des libellés (`Guindant`, `Bordure`, `Tissu`, `Surface`,
`Client`, `Navire`…) qui **ne font pas partie des ancres actuelles** du classifieur du dépôt
(`seamtech_search/anchors.py`). Résultat mesuré : ces fiches sont classées `plan_pdf` — elles sortent donc
du recensement des fiches et de l'indexation « technique » actuelle. C'est exactement le type d'angle mort
que la Phase 0 doit révéler : la liste `TECHNICAL_ANCHORS` devra être étendue au vocabulaire réel des
fiches (à caler sur les 20-30 fiches réelles, voir blocage n°1), et le lot B s'appuiera sur les gabarits
(ancres de variante) plutôt que sur la seule classification actuelle.

> **Mise à jour (PR 1, branche `phase0/fix-classement-fiches`)** : le constat du §2.3 est traité —
> détection structurelle indépendante du classifieur (lexique configurable `config/lexique_fiches.json`
> + structure de tableau), double vue et section « désaccords » dans l'inventaire. Voir
> `docs/DETECTION_FICHES.md`. La fiche génois de démonstration est désormais vue comme candidate et
> listée dans les désaccords ; les familles de gabarits passent de 1 (vue classifieur seule) à 3.

## 3. Vérifications d'état exécutées

| Contrôle | Résultat |
| --- | --- |
| `pytest` (suite complète) | **298 passés / 9 ignorés** (les 265 préexistants restent verts + 33 nouveaux ; les ignorés sont les intégrations PostgreSQL/Docker/MinIO non disponibles hors CI) |
| `ruff check .` | propre |
| `pip-audit -r requirements.txt` et `-r requirements-dev.txt` | aucune vulnérabilité connue |
| `scripts/coverage_gate.py` | plancher global 85 % et portes par module atteints |
| Note d'environnement | la suite doit être lancée avec des répertoires temporaires disposant de > 1 Go libres (`pytest --basetemp`), le garde-fou `min_free_bytes` (1 Go par défaut) renvoyant 507 sinon — comportement voulu du produit, constaté en bac à sable |

## 4. Ce qui reste ouvert / bloqué (les 4 demandes du plan §18 / prompt §8)

1. **20-30 fiches PDF réelles** (dont 1-2 d'un autre type de voile) : sans elles, le taux de lecture
   champ par champ ci-dessus ne porte que sur la fiche d'exemple. Le mode `--verite` est prêt ; il faut
   remplir le JSON de vérité pour chaque fiche réelle.
2. **Accès au disque de l'archive (lecture seule)** : le script d'inventaire est prêt ; le rapport chiffré
   réel (10 000 fiches, 50+ Go, part de scans réelle, localisation) attend le chemin.
3. **Confirmation R2** : des données clients (dumps + PDF validés) peuvent-elles aller dans les
   sauvegardes Cloudflare R2 ? Conditionne le scénario d'hébergement (§14.4), pas la Phase 0.
4. **Arbitrage matériel 8 vs 16 Go** : recommandé avant la Phase 2 (l'extraction et les embeddings
   tiennent sur 8 Go ; le modèle 3-4B de l'assistant est « tendu » sur 8 Go).

## 5. Prochaine étape proposée

Dès réception des fiches réelles : remplir la vérité terrain (format documenté dans
`scripts/validate_extraction.py`), produire le rapport de calibration champ par champ, puis lancer le
**lot A** (migrations 006-009, image `pgvector/pgvector:pg16`) en parallèle de la calibration — le lot A
ne dépend d'aucun chiffre d'extraction (décision §17.2 : il n'a pas de prédécesseur).

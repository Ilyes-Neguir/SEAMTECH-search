# Fiche de validation de référence — MODÈLE

> **MODÈLE VIDE** — copier ce fichier par fiche contrôlée.
> Tout exemple ci-dessous est marqué `EXEMPLE_SYNTHETIQUE` : il ne provient
> d'aucun document réel et ne doit pas être confondu avec une fiche client.
> Protocole d'emploi : `docs/PROTOCOLE_VALIDATION_HUMAINE.md`.

---

## En-tête de fiche

| Rubrique | Valeur |
|---|---|
| Code de fiche | `EXEMPLE_SYNTHETIQUE — 0000-XX-000` |
| Chemin du document | `<SOURCE>/CLIENT-EXEMPLE/fiche-synthetique.pdf` (relatif à la racine d'archive) |
| Version du gabarit | `gabarit-synthetique-v0` (celle appliquée par l'extraction) |
| Opérateur extraction | `EXEMPLE_SYNTHETIQUE — sans objet (extraction machine)` |
| Validateur | `Prénom N. (identifiant_compte)` |
| Date | `AAAA-MM-JJ` |
| Contrôle secondaire (si correction) | `Prénom N. (identifiant_compte)` + `AAAA-MM-JJ` |
| Statut final | `VALIDEE` \| `A_CORRIGER` \| `REJETEE` |

## Corps de validation — une ligne par champ

Colonnes : **Champ** (clé de schéma) · **Valeur extraite** · **Valeur
attendue** (telle que dans le document, accents compris) · **Résultat**
(`OK` / `ABSENTE` / `NON_APPLICABLE` / `CORRIGEE` / `ANOMALIE`) ·
**Commentaire** · **Page** (à partir de 1) · **Zone PDF** (`x0,y0,x1,y1`).

| Champ | Valeur extraite | Valeur attendue | Résultat | Commentaire | Page | Zone PDF |
|---|---|---|---|---|---|---|
| `fiche.code` | `EXEMPLE_SYNTHETIQUE 0000-XX-000` | `EXEMPLE_SYNTHETIQUE 0000-XX-000` | `OK` | cas synthétique | 1 | `72,700,220,720` |
| `fiche.titre` | `EXEMPLE_SYNTHETIQUE voile de test` | `EXEMPLE_SYNTHETIQUE voile de test` | `OK` | | 1 | `72,640,300,660` |
| `cotes.finie.slu_m` | `6,60` | `6,60 m` | `OK` | virgule décimale admise | 1 | `300,500,360,520` |
| `cotes.finie.sle_m` | `5,05` | `5,50 m` | `CORRIGEE` | `CORRECTION: 5,05 → 5,50 — inversion de chiffres (EXEMPLE)` | 1 | `300,470,360,490` |
| `cotes.finie.spa_m2` | — | `15,71 m²` | `ABSENTE` | valeur présente dans le tableau, non lue (EXEMPLE) | 1 | `300,410,360,430` |
| `materiau.epaisseur.1` | `Monofilm K903 0,15 mm` | `Monofilm K903 0,15 mm` | `OK` | rang vérifié | 2 | `72,300,320,320` |
| `galon.guindant.largeur_mm` | — | — | `NON_APPLICABLE` | modèle sans guindant cousu (EXEMPLE) | 2 | `72,200,320,220` |
| `jonction.laizes.1` | `laizes — zigzag 3/mm` | `laizes — zigzag 3/mm` | `OK` | ordre 1 vérifié | 2 | `72,120,360,140` |
| `cotes.dessin.shw_m` | `2,40` | `2,40 m` | `ANOMALIE` | `ANOMALIE: surface_incoherente — SPA/½·SLU·SLE hors bande (EXEMPLE)` | 1 | `360,500,420,520` |

## Décision finale

- [ ] Chaque champ a un résultat (aucune case vide)
- [ ] Aucune anomalie ouverte
- [ ] Toute correction contre-vérifiée (`controle_secondaire`) si applicable
- [ ] Statut final : `___________________`
- [ ] Validateur : `___________________` — Date : `____________`
- [ ] Contrôle secondaire (si ≥ 1 correction/anomalie) : `___________________` — Date : `____________`

> Rappel : une fiche corrigée ne peut être `VALIDEE` par l'opérateur qui a
> appliqué les corrections sans second contrôle (protocole §12).

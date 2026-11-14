# Matrice de recette fonctionnelle A→K

- items : 5
- prouvés : 3
- partiels : 2
- non prouvés : 0
- échecs : 0
- introuvables : 0

| Section | Item | Test écrit (résultat) | Navigateur / manuel | Statut |
|---|---|---|---|---|
| K · Sauvegarde et restauration | K1 Sauvegarde puis restauration sur base neuve — identité octet à octet | `test_10_sauvegarde_restauration_base_neuve`, `tests/test_sauvegarde_identite_octets.py` — 9✓ 0✗ 1⤼ 0! | — | **PARTIEL** |
| K · Sauvegarde et restauration | K2 Objets — sauvegarde et restauration sans écrasement | `test_sauvegarde_copie_objet_par_reseau_isole`, `test_s3rver_restauration_objet_refusee_sans_ecraser`, `tests/test_sauvegarde_objet_checksum.py` — 14✓ 0✗ 3⤼ 0! | — | **PARTIEL** |
| K · Sauvegarde et restauration | K3 Identité restreinte — l'outil n'utilise jamais l'administrateur | `test_outil_de_sauvegarde_utilise_l_identite_dediee_et_jamais_l_administrateur` — 1✓ 0✗ 0⤼ 0! | — | **PROUVÉ** |
| K · Sauvegarde et restauration | K4 Recette locale sur poste — les 20 contrôles | `tests/test_recette_locale.py` — 21✓ 0✗ 0⤼ 0! | scripts/recette_locale.ps1, à rejouer dans un venv disponible. | **PROUVÉ** |
| K · Sauvegarde et restauration | K5 Un échec de restauration nomme sa cause et s'arrête | `test_un_echec_de_restauration_nomme_sa_cause`, `test_sans_pre_requis_le_script_nomme_ce_qui_manque_et_refuse`, `test_recette_ps1_arrete_sur_echec_restauration_sans_poursuivre_down_up` — 3✓ 0✗ 0⤼ 0! | — | **PROUVÉ** |

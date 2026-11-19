"""Source unique dans le paquet : vérité terrain étendue de référence.

Fiche SYNTHÉTIQUE régénérable : ``sample_data/CLIENT-1042-EX/fiche-1042-EX_ffab.pdf``,
produite par ``scripts/generer_fiche_reference_synthetique.py``. Elle reprend la mise en
page de la fiche d'origine pour que les règles du gabarit FICHE_PORTANT_V1 s'appliquent
à l'identique, et n'en conserve que les valeurs techniques.

74 cibles réparties en 12 familles. Les 67 valeurs techniques (cotes, matières,
accastillage) sont celles de la fiche d'origine : elles n'identifient aucun client. Les
7 valeurs identifiantes — client, raison sociale, atelier, code de commande, dessinateur,
fichier source — y sont remplacées par des valeurs fictives.

Aucune valeur devinée ou extrapolée (RG6) : chaque cible est relue sur le document par
``evaluer_verite``, qui rend 74/74 sur cette fiche.
"""

from __future__ import annotations

VERITE_REFERENCE: dict[str, object] = {
    # --- bateau ---
    'bateau': '29er',
    # --- client ---
    'client': 'Exemplia',
    # --- cotes ---
    'cotes.dessin.poids_kg': 0.7,
    'cotes.dessin.sf_m': 3.08,
    'cotes.dessin.shw_m': 3.14,
    'cotes.dessin.sle_m': 5.5,
    'cotes.dessin.slu_m': 6.6,
    'cotes.dessin.tetiere_cm': 3.0,
    'cotes.finie.sf_m': 3.08,
    'cotes.finie.shw_m': 3.14,
    'cotes.finie.sle_m': 5.5,
    'cotes.finie.slu_m': 6.6,
    'cotes.finie.spa_m2': 15.71,
    # --- fiche ---
    'fiche.atelier': 'EX',
    'fiche.code': '1042-EX',
    'fiche.commande_numero': '1042-EX',
    'fiche.date_dessin': '2026-03-06',
    'fiche.date_edition': '2026-03-06',
    'fiche.dessinateur': 'Demo',
    'fiche.fichier_source': '1042-EX.xlsm',
    'fiche.montage_fil': 'V46',
    'fiche.montage_type': 'Collé/Cousu',
    'fiche.quantite': 1,
    'fiche.titre': 'Voile de portant',
    # --- finition ---
    'finition.amure': 'Œillet SR12',
    'finition.drisse': 'Œillet SR12',
    'finition.ecoute': 'Œillet SR12',
    # --- galon ---
    'galon.bordure': 'Blanc',
    'galon.bordure.grammage_g_m2': 65.0,
    'galon.bordure.largeur_mm': 50.0,
    'galon.bordure.matiere': 'Nylon',
    'galon.chute': 'Rouge',
    'galon.chute.grammage_g_m2': 65.0,
    'galon.chute.largeur_mm': 50.0,
    'galon.chute.matiere': 'Nylon',
    'galon.guindant': 'Bleu',
    'galon.guindant.grammage_g_m2': 65.0,
    'galon.guindant.largeur_mm': 50.0,
    'galon.guindant.matiere': 'Nylon',
    # --- gamme ---
    'gamme': 'Medium Régate',
    # --- jonction ---
    'jonction.horizontale': '2 Zigzag 6 tps 30mm',
    'jonction.horizontale.espacement_mm': 30.0,
    'jonction.horizontale.nb_points': 6,
    'jonction.horizontale.nb_zigzag': 2,
    'jonction.laizes': '1 zigzag 6 tps 15mm',
    'jonction.laizes.espacement_mm': 15.0,
    'jonction.laizes.nb_points': 6,
    'jonction.laizes.nb_zigzag': 1,
    'jonction.surplus': '~',
    'jonction.verticale': '2 Zigzag 6 tps 30mm',
    'jonction.verticale.espacement_mm': 30.0,
    'jonction.verticale.nb_points': 6,
    'jonction.verticale.nb_zigzag': 2,
    # --- materiau ---
    'materiau.epaisseur.1': 'Monofilm K903',
    'materiau.epaisseur.1.mesure_mm': 190.0,
    'materiau.epaisseur.2.grammage_g_m2': 270.0,
    'materiau.epaisseur.2.mesure_mm': 220.0,
    'materiau.epaisseur.3.grammage_g_m2': 210.0,
    'materiau.epaisseur.3.mesure_mm': 260.0,
    'materiau.epaisseur.4.grammage_g_m2': 170.0,
    'materiau.epaisseur.4.mesure_mm': 300.0,
    # --- option ---
    'option.protection_anti_uv': False,
    'option.retenue_contre_ecoute': False,
    'option.velcro_anti_deroulement': False,
    # --- renfort ---
    'renfort.1': 2,
    'renfort.1.diametre_mm': 200.0,
    'renfort.1.forme': 'œillets',
    'renfort.1.matiere': 'Nylon',
    'renfort.2': 1,
    'renfort.2.forme': 'dacron',
    'renfort.3': 1,
    'renfort.3.diametre_mm': 110.0,
    'renfort.3.forme': 'dacron',
    # --- type_voile ---
    'type_voile': 'Spi Asymétrique',
}

VERITE_REFERENCE_COMPLETE: dict[str, object] = VERITE_REFERENCE

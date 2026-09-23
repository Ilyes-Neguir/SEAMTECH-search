"""Tableau de bord qualité — Lot K.1 (plan v3.0 §17.3, Phase 6).

Sources réelles (jamais inventées) :
- fiche_champ_extrait : taux extraction auto, taux correction par champ
- fiche_validation + v_qualite : temps validation par fiche médiane+p95
- fiche_anomalie : anomalies plus fréquentes par type
- fiche : volume par statut / en attente validation
- recherche_log : usage recherches/jour + part sans résultat, split canal
- lot_import / lot_dossier : volume lots

Contraintes :
- Aucune agrégation Python si GROUP BY suffit (tout en SQL)
- Requêtes <100 ms sur 1 000 fiches (mesuré n+p50/p95, voir tests perf)
- Séparation utilisateur vs assistant via filtres->>'canal'
"""

from __future__ import annotations

from typing import Any


def _exiger_postgres(index: Any) -> None:
    if not getattr(index, "is_postgres", False):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Tableau qualité disponible sur PostgreSQL uniquement (migrations 006-009).",
        )


def taux_extraction_auto(cursor: Any) -> dict[str, Any]:
    """Taux extraction auto = part champs sans correction.

    Définition : COUNT(corrige=false) / COUNT(*)
    Unité : ratio 0-1 + pourcentage
    Période : tout historique
    """
    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE NOT corrige) AS auto,
               COUNT(*) FILTER (WHERE corrige) AS corriges
        FROM fiche_champ_extrait
        """
    )
    total, auto, corriges = cursor.fetchone()
    total = int(total or 0)
    auto = int(auto or 0)
    corriges = int(corriges or 0)
    taux = (auto / total) if total else None
    return {
        "definition": "Part des champs extraits sans correction humaine (corrige=false)",
        "unite": "ratio",
        "periode": "tout historique",
        "total_champs": total,
        "auto": auto,
        "corriges": corriges,
        "taux_auto": taux,
        "taux_auto_pct": round(taux * 100, 2) if taux is not None else None,
    }


def taux_correction_par_champ(cursor: Any) -> list[dict[str, Any]]:
    """Taux correction par champ — classement champs plus corrigés → quel gabarit améliorer.

    Définition : par champ, nb corrigés / total, trié par taux desc
    Unité : ratio + compte
    Période : tout historique
    GROUP BY suffit (pas d'agrégation Python)
    """
    cursor.execute(
        """
        SELECT champ,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE corrige) AS corriges,
               COUNT(*) FILTER (WHERE NOT corrige) AS auto,
               CASE WHEN COUNT(*) = 0 THEN 0
                    ELSE COUNT(*) FILTER (WHERE corrige)::float / COUNT(*) END AS taux_correction
        FROM fiche_champ_extrait
        GROUP BY champ
        ORDER BY taux_correction DESC, total DESC
        """
    )
    result = []
    for champ, total, corriges, auto, taux in cursor.fetchall():
        result.append(
            {
                "champ": str(champ),
                "total": int(total),
                "corriges": int(corriges),
                "auto": int(auto),
                "taux_correction": float(taux) if taux is not None else 0.0,
                "taux_correction_pct": round(float(taux) * 100, 2) if taux is not None else 0.0,
            }
        )
    return result


def temps_validation(cursor: Any) -> dict[str, Any]:
    """Temps validation par fiche médiane+p95.

    Définition : durée entre fiche.created_at (cree_le) et première validation (valide_le)
    Unité : secondes + minutes
    Période : fiches validées uniquement
    Utilise percentile_cont pour médiane et p95 en SQL (pas Python)
    """
    cursor.execute(
        """
        SELECT COUNT(*) AS nb,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (valide_le - cree_le))) AS mediane_s,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (valide_le - cree_le))) AS p95_s,
               AVG(EXTRACT(EPOCH FROM (valide_le - cree_le))) AS moyenne_s,
               MIN(EXTRACT(EPOCH FROM (valide_le - cree_le))) AS min_s,
               MAX(EXTRACT(EPOCH FROM (valide_le - cree_le))) AS max_s
        FROM v_qualite
        WHERE valide_le IS NOT NULL AND cree_le IS NOT NULL
        """
    )
    nb, mediane, p95, moyenne, min_s, max_s = cursor.fetchone()
    return {
        "definition": "Durée entre création fiche et première validation (valide_le - cree_le)",
        "unite": "secondes",
        "periode": "fiches validées uniquement",
        "nb_fiches_validees": int(nb or 0),
        "mediane_s": float(mediane) if mediane is not None else None,
        "p95_s": float(p95) if p95 is not None else None,
        "moyenne_s": float(moyenne) if moyenne is not None else None,
        "min_s": float(min_s) if min_s is not None else None,
        "max_s": float(max_s) if max_s is not None else None,
        "mediane_min": round(float(mediane) / 60, 2) if mediane is not None else None,
        "p95_min": round(float(p95) / 60, 2) if p95 is not None else None,
    }


def anomalies_frequentes(cursor: Any) -> list[dict[str, Any]]:
    """Anomalies plus fréquentes par type.

    Définition : COUNT par code anomalie
    Unité : compte
    Période : tout historique
    """
    cursor.execute(
        """
        SELECT code, gravite, COUNT(*) AS nb, COUNT(*) FILTER (WHERE statut='a_traiter') AS a_traiter
        FROM fiche_anomalie
        GROUP BY code, gravite
        ORDER BY nb DESC
        """
    )
    return [
        {
            "code": str(code),
            "gravite": str(gravite),
            "nb": int(nb),
            "a_traiter": int(a_traiter),
        }
        for code, gravite, nb, a_traiter in cursor.fetchall()
    ]


def volume_par_statut(cursor: Any) -> dict[str, Any]:
    """Volume fiches par statut / en attente validation.

    Définition : COUNT par statut fiche
    Unité : compte
    Période : instantané
    """
    cursor.execute("SELECT statut, COUNT(*) FROM fiche GROUP BY statut ORDER BY COUNT(*) DESC")
    par_statut = {str(s): int(n) for s, n in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) FROM fiche WHERE statut='a_valider'")
    en_attente = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM fiche")
    total = int(cursor.fetchone()[0])
    return {
        "definition": "Nombre de fiches par statut, dont en attente validation (a_valider)",
        "unite": "compte",
        "periode": "instantané",
        "total": total,
        "en_attente_validation": en_attente,
        "par_statut": par_statut,
    }


def usage_recherches(cursor: Any) -> dict[str, Any]:
    """Usage recherches/jour + part sans résultat, split utilisateur vs assistant.

    Définition :
    - par jour : COUNT total, COUNT sans résultat (nb_resultats=0), ratio sans résultat
    - par canal : filtres->>'canal' (utilisateur vs assistant), NULL → utilisateur
    Unité : compte + ratio
    Période : 30 derniers jours (journal)
    """
    cursor.execute(
        """
        SELECT date_trunc('day', created_at)::date AS jour,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE nb_resultats=0) AS sans_resultat,
               CASE WHEN COUNT(*)=0 THEN 0
                    ELSE COUNT(*) FILTER (WHERE nb_resultats=0)::float / COUNT(*) END AS part_sans_resultat
        FROM recherche_log
        WHERE created_at >= now() - interval '30 days'
        GROUP BY jour
        ORDER BY jour DESC
        """
    )
    par_jour = [
        {
            "jour": str(jour),
            "total": int(total),
            "sans_resultat": int(sans),
            "part_sans_resultat": float(part) if part is not None else 0.0,
            "part_sans_resultat_pct": round(float(part) * 100, 2) if part is not None else 0.0,
        }
        for jour, total, sans, part in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT COALESCE(filtres->>'canal', 'utilisateur') AS canal,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE nb_resultats=0) AS sans_resultat,
               CASE WHEN COUNT(*)=0 THEN 0
                    ELSE COUNT(*) FILTER (WHERE nb_resultats=0)::float / COUNT(*) END AS part_sans_resultat
        FROM recherche_log
        WHERE created_at >= now() - interval '30 days'
        GROUP BY canal
        ORDER BY total DESC
        """
    )
    par_canal = [
        {
            "canal": str(canal),
            "total": int(total),
            "sans_resultat": int(sans),
            "part_sans_resultat": float(part) if part is not None else 0.0,
            "part_sans_resultat_pct": round(float(part) * 100, 2) if part is not None else 0.0,
        }
        for canal, total, sans, part in cursor.fetchall()
    ]

    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE nb_resultats=0) AS sans_resultat,
               CASE WHEN COUNT(*)=0 THEN 0
                    ELSE COUNT(*) FILTER (WHERE nb_resultats=0)::float / COUNT(*) END AS part_sans_resultat
        FROM recherche_log
        WHERE created_at >= now() - interval '30 days'
        """
    )
    total, sans, part = cursor.fetchone()
    total = int(total or 0)
    sans = int(sans or 0)
    part = float(part) if part is not None else 0.0

    return {
        "definition": "Usage recherches par jour et par canal (filtres->>'canal'), part sans résultat",
        "unite": "compte + ratio",
        "periode": "30 derniers jours",
        "total_30j": total,
        "sans_resultat_30j": sans,
        "part_sans_resultat_30j": part,
        "part_sans_resultat_pct_30j": round(part * 100, 2),
        "par_jour": par_jour,
        "par_canal": par_canal,
    }


def lots_stats(cursor: Any) -> dict[str, Any]:
    """Volume lots / imports.

    Définition : COUNT par statut lot_import et lot_dossier
    Unité : compte
    Période : tout historique
    """
    cursor.execute("SELECT statut, COUNT(*) FROM lot_import GROUP BY statut")
    par_statut_lot = {str(s): int(n) for s, n in cursor.fetchall()}
    cursor.execute("SELECT statut, COUNT(*) FROM lot_dossier GROUP BY statut")
    par_statut_dossier = {str(s): int(n) for s, n in cursor.fetchall()}
    cursor.execute("SELECT COUNT(*) FROM lot_import")
    total_lots = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM lot_dossier")
    total_dossiers = int(cursor.fetchone()[0])
    return {
        "definition": "Volume lots d'import et dossiers par statut",
        "unite": "compte",
        "periode": "tout historique",
        "total_lots": total_lots,
        "total_dossiers": total_dossiers,
        "par_statut_lot": par_statut_lot,
        "par_statut_dossier": par_statut_dossier,
    }


def doublons_detectes(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 - curseur psycopg2 réel
    """Doublons détectés — Lot L.1 (§17.4), indicateur 8 du tableau de bord.

    Trois compteurs, tous en SQL ``GROUP BY`` / ``FILTER`` comme le reste du
    module (aucune agrégation Python) :

    - ``groupes_exacts`` : nombre d'empreintes SHA-256 portées par PLUSIEURS
      fiches (``GROUP BY empreinte_sha256 HAVING count(DISTINCT id_fiche) > 1``
      sur ``fiche_piece_jointe``) — le même critère que la détection, pas une
      approximation ;
    - ``liens_probables`` : lignes ``fiche_lien`` de type ``doublon_probable``
      (les propositions de rapprochement par titre déjà enregistrées) ;
    - ``doublons_vus_avant_validation`` : nombre de fiches **non ``valide```**
      engagées dans un lien de doublon, dans un sens ou dans l'autre. C'est LE
      chiffre du lot : combien de doublons ont été vus AVANT la validation,
      donc combien ont pu être arbitrés sans qu'une fiche validée soit à
      reprendre.

    Un doublon vu n'est pas un doublon traité : cet indicateur mesure la
    DÉTECTION, jamais une décision.
    """
    cursor.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT p.empreinte_sha256
            FROM fiche_piece_jointe p
            WHERE p.empreinte_sha256 IS NOT NULL AND p.empreinte_sha256 <> ''
            GROUP BY p.empreinte_sha256
            HAVING COUNT(DISTINCT p.id_fiche) > 1
        ) AS groupes
        """
    )
    groupes_exacts = int(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM fiche_lien WHERE type = 'doublon_probable'")
    liens_probables = int(cursor.fetchone()[0] or 0)

    cursor.execute(
        """
        SELECT COUNT(DISTINCT f.id_fiche)
        FROM fiche f
        WHERE f.statut <> 'valide'
          AND (
              EXISTS (SELECT 1 FROM fiche_lien l WHERE l.id_fiche_source = f.id_fiche)
              OR EXISTS (SELECT 1 FROM fiche_lien l WHERE l.id_fiche_cible = f.id_fiche)
          )
        """
    )
    avant_validation = int(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM fiche_lien WHERE type = 'doublon_exact'")
    liens_exacts = int(cursor.fetchone()[0] or 0)

    return {
        "definition": (
            "Doublons détectés (empreintes SHA-256 partagées + liens probables) et nombre de fiches "
            "NON validées déjà engagées dans un lien de doublon — donc vues avant validation. "
            "Détection PROPOSITIVE : aucun effacement, aucune fusion."
        ),
        "unite": "compte",
        "periode": "instantané",
        "groupes_exacts": groupes_exacts,
        "liens_exacts": liens_exacts,
        "liens_probables": liens_probables,
        "doublons_vus_avant_validation": avant_validation,
    }


def taux_par_utilisateur(cursor: Any) -> dict[str, Any]:  # noqa: ANN401 - curseur psycopg2 réel
    """Actions de validation attribuées à un compte nominatif — Lot L.2, indicateur 9.

    Deux chiffres, dans cet ordre d'importance :

    - ``actions_attribuees`` / ``part_attribuee`` : combien d'actions portent
      l'identité d'un compte nominatif (``fiche_validation.id_utilisateur``).
    - ``actions_sans_utilisateur`` : combien n'en portent aucune.

    Le second n'est PAS une anomalie en soi : toutes les validations faites
    avant L.2 sont anonymes, et le rattrapage n'existe pas — on n'invente pas
    d'attribution a posteriori. L'indicateur sert à mesurer la couverture
    réelle, pas à désigner un coupable. Aucune écriture, aucune correction
    automatique ici : lecture seule, comme tout le tableau.
    """
    cursor.execute(
        """
        SELECT COUNT(*)::int AS actions_total,
               COUNT(id_utilisateur)::int AS actions_attribuees
        FROM fiche_validation
        """
    )
    total, attribuees = (int(valeur or 0) for valeur in cursor.fetchone())

    cursor.execute(
        """
        SELECT u.identifiant, u.nom, u.role, COUNT(*)::int AS actions
        FROM fiche_validation v
        JOIN utilisateur u ON u.id_utilisateur = v.id_utilisateur
        GROUP BY u.identifiant, u.nom, u.role
        ORDER BY COUNT(*) DESC, u.identifiant
        """
    )
    par_utilisateur = [
        {
            "identifiant": str(ligne[0]),
            "nom": ligne[1],
            "role": str(ligne[2]),
            "actions": int(ligne[3]),
        }
        for ligne in cursor.fetchall()
    ]
    return {
        "definition": (
            "Répartition des actions de validation par compte nominatif ; "
            "« sans utilisateur » regroupe les actions antérieures à L.2, jamais réattribuées a posteriori."
        ),
        "unite": "action",
        "periode": "depuis l'origine",
        "actions_total": total,
        "actions_attribuees": attribuees,
        "actions_sans_utilisateur": total - attribuees,
        "part_attribuee": round(attribuees / total, 4) if total else None,
        "par_utilisateur": par_utilisateur,
        "comptes_actifs_sans_action": _comptes_sans_action(cursor, par_utilisateur),
    }


def _comptes_sans_action(cursor: Any, par_utilisateur: list[dict[str, Any]]) -> int:  # noqa: ANN401
    """Comptes actifs n'ayant encore rien validé (informatif, jamais bloquant)."""
    avec_action = {entree["identifiant"] for entree in par_utilisateur}
    cursor.execute("SELECT identifiant FROM utilisateur WHERE actif ORDER BY identifiant")
    return sum(1 for (identifiant,) in cursor.fetchall() if str(identifiant) not in avec_action)


def tableau_de_bord(index: Any) -> dict[str, Any]:
    """Assemble le tableau de bord complet — toutes requêtes en GROUP BY.

    Chaque indicateur porte définition+unité+période (exigence Lot K).
    """
    _exiger_postgres(index)
    with index.connect() as conn:
        with conn.cursor() as cursor:
            taux_auto = taux_extraction_auto(cursor)
            par_champ = taux_correction_par_champ(cursor)
            temps_val = temps_validation(cursor)
            anomalies = anomalies_frequentes(cursor)
            volume = volume_par_statut(cursor)
            recherches = usage_recherches(cursor)
            lots = lots_stats(cursor)
            doublons = doublons_detectes(cursor)
            par_utilisateur = taux_par_utilisateur(cursor)

    return {
        "taux_extraction_auto": taux_auto,
        "taux_correction_par_champ": par_champ,
        "temps_validation": temps_val,
        "anomalies_frequentes": anomalies,
        "volume_par_statut": volume,
        "usage_recherches": recherches,
        "lots": lots,
        "doublons_detectes": doublons,
        "taux_par_utilisateur": par_utilisateur,
    }

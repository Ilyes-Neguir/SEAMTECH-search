# ADR 0002 — Identité d'octets et liaison au catalogue

- **Statut : proposé, non qualifié.**
- **Date :** 2026-10-31
- **Numéro réservé :** `032_identite_octets_documents` (migrations de l'index commun, SQLite et PostgreSQL). Vérifier à nouveau l'absence de collision avant publication.

## Constat

`documents.content_hash` n'a pas un sens unique : l'indexeur y met une empreinte du texte, tandis que le dépôt de fiches y met une empreinte du fichier. Une reprise peut envoyer un objet conforme à l'import, mais l'association par chemin seul pourrait écraser le document d'un autre import ou lier un contenu différent. Les anciennes lignes ne permettent pas de déduire leur identité d'octets.

## Décision proposée

1. Ajouter une colonne nullable `source_sha256`, distincte de `content_hash`, sans backfill ni déduction sur les anciens documents. Un hash n'est renseigné que lorsqu'un flux d'octets déterminé est connu. La migration installe un déclencheur SQL qui invalide l'identité à toute réécriture des champs de représentation, même par un ancien binaire ignorant la nouvelle colonne et même si les valeurs écrites sont identiques. L'indexeur qui apporte une capture rétablit son SHA par une seconde instruction dans la même transaction, uniquement sur les lignes effectivement modifiées. Les historiques restent inconnus et non qualifiés.
2. Lors d'un import capturé, associer l'empreinte de la copie au document dans la même transaction que le payload. Une capture différente ne remplace pas l'identité connue, même si l'ancienne ligne n'a pas encore d'objet. SQLite verrouille l'écriture avant la lecture de la garde ; PostgreSQL applique la condition dans `ON CONFLICT`. Lors de la reprise, lier une preuve de transport identifiable uniquement à la ligne courante de même clé logique ET de même SHA de capture. Ne jamais remplacer une autre clé objet. Une ligne absente, modifiée, ancienne ou déjà liée autrement devient un conflit visible ; le payload reste en revue.
3. Mettre à jour la liaison et l'état de l'import dans une seule transaction SQL, ordonner les verrous, préserver les identifiants et les références métier, et ne tenir aucun verrou SQL pendant les appels au stockage. Les résultats incertains restent en réconciliation ; aucun succès global ou purge ne sont autorisés sur cette base seule.
4. Les accès `/pieces`, `/open` et artefacts d'import vérifient une copie bornée sur disque temporaire avant réponse lorsqu'un SHA existe ; ils peuvent servir une autre copie conforme. Les accès hérités sans SHA restent compatibles mais non vérifiés. Les corps S3 et les descripteurs épinglés sont fermés à la fin ou en cas d'interruption observée. Le démarrage et `/ready` exigent colonne, marqueur et déclencheur actif ; `/health` expose le diagnostic. Une sauvegarde actuelle et la vérification/restauration d'un nouveau manifeste contrôlent aussi la présence du déclencheur. Les copies locales et objet sont comparées au SHA du snapshot PostgreSQL lorsqu'il est connu ; une divergence est signalée et n'est pas copiée. L'identité des lignes historiques sans SHA demeure inconnue.
5. Vérifier toutes les voies d'écriture de `documents` et leurs courses, les liens déjà protégés, rollback au moment de la publication, copies représentatives et compatibilité de restauration. Le contrôle du déclencheur ne prouve pas que sa définition SQL n'a pas été modifiée ; les écritures directes dans les structures de recherche et une désactivation entre deux opérations ne sont pas qualifiées.

## Migration et retour arrière

DDL additive nullable, fonction et déclencheur, sans index ni mise à jour de masse ; migrations idempotentes, une seule fois au démarrage, sur copie et bases jetables avant tout déploiement. Mesurer le verrou PostgreSQL et le coût des déclencheurs sur données représentatives. Le retour arrière logiciel conserve la colonne, la fonction et le déclencheur : les supprimer pendant qu'une ancienne version écrit pourrait rendre les identités restantes mensongères. Ne pas supprimer la colonne ni effacer les preuves sur une base réelle. Le retour arrière complet s'appuie sur la sauvegarde coordonnée de la base et des objets, actuellement non qualifiée.

Ce choix ne prouve pas un instantané global, l'exclusivité des écritures S3, le fencing Redis/PostgreSQL, ni les comportements Windows, charge ou restauration. Il ne rend aucun point PRÊT.

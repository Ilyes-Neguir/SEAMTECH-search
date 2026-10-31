# ADR 0001 — Confinement du scan et de l'import de dossiers

- **Statut : proposé pour le parcours complet ; scan POSIX partiellement réalisé.**
- **Date :** 2026-10-31

## Contexte

Un chemin vérifié avant lecture peut être remplacé par un lien ou par un répertoire différent avant l'extraction ou le transfert. Le parcours historique examine les fichiers au scan, les relit lors de l'import puis peut les relire lors d'une correction ou reprise. Réutiliser le nom seul ne prouve ni que les octets sont ceux analysés ni qu'ils proviennent encore du dossier autorisé. Les rapports d'incident et les essais utilisent uniquement des fichiers synthétiques.

## Décision partielle réalisée

Le scan POSIX inventorie depuis des répertoires ouverts sans suivre les liens, puis copie chaque PDF/Excel depuis un descripteur ouvert relativement à la racine épinglée, avec limite de taille et contrôle des métadonnées de fichier avant/après copie. Il rejette aussi les fichiers possédant plusieurs liens physiques. Les documents sont analysés à partir des copies temporaires ; les chemins retournés restent les noms d'origine. Cela réduit certaines courses testées, mais les noms peuvent être périmés à la confirmation et les métadonnées ne prouvent pas un snapshot atomique. Le scan refuse les plateformes sans primitives nécessaires, notamment Windows pour l'instant ; ce refus est une régression fonctionnelle explicite à résoudre avant qualification.

## Conception à prouver pour le parcours complet

1. L'inventaire, la sélection et la lecture doivent partager une identité stable et confinée, sans résolution tardive de chemins modifiables. Stocker un manifeste structuré associant chaque chemin logique à sa taille, son empreinte de contenu et une source durable effectivement préservée.
2. Les octets utilisés pour classification, extraction, indexation et envoi doivent correspondre à ce manifeste. Vérifier aussi les reprises, corrections et relectures ; un original absent ou modifié doit produire une erreur explicite, jamais une preuve de préservation erronée.
3. Conserver les chemins logiques, noms et clés/metadata S3 compatibles, sans publier les chemins temporaires. Aucun écrasement d'original. S'assurer que les rapports et objets restent associés aux documents adéquats.
4. Chiffrage et droits des fichiers intermédiaires selon le modèle de menace retenu ; politique d'espace disque bornée, concurrence, interruption et reprise sans purge prématurée. Une copie complète temporaire multiplie potentiellement l'espace requis ; il ne faut pas introduire une solution qui bloque silencieusement les grands dossiers. Quantifier débit et volume et tester le nettoyage après crash dur.
5. Assurer des garde-fous équivalents sur Windows et les stockages supportés ou refuser clairement l'environnement non qualifié. Tester les manipulations de liens, chemins et répertoires par des processus concurrents réels, pas seulement des mocks.
6. Rejouer notamment l'échec documenté dans `tests/test_import_workflow.py` à la lecture initiale et à l'envoi après extraction, les tests de reprise/correction et les tests S3 avec relecture des octets. Vérifier également les chemins de nettoyage des workers et les versions PG/Redis qui maintiennent l'état durable.

## Alternatives et conséquences

- `resolve()` et contrôles `exists()` successifs : insuffisants face à la substitution entre contrôle et utilisation.
- Descripteur ouvert et conservé : protège l'identité de l'entrée mais pas une modification des octets en place, et conserver un descripteur par fichier ne passe pas nécessairement à l'échelle de grandes arborescences.
- Copie privée : évite les réouvertures de la source après copie mais ne gèle pas toute l'arborescence à un instant global ; limite de taille, saturation disque, crash, même UID et garanties de durabilité restent à traiter.
- Stockage objet versionné et empreintes : utile seulement avec qualification du fournisseur, gestion du résultat ambigu et conservation du manifeste. Une preuve de transport seule ne lie pas automatiquement l'analyse aux mêmes octets.

Cette décision ne déclare aucune porte de production prête et ne remplace pas les exigences de sauvegarde/restauration coordonnée.

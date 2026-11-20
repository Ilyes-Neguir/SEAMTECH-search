# SEAMTECH Search — Produit

## Vision
Outil interne de gestion de fiches de voiles pour une voilerie technique. Recherche, extraction, validation et comparaison de fiches de fabrication (spi, génois, grand-voile, etc.) avec traçabilité complète (« qui a validé quoi »).

## Utilisateurs
- **Approbateurs** : valident les fiches extraites, corrigent les cotes
- **Utilisateurs** : déposent des dossiers, consultent les fiches
- **Lecture** : consultent sans modifier
- **Admin** : gèrent les comptes et gabarits
- **Secours** : compte d'urgence (mot de passe partagé)

## Parcours critiques
1. Connexion nominative (login)
2. Création de compte (nouveau)
3. Recherche de fiches (`/recherche`)
4. Import de dossier (`/nouveau`)
5. Validation des fiches (`/validation`)
6. Comparaison de voiles (`/comparaison` via bouton `/recherche`)

## Principes
- **Lisibilité avant tout** : les tableaux denses restent lisibles, le décor ne gêne jamais
- **Traçabilité** : chaque validation est attribuée à un compte nominatif
- **Honnêteté** : les écarts sont calculés, jamais inventés ; « — » = valeur non lue
- **Hors-ligne** : aucun CDN, aucune ressource externe, 100 % local
- **Accessibilité** : WCAG AA, navigation clavier, focus visible, reduced-motion respecté

## Non-objectifs
- Pas de site vitrine, pas de marketing
- Pas de 3D sur les tableaux de données
- Pas de glassmorphism, pas de dégradés violet/bleu

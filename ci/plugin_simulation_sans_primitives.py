"""Simule une plateforme SANS primitives POSIX (ce qu'est Windows pour ce produit)."""
import seamtech_search.plateforme as pf


def capacites_sans_primitives():
    return {nom: False for nom in pf.CAPACITES}


pf.capacites = capacites_sans_primitives

# Usage (depuis la racine du dépôt) :
#   PYTHONPATH=.:ci python -m pytest -p plugin_simulation_sans_primitives <sélection>
#
# Tout test qui échoue SOUS ce plugin, et qui passe SANS lui, dépend des
# primitives POSIX de confinement : il ne peut pas passer sur un Windows
# natif, et c'est une raison ÉTABLIE — pas une supposition — de l'inscrire
# dans ci/ecarts-windows.txt.

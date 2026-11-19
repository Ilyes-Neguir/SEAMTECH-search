/**
 * Libellés des filtres de recherche — ce que l'écran montre, pas ce qu'il envoie.
 *
 * La valeur d'un filtre est une CLÉ : c'est elle qui part au backend, et elle
 * doit rester exacte. Mais la clé n'est pas toujours ce qu'on peut lire. Deux
 * cas, l'un bénin et l'autre non :
 *
 *   - « voile » est une clé de catégorie ; la liste des facettes affiche
 *     « Voiles ». Afficher « voile » dans la pastille du filtre actif donnait
 *     deux écritures pour la même chose ;
 *   - « 42 » est un identifiant de modèle de bateau. Un nombre nu n'est pas un
 *     libellé : personne ne sait ce qu'est le modèle 42. On écrit ce que c'est
 *     (« modèle n° 42 ») au lieu de faire croire qu'on sait son nom — une
 *     pastille n'invente jamais un nom qu'elle n'a pas.
 */

export const LIBELLES_VALEURS_FACETTES: Record<string, Record<string, string>> = {
  categorie: { voile: "Voiles", accessoire: "Accessoires" },
}

/**
 * Libellé affichable de la valeur d'un filtre actif.
 *
 * Règle : la clé reste la clé (elle sert à retirer le filtre), seul l'AFFICHAGE
 * est traduit. Et quand aucune traduction n'existe, la valeur brute est rendue
 * telle quelle — jamais vide, jamais remplacée par un à-peu-près.
 */
export function libelleFiltreActif(cle: string, valeur: string): string {
  if (cle === "boat_model_id" && /^\d+$/.test(valeur)) return `modèle n° ${valeur}`
  return LIBELLES_VALEURS_FACETTES[cle]?.[valeur] ?? valeur
}

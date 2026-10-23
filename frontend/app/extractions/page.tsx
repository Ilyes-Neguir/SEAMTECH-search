import { requireAuthValide } from "@/lib/auth-helpers";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function Page() {
  const auth = await requireAuthValide();
  if (!auth.ok) redirect("/login");
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Extractions — Lot 3a/3b</h1>
      <p className="text-sm text-gray-600 mb-4">Lancez une extraction sur un document, puis rattachez/corrigez (approbateur). 3-clics : Recherche → Fiche produit → Validation.</p>
      <div data-testid="extractions-app" className="space-y-4">
        <div className="border p-4 bg-white rounded">
          <h2 className="font-medium mb-2">Lancer extraction (POST /api/extractions/run)</h2>
          <p className="text-xs text-gray-500">Utilisez l'API : {`{ document_id, texte }`} → sail_extractions avec provenance + cohérence.</p>
        </div>
        <div className="border p-4 bg-white rounded">
          <h2 className="font-medium mb-2">À valider (approbateur)</h2>
          <p className="text-xs text-gray-500">GET /extractions/a-valider/list — écran côte-à-côte source / extraction à implémenter.</p>
        </div>
      </div>
    </div>
  );
}

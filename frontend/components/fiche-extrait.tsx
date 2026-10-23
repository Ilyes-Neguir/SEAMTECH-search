"use client";
import { useState } from "react";

type Extraction = {
  id: number;
  document_id: number;
  sail_type_normalized: string | null;
  boat_model_raw: string | null;
  luff_length_cm: number | null;
  leech_length_cm: number | null;
  foot_length_cm: number | null;
  luff_perpendicular_cm: number | null;
  sail_area_sqm: number | null;
  confidence_overall: number | null;
  coherence_score: number | null;
  coherence_flags_json: string[] | null;
  field_provenance_json: Record<string, any> | null;
  extraction_status: string;
};

export default function FicheExtrait({ extraction }: { extraction: Extraction }) {
  const [showProvenance, setShowProvenance] = useState(false);
  if (!extraction) return <div data-testid="fiche-extrait-vide">Aucune extraction</div>;
  const champs = [
    { label: "Type de voile", value: extraction.sail_type_normalized, raw: extraction.field_provenance_json?.["sail_type"]?.raw },
    { label: "Modèle bateau", value: extraction.boat_model_raw },
    { label: "Guindant (luff)", value: extraction.luff_length_cm ? `${extraction.luff_length_cm} cm` : "—" },
    { label: "Chute (leech)", value: extraction.leech_length_cm ? `${extraction.leech_length_cm} cm` : "—" },
    { label: "Bordure (foot)", value: extraction.foot_length_cm ? `${extraction.foot_length_cm} cm` : "—" },
    { label: "LP", value: extraction.luff_perpendicular_cm ? `${extraction.luff_perpendicular_cm} cm` : "—" },
    { label: "Surface", value: extraction.sail_area_sqm ? `${extraction.sail_area_sqm} m²` : "—" },
  ];
  return (
    <div data-testid="fiche-extrait" className="border rounded p-4 bg-white">
      <h3 className="font-semibold mb-2">Fiche produit — Extrait du document</h3>
      <div className="grid grid-cols-2 gap-2 text-sm">
        {champs.map((c) => (
          <div key={c.label} className="flex flex-col">
            <span className="text-gray-500">{c.label}</span>
            <span data-testid={`champ-${c.label}`} className="font-medium">{String(c.value ?? "—")}</span>
            {c.raw && <span className="text-xs text-gray-400">source: {c.raw}</span>}
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-4 text-xs">
        <span>Confiance: <strong>{extraction.confidence_overall ?? "—"}</strong></span>
        <span>Cohérence: <strong>{extraction.coherence_score ?? "—"}</strong></span>
        <span>Statut: <strong>{extraction.extraction_status}</strong></span>
      </div>
      {extraction.coherence_flags_json && extraction.coherence_flags_json.length > 0 && (
        <div data-testid="coherence-flags" className="mt-2 text-xs text-amber-600">
          Drapeaux: {extraction.coherence_flags_json.join(", ")}
        </div>
      )}
      <button onClick={() => setShowProvenance(!showProvenance)} data-testid="btn-provenance" className="mt-3 text-xs underline">
        {showProvenance ? "Masquer provenance" : "Voir provenance détaillée"}
      </button>
      {showProvenance && extraction.field_provenance_json && (
        <pre data-testid="provenance-json" className="mt-2 bg-gray-50 p-2 text-xs overflow-auto">{JSON.stringify(extraction.field_provenance_json, null, 2)}</pre>
      )}
      <p className="mt-2 text-xs text-gray-400">3-clics : Recherche → Fiche produit → Détail source (page/bbox)</p>
    </div>
  );
}

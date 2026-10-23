"use client";
import { useState } from "react";

export default function ConfiancePanel() {
  const [modelId, setModelId] = useState("");
  const [sailType, setSailType] = useState("gv");
  const [justification, setJustification] = useState("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  async function calculer() {
    setError(null);
    const r = await fetch(`/api/evaluations/confiance/calcul`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ boat_model_id: Number(modelId), sail_type_normalized: sailType, justification: justification || undefined }), cache: "no-store" });
    const j = await r.json();
    if (!r.ok) { setError(j.detail || "Erreur"); return; }
    setResult(j);
  }
  async function charger() {
    setError(null);
    const r = await fetch(`/api/evaluations/confiance?boat_model_id=${modelId}&sail_type_normalized=${sailType}`, { cache: "no-store" });
    const j = await r.json();
    if (!r.ok) { setError(j.detail || "Introuvable"); return; }
    setResult(j);
  }

  return (
    <div data-testid="confiance-panel" className="border rounded p-4 bg-white">
      <h3 className="font-semibold mb-2">Fonction B — Évaluations confiance (médiane/MAD, N≥5, seuil 75%)</h3>
      <p className="text-xs text-gray-500 mb-2">INSUFFISANT si N&lt;5, HETEROGENE si &gt;75% hors bande, OK sinon. Justification obligatoire si HETEROGENE.</p>
      <div className="flex gap-2 mb-2">
        <input data-testid="input-model" value={modelId} onChange={e=>setModelId(e.target.value)} placeholder="boat_model_id" className="border px-2 py-1 text-sm" />
        <input data-testid="input-type" value={sailType} onChange={e=>setSailType(e.target.value)} placeholder="sail_type (gv/ge/spi…)" className="border px-2 py-1 text-sm" />
      </div>
      <textarea data-testid="input-justif" value={justification} onChange={e=>setJustification(e.target.value)} placeholder="Justification (obligatoire si HETEROGENE)" className="border w-full px-2 py-1 text-sm mb-2" rows={2} />
      <div className="flex gap-2">
        <button data-testid="btn-calcul" onClick={calculer} className="bg-green-600 text-white px-3 py-1 text-sm rounded">Calculer</button>
        <button data-testid="btn-charger" onClick={charger} className="bg-gray-600 text-white px-3 py-1 text-sm rounded">Charger</button>
      </div>
      {error && <div data-testid="confiance-error" className="text-sm text-red-600 mt-2">{error}</div>}
      {result && (
        <div data-testid="confiance-result" className="mt-3">
          <div className="text-sm">Statut: <strong data-testid="confiance-status">{result.confiance_status || result.status}</strong> — N={result.sample_size ?? result.n}</div>
          {result.stats_json && <pre className="bg-gray-50 p-2 text-xs overflow-auto mt-2">{JSON.stringify(result.stats_json, null, 2)}</pre>}
          {result.stats && <pre className="bg-gray-50 p-2 text-xs overflow-auto mt-2">{JSON.stringify(result.stats, null, 2)}</pre>}
          <pre className="bg-gray-50 p-2 text-xs overflow-auto mt-2">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

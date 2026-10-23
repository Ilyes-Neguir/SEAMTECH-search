"use client";
import { useState } from "react";

type Delta = { a: number | null; b: number | null; delta_cm: number | null; delta_pct: number | null; flag: string; unit: string };

export default function CompareSails() {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function comparer() {
    setError(null); setLoading(true);
    try {
      const r = await fetch(`/api/compare/sails?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, { cache: "no-store" });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Erreur comparaison");
      setResult(j);
    } catch (e: any) {
      setError(e.message);
    } finally { setLoading(false); }
  }

  return (
    <div data-testid="compare-sails" className="border rounded p-4 bg-white">
      <h3 className="font-semibold mb-3">Fonction A — Comparaison bloc-par-bloc Δcm / Δ%</h3>
      <p className="text-xs text-gray-500 mb-3">Même modèle + même type requis (422 sinon). 3-clics : Sélectionner 2 voiles → Comparer → Voir écarts.</p>
      <div className="flex gap-2 mb-3">
        <input data-testid="input-a" value={a} onChange={e=>setA(e.target.value)} placeholder="ID voile A" className="border px-2 py-1 text-sm" />
        <input data-testid="input-b" value={b} onChange={e=>setB(e.target.value)} placeholder="ID voile B" className="border px-2 py-1 text-sm" />
        <button data-testid="btn-compare" onClick={comparer} disabled={loading} className="bg-blue-600 text-white px-3 py-1 text-sm rounded disabled:opacity-50">{loading?"…":"Comparer"}</button>
      </div>
      {error && <div data-testid="compare-error" className="text-sm text-red-600">{error}</div>}
      {result && (
        <div data-testid="compare-result" className="mt-3">
          <div className="text-sm mb-2">Synthèse: <strong>{result.comparaison?.synthese}</strong> — écarts: {result.comparaison?.nb_ecarts}</div>
          <table className="w-full text-xs border">
            <thead><tr className="bg-gray-100"><th className="p-1">Champ</th><th className="p-1">A</th><th className="p-1">B</th><th className="p-1">Δcm</th><th className="p-1">Δ%</th><th className="p-1">Flag</th></tr></thead>
            <tbody>
              {Object.entries(result.comparaison?.delta_json || {}).map(([k, v]: any)=>(
                <tr key={k} className="border-t"><td className="p-1 font-medium">{k}</td><td className="p-1">{String(v.a ?? "—")}</td><td className="p-1">{String(v.b ?? "—")}</td><td className="p-1">{v.delta_cm ?? "—"}</td><td className="p-1">{v.delta_pct != null ? `${v.delta_pct}%` : "—"}</td><td className="p-1">{v.flag}</td></tr>
              ))}
            </tbody>
          </table>
          <pre className="mt-2 bg-gray-50 p-2 text-xs overflow-auto">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

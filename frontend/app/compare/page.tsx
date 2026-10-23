import CompareSails from "@/components/compare-sails"

export const dynamic = "force-dynamic"

export default function Page() {
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Fonction A — Comparaison</h1>
      <CompareSails />
    </div>
  )
}

import ConfiancePanel from "@/components/confiance-panel"

export const dynamic = "force-dynamic"

export default function Page() {
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Fonction B — Confiance</h1>
      <ConfiancePanel />
    </div>
  )
}

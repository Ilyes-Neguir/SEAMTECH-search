import { requireAuthValide } from "@/lib/auth-helpers";
import { redirect } from "next/navigation";
import ConfiancePanel from "@/components/confiance-panel";

export const dynamic = "force-dynamic";

export default async function Page() {
  const auth = await requireAuthValide();
  if (!auth.ok) redirect("/login");
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Fonction B — Confiance</h1>
      <ConfiancePanel />
    </div>
  );
}

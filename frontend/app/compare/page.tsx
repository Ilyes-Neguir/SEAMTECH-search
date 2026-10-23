import { requireAuthValide } from "@/lib/auth-helpers";
import { redirect } from "next/navigation";
import CompareSails from "@/components/compare-sails";

export const dynamic = "force-dynamic";

export default async function Page() {
  const auth = await requireAuthValide();
  if (!auth.ok) redirect("/login");
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Fonction A — Comparaison</h1>
      <CompareSails />
    </div>
  );
}

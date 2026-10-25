import { redirect } from "next/navigation"
import { DemandesApp } from "@/components/demandes-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/approvals")
  return <DemandesApp />
}

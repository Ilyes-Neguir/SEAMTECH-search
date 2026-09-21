import { redirect } from "next/navigation"
import { DossiersApp } from "@/components/dossiers-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/dossiers")
  return <DossiersApp />
}

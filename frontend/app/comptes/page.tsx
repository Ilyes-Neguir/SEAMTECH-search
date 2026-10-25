import { redirect } from "next/navigation"
import { ComptesApp } from "@/components/comptes-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/comptes")
  return <ComptesApp />
}

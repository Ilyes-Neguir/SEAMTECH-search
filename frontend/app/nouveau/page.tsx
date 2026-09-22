import { redirect } from "next/navigation"
import { NouveauApp } from "@/components/nouveau-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/nouveau")
  return <NouveauApp />
}

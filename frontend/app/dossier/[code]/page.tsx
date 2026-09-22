import { redirect } from "next/navigation"
import { FicheApp } from "@/components/fiche-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page({ params }: { params: Promise<{ code: string }> }) {
  if (!(await isAuthenticated())) redirect("/login")
  const { code } = await params
  return <FicheApp code={decodeURIComponent(code)} />
}

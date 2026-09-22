import { redirect } from "next/navigation"
import { GabaritsApp } from "@/components/gabarits-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/gabarits")
  return <GabaritsApp />
}

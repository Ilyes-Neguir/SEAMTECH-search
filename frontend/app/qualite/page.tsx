import { redirect } from "next/navigation"
import { QualiteApp } from "@/components/qualite-app"
import { isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export default async function Page() {
  if (!(await isAuthenticated())) redirect("/login?next=/qualite")
  return <QualiteApp />
}

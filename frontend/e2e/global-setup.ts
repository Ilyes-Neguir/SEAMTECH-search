import { execSync } from "child_process"
import path from "path"

export default async function globalSetup() {
  const repoRoot = path.resolve(__dirname, "../..")
  const configPath = path.resolve(__dirname, "backend.config.json")
  try {
    execSync(`python3 -m seamtech_search index --config "${configPath}" --rebuild`, {
      cwd: repoRoot,
      stdio: "inherit",
    })
  } catch (err) {
    console.warn("Global setup indexing warning:", err)
  }
}

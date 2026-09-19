import type { BootstrapResponse, ProjectResult, SavePayload, Study, StudyStatus, StudySummary } from "./types"

export const LOGIN_URL = "/login?redirect-to=%2Fvehicle-bom"
const PORTAL_PREFIX = "/api/method/alumicraft.bom.portal."
const SERVICE_PREFIX = "/api/method/alumicraft.bom.service."

export class PortalApiError extends Error {
  readonly kind: "auth" | "request" | "validation"
  readonly status: number
  constructor(message: string, kind: PortalApiError["kind"] = "request", status = 0) {
    super(message); this.name = "PortalApiError"; this.kind = kind; this.status = status
  }
}

function csrfToken(): string {
  return typeof document === "undefined" ? "" : document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || ""
}
function messageOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "_server_messages" in body) {
    const raw = (body as { _server_messages?: unknown })._server_messages
    if (typeof raw === "string") {
      try {
        const messages = JSON.parse(raw) as unknown
        if (Array.isArray(messages)) {
          const parsed = messages.map((item) => { try { const value = typeof item === "string" ? JSON.parse(item) : item; return value && typeof value === "object" && "message" in value ? String(value.message) : String(value) } catch { return String(item) } }).filter(Boolean)
          if (parsed.length) return parsed.join(" ")
        }
      } catch { /* Frappe may return a plain message string. */ }
    }
  }
  if (body && typeof body === "object" && "message" in body) {
    const value = (body as { message?: unknown }).message
    if (typeof value === "string") return value
    if (value && typeof value === "object" && "message" in value && typeof value.message === "string") return value.message
  }
  return fallback
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set("Accept", "application/json")
  if ((init.method || "GET").toUpperCase() !== "GET") headers.set("X-Frappe-CSRF-Token", csrfToken() || "None")
  const response = await fetch(url, { ...init, credentials: "same-origin", headers })
  const body = await response.json().catch(() => ({})) as unknown
  if (response.status === 401 || response.status === 403) throw new PortalApiError("Your session or manufacturing access has expired.", "auth", response.status)
  if (!response.ok) throw new PortalApiError(messageOf(body, "The request could not be completed."), "request", response.status)
  return body as T
}
function get<T>(method: string, params: Record<string, string | number | undefined> = {}) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") query.set(key, String(value)) })
  return requestJson<{ message: T }>(`${PORTAL_PREFIX}${method}${query.size ? `?${query}` : ""}`).then((body) => body.message)
}
function post<T>(url: string, payload: unknown) {
  return requestJson<{ message: T }>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then((body) => body.message)
}

export const api = {
  bootstrap: () => get<BootstrapResponse>("bootstrap"),
  listStudies: () => get<StudySummary[]>("list_studies"),
  getStudy: (name: string) => get<Study>("get_study", { name }),
  searchProjects: (company: string, query: string) => get<ProjectResult[]>("search_projects", { company, query }),
  saveStudy: (payload: SavePayload) => post<Study>(`${PORTAL_PREFIX}save_study`, { payload: JSON.stringify(payload) }),
  start: (name: string) => post<{ name: string; status: StudyStatus }>(`${SERVICE_PREFIX}start`, { name }),
  recover: (name: string) => post<{ name: string; status: StudyStatus }>(`${SERVICE_PREFIX}recover`, { name }),
  logout: () => post<unknown>("/api/method/logout", {}),
  exportCsv: async (name: string) => {
    const query = new URLSearchParams({ name })
    const response = await fetch(`${SERVICE_PREFIX}export_csv?${query}`, { credentials: "same-origin", headers: { Accept: "text/csv", "X-Frappe-CSRF-Token": csrfToken() } })
    if (response.status === 401 || response.status === 403) throw new PortalApiError("Your session or manufacturing access has expired.", "auth", response.status)
    if (!response.ok) throw new PortalApiError("The CSV export could not be created.", "request", response.status)
    const blob = await response.blob()
    const href = URL.createObjectURL(blob); const anchor = document.createElement("a")
    anchor.href = href; anchor.download = `${name}.csv`; anchor.click(); URL.revokeObjectURL(href)
  },
}

export function isActiveStatus(status: StudyStatus) { return status === "Queued" || status === "Running" }
export function isEditableStatus(status: StudyStatus) { return status === "Draft" || status === "Needs Review" }
export function isSourceEditable(status: StudyStatus) { return status === "Draft" }

export function toSavePayload(study: Study): SavePayload {
  const materialFields = ["name", "assembly", "item_code", "description", "stock_uom", "proposed_quantity", "unit_cost", "cost_known", "purpose", "review_status", "notes"]
  const laborFields = ["name", "activity_type", "proposed_hours", "hourly_cost", "cost_known", "review_status", "notes"]
  const pick = (row: Record<string, unknown>, fields: string[]) => Object.fromEntries(fields.filter((field) => row[field] !== undefined && row[field] !== null).map((field) => [field, row[field]]))
  return {
    ...(study.name ? { name: study.name, modified: study.modified } : {}), title: study.title.trim(), company: study.company,
    standard_description: study.standard_description.trim(), mode: study.mode, from_date: study.from_date || "", to_date: study.to_date || "",
    projects: study.projects.map((row) => ({ project: row.project, vehicle_count: Number(row.vehicle_count) })),
    materials: study.materials.map((row) => pick(row as unknown as Record<string, unknown>, materialFields)),
    labor: study.labor.map((row) => pick(row as unknown as Record<string, unknown>, laborFields)), material_allowance: Number(study.material_allowance) || 0,
    overhead_allowance: Number(study.overhead_allowance) || 0, target_margin: Number(study.target_margin) || 0,
  }
}

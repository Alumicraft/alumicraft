export type StudyStatus = "Draft" | "Queued" | "Running" | "Needs Review" | "Failed" | string

export interface Company { name: string; currency?: string | null }
export interface ProjectResult { name: string; project_name?: string | null; status?: string | null }
export interface ProjectSelection { name?: string; project: string; vehicle_count: number }

export interface MaterialRow {
  name?: string | null; line_key?: string | null; assembly?: string | null; item_code?: string | null
  description?: string | null; stock_uom?: string | null; proposed_quantity?: number | null
  unit_cost?: number | null; cost_known?: boolean | number | null; amount?: number | null
  purpose?: string | null; confidence?: number | null; review_status?: string | null
  project_count?: number | null; evidence?: string; notes?: string | null; isManual?: boolean
}
export interface LaborRow {
  name?: string | null; activity_type?: string | null; proposed_hours?: number | null
  hourly_cost?: number | null; cost_known?: boolean | number | null; amount?: number | null
  review_status?: string | null; evidence?: string; notes?: string | null; isManual?: boolean
}

export interface Study {
  name: string; modified?: string | null; title: string; company: string; currency?: string | null
  standard_description: string; mode: "Historical only" | "Jev" | string
  from_date?: string | null; to_date?: string | null; status: StudyStatus
  projects: ProjectSelection[]; materials: MaterialRow[]; labor: LaborRow[]
  material_allowance: number; overhead_allowance: number; target_margin: number
  material_total?: number | null; labor_total?: number | null; estimated_total?: number | null
  suggested_retail?: number | null; missing_cost_count?: number | null; pending_review_count?: number | null
  warnings?: string | null; error_message?: string | null
}

export interface StudySummary { name: string; title: string; status: StudyStatus; company?: string; currency?: string; estimated_total?: number | null; modified?: string | null }
export interface BootstrapResponse { user: string; companies: Company[]; assemblies: string[]; purposes: string[] }
export type ApiEnvelope<T> = { message: T }

export interface SavePayload {
  name?: string; modified?: string | null; title: string; company?: string; standard_description?: string
  mode?: string; from_date?: string; to_date?: string; projects?: Array<{ project: string; vehicle_count: number }>
  materials: Array<Record<string, unknown>>; labor: Array<Record<string, unknown>>
  material_allowance: number; overhead_allowance: number; target_margin: number
}

// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest"
import { api, isActiveStatus, PortalApiError, toSavePayload } from "./api"
import type { Study } from "./types"

const study: Study = {
  name: "BOM-STUDY-00001", modified: "2026-09-18 10:00:00", title: "Scout", company: "Alumicraft", currency: "USD",
  standard_description: "Series II", mode: "Historical only", status: "Needs Review", projects: [{ name: "ROW-1", project: "PROJ-1", vehicle_count: 2 }],
  materials: [{ name: "MAT-1", line_key: "line-1", description: "Tube", assembly: "Chassis and fabrication", unit_cost: 4, cost_known: true, notes: "" }],
  labor: [], material_allowance: 10, overhead_allowance: 5, target_margin: 20,
}

beforeEach(() => {
  document.head.innerHTML = '<meta name="csrf-token" content="csrf-test">'
  vi.restoreAllMocks()
})

describe("vehicle BOM API contract", () => {
  it("submits only editable fields, preserves child names, and allows clearing notes", () => {
    const payload = toSavePayload(study)
    expect(payload.projects).toEqual([{ project: "PROJ-1", vehicle_count: 2 }])
    expect(payload.materials).toEqual(expect.arrayContaining([{ name: "MAT-1", assembly: "Chassis and fabrication", description: "Tube", unit_cost: 4, cost_known: true, notes: "" }]))
    expect(payload.materials[0]).not.toHaveProperty("line_key")
    expect(payload).not.toHaveProperty("material_total")
  })

  it("does not silently turn an invalid vehicle count into one", () => {
    const payload = toSavePayload({ ...study, projects: [{ project: "PROJ-1", vehicle_count: 0 }] })
    expect(payload.projects[0].vehicle_count).toBe(0)
  })

  it("sends same-origin credentials and the CSRF header for POST saves", async () => {
    const response = { ok: true, status: 200, json: async () => ({ message: study }) } as Response
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(response)
    await api.saveStudy(toSavePayload(study))
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("save_study"), expect.objectContaining({ credentials: "same-origin", method: "POST", headers: expect.any(Headers) }))
    const options = fetchMock.mock.calls[0][1] as RequestInit
    expect((options.headers as Headers).get("X-Frappe-CSRF-Token")).toBe("csrf-test")
  })

  it("surfaces Frappe server messages and maps auth failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: false, status: 417, json: async () => ({ _server_messages: JSON.stringify([JSON.stringify({ message: "Reload before saving." })]) }) } as Response)
    await expect(api.saveStudy(toSavePayload(study))).rejects.toMatchObject({ message: "Reload before saving." })
    vi.restoreAllMocks()
    vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: false, status: 403, json: async () => ({}) } as Response)
    await expect(api.listStudies()).rejects.toBeInstanceOf(PortalApiError)
    await expect(api.listStudies()).rejects.toMatchObject({ kind: "auth", status: 403 })
  })

  it("recognizes only queued and running as pollable states", () => {
    expect(isActiveStatus("Queued")).toBe(true)
    expect(isActiveStatus("Running")).toBe(true)
    expect(isActiveStatus("Needs Review")).toBe(false)
    expect(isActiveStatus("Draft")).toBe(false)
  })
})

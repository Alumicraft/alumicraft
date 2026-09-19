# Standard vehicle BOM study

## Scope
An installable feature in the existing Alumicraft Frappe app. A Manufacturing Manager selects a company, a standard vehicle description, and representative projects (with the number of vehicles each project produced). A background job snapshots submitted Purchase Invoice lines and Timesheet details and generates a reviewable estimating BOM. Historical purchases are evidence, never proof of installed quantity. No production BOM, price list, invoice, or timesheet is modified. No automatic jobs or provider calls on install/migrate.

## First release
- Native Vehicle BOM Settings and Vehicle BOM Study DocTypes, project/material/labor child tables.
- Read-only, permission-aware ERPNext data adapter; company and project scoping; submitted documents only; signed returns; stock-UOM normalization; company-currency costs.
- Historical-only mode works without a provider. Optional Jev mode batches independent subsystem and purchase-purpose judgments, caches identical decisions, records usage, and makes unresolved decisions reviewable.
- Compare net material quantity per vehicle by project, show the median observed quantity and evidence/conflicts. Never add quantities from different vehicles together as one BOM.
- Native editable review grids and CSV export. Approved/excluded decisions are user review states; no accuracy or 90% completeness claim is computed from the model's confidence.
- Standard cost worksheet: proposed materials plus labor; optional explicit overhead/material allowance and target margin. Historical costs are labeled, not called current quotes.

## Integration contracts
`alumicraft.bom.data.collect_snapshot(company, projects, from_date=None, to_date=None)` accepts project names (list[str]) and returns a JSON-serializable dict. Keys: company, currency, projects, purchase_lines, labor_lines, warnings. Caller handles vehicle counts separately.

Purchase line keys: source_id (Purchase Invoice Item name), invoice, posting_date (ISO), project, supplier, item_code (possibly generic), item_name, description (plain text), item_group, stock_uom, quantity (signed stock units), unit_cost (company currency per stock unit; null if unavailable), amount (signed net company currency), is_return, return_against. Optional flags list. Every source_id is unique. Do not infer a project for an unassigned line except an explicit parent project when available. Do not ship customer names, employee identities, addresses or unrelated accounting fields to Jev.

Labor line keys: source_id (Timesheet Detail name), timesheet, project, activity_type, hours, costing_amount (company currency; null when unavailable), description (plain text). Snapshot excludes employee identifiers. Zero costing amounts are not proof that labor is free.

`alumicraft.bom.jev.classify_rows(rows, standard_description, config, cache=None, on_result=None)` is a pure Python adapter, with no Frappe calls and no SDK dependency. Rows contain id, item_code, item_name, description, item_group, project (opaque ID), optional project_description. Config keys api_key, model, batch_size, concurrency, max_requests, timeout, confidence_threshold. Output: {decisions: {row_id: {assembly, purpose, confidence, review_required}}, cache: {hash: decision}, metrics: {requests, input_tokens, elapsed_seconds}, warnings: []}. `cache` is persisted by caller. `on_result(cache_key, decision)` is called on the coordinating thread after each completed result so caller can checkpoint. Allowed assemblies: Chassis and fabrication, Suspension, Drivetrain, Brakes and steering, Electrical, Cooling and fuel, Interior and body, Wheels and tires, Consumables, Unknown. Allowed purposes: Standard, Custom, Spare, Rework, Unknown. Missing/malformed answers become Unknown or explicit errors, never fabricated certainty. Model classifications are suggestions, not automatic exclusion. Cache key covers normalized input, standard description, model, and prompt version. Validate provider responses, retry transient failures within the max_requests cap, constrain payload size, and never expose credentials or raw provider errors to users.

Root owns DocTypes/controllers/job lifecycle/assembly engine/export/docs. Data subagent owns data.py and focused tests. Jev subagent owns jev.py and focused tests. Tests run offline; actual Frappe install, browser interaction, and provider accuracy remain separate verification gates.

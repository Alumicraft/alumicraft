# Vehicle BOM Study

This feature is shipped inside the existing `alumicraft` app. It uses ERPNext Purchase Invoices, Projects, Items and Timesheets already on the site. It does not sync QuickBooks, create a second database, install a separate web application, or change source documents.

## Install or upgrade

Requires the existing Frappe/ERPNext v16 environment and a working long-queue worker. The app declares `erpnext` as a required app. Deploy the reviewed app revision through the same Frappe Cloud app/deployment process used for the current Alumicraft app. Standard app migration synchronizes the five new DocTypes and standard form script; no console patch or manual custom fields are required.

For a disposable self-managed site with ERPNext already installed:

```sh
bench get-app /path/to/reviewed/alumicraft
bench --site TEST_SITE install-app alumicraft
bench build --app alumicraft
```

If the app is already installed, update its source to the reviewed revision, then run:

```sh
bench --site TEST_SITE migrate
bench build --app alumicraft
bench restart
```

Do not replace an existing app checkout with an unreviewed branch. Take the usual site backup before deployment. Neither installation nor migration runs the pipeline, sends data to a model provider, or changes existing BOMs or prices.

## Standalone workspace and access

After updating the Alumicraft app, open `https://backdesk.drivealumicraft.com/vehicle-bom` directly. This dedicated page runs outside Desk and adds no ERPNext search-bar, navigation or workspace shortcut. The existing native DocTypes remain available to authorized administrators.

The workspace uses the existing ERPNext login session. Guests are redirected to the site's login page with a return URL. Only signed-in System Managers or Manufacturing Managers can open it or call its data endpoints. Study ownership and source-document permissions continue to apply. Self-registration, if enabled elsewhere on the site, does not grant the required roles. This feature does not create users or change role assignments.

The page is uncached, excluded from the sitemap, and marked noindex. Those settings are for privacy and discovery; server-side role and document checks provide access control. Mutations use same-origin session cookies and Frappe CSRF protection. No service API key is exposed in the browser. A stale modified timestamp prevents one browser from overwriting another user's saved review.

Use the dedicated page to create a study, select representative projects, build the draft, review components/labor, save cost changes, and export CSV. The page is hosted by the existing app; it needs no separate hosting service or identity database. The interface uses React and real shadcn/ui components (Radix primitives) installed from the official shadcn registry. Styles belong only to this standalone page; Desk styles and navigation are unchanged.

Frontend source lives in `frontend/`. To change it, run `npm ci`, `npm test`, and `npm run build` from that directory. Vite writes the compiled assets to `alumicraft/public/vehicle_bom/`; include those assets in the app revision. Frappe Cloud can serve the committed assets through the normal app build/update without needing a separate Node server. Python source packages also include the frontend source and lockfile.

## First use in the native form

1. Search Desk for **Vehicle BOM Study** (`/app/vehicle-bom-study`) and create a record.
2. Select the company and describe the standard vehicle configuration. Add representative projects. Enter the number of completed vehicles represented by each project; default one.
3. Leave dates blank for full project history where possible. A date window can omit earlier purchases or returns. Do not mix incomplete builds with complete ones without reviewing that limitation.
4. Choose **Historical only** to test the data extraction without an API key. Save and click **Build Draft**.
5. The background job snapshots submitted, readable source documents. Review the materials and labor tables after the status changes to **Needs Review**.
6. Edit quantities, costs, assembly and purpose. Explicitly approve or exclude each line. Add missing materials/labor with an explanatory note. Generated evidence is preserved; exclude a generated row instead of deleting it.
7. Set material/overhead allowances and a target margin. Save before **Export CSV**. The retail calculation remains unavailable (displayed as 0) until active materials and labor exist and all active lines are reviewed with known costs. This is an arithmetic result at the selected margin, not a completeness certification or suggested commercial policy.

System Managers can manage studies. Manufacturing Managers can manage their own studies and need source read access to Company, Project, Purchase Invoice, Timesheet, Item and Account. Company and project permissions still apply. Exports and resumed runs recheck access to saved source documents. The snapshot contains business purchasing/labor evidence, so use appropriate ERPNext roles for this feature.

## Enable Jev

A System Manager opens **Vehicle BOM Settings** (`/app/vehicle-bom-settings`), enables Jev, stores a TypeSafe API key in the encrypted Password field, and sets the model and batch limits. The default mode on a new study remains Historical only. Save a Jev-mode study and click Build Draft to send its selected purchase descriptions, item metadata, opaque project IDs and standard configuration to TypeSafe. Employee identity columns, invoice costs, customer details and addresses are not included in the classification payload. Free-text descriptions are still user data and can contain sensitive content.

The adapter uses the official HTTPS System One endpoint with two Choice questions per row: assembly and purchase purpose. Exact item identity and calculations remain deterministic. The first release does not invent canonical part numbers or automatically resolve fuzzy part aliases. Generic item codes are grouped only by supplier and normalized description; they remain reviewable. A placeholder labeled `Part` does not collapse all purchases into one component.

Requests run in bounded concurrent batches. Responses are validated and successful decisions cached within the study; cache keys include model, prompt version, standard description and row context. Model versions should be pinned for stable repeatability; `jev-latest` may change underneath an existing cache. Request caps include transient retries and apply per attempt, not per study lifetime. Input token counts and elapsed time describe the completed attempt; they are not a provider billing reconciliation and cannot account for requests whose responses were lost. No automatic LLM fallback incurs extra costs.

If a request fails, remaining decisions stay unresolved. **Resume** uses the saved snapshot and successful cache entries. **Recover Interrupted Run** marks a study failed only after its queue job is no longer active, allowing a subsequent Resume. It does not cancel a healthy worker. To change projects or the standard configuration, create or duplicate a study; generated results are not copied.

## Quantity and cost rules

- Submitted Purchase Invoice lines only; canceled/draft invoices do not contribute. Returns retain negative quantities and amounts.
- Row project takes precedence; an explicit parent project is used only when the row lacks one. Unassigned rows are counted as a gap, not guessed into a vehicle.
- Stock units and company-currency net amounts form the material cost basis. Different UOMs never silently merge.
- Sum each component's signed quantity within each project, divide by the number of vehicles, then take the median positive observed quantity across projects. Missing purchases are not assumed to mean zero consumption. Differences and net returns remain visible.
- Unit price is the latest available positive historical purchase cost. This release does not claim it is a current supplier quote and does not automatically apply Item Price policy.
- Labor uses median observed hours per vehicle by activity. A rate is calculated only when every contributing positive-hour record has a usable cost. Missing labor cost stays unknown instead of diluting the average as free labor.
- AI labels do not automatically remove suspected custom/spare/rework purchases. Every generated positive-quantity material and labor line starts pending review.
- Allowances are explicit. Do not add overhead already included in labor rates. Material tax, freight, stock withdrawals, fabricated subcomponents, shop consumables, setup/development effort and rework may need separate review. Labor and material sources can overlap for subcontracting: exclude the duplicated cost explicitly.

This produces an estimating BOM for a standard vehicle, not a released manufacturing BOM. The system does not report “90% complete” because purchasing data cannot establish the denominator of missing physical components. Approvals stay within the study in this release; there is no cross-study approved assembly library or automatic ERPNext BOM publication yet.

## Verification

Offline checks cover accounting/quantity edge cases, permission-aware extraction, Jev payload/response handling and retry limits, saved-state protection, CSV escaping, native form/package structure, standalone page authentication, portal payload limits, and React save/navigation behavior. Tests use synthetic data and mocked HTTP; they do not call TypeSafe.

```sh
python -m pytest -q tests
node --test tests/js/*.test.js
python -m compileall -q alumicraft
(cd frontend && npm ci && npm test && npm run build)
```

The standalone page has also been checked in a browser against a synthetic Class 10 fixture, including editing quantities, the unsaved-change dialog, saved cost refresh, and escaped source descriptions. This fixture does not exercise a live Frappe session or production project data.

Before deployment is accepted, verify on a disposable Frappe site: app install/migrate; Settings password persistence; native Study create/save; queue processing; a synthetic two-project fixture with a return and foreign-currency labor; material review/save/export; worker recovery; manager/owner/company permission boundaries; duplicate-study reset. Then run a small labeled real-data Jev sample and compare accuracy, review time, throughput and cost against a low-cost LLM before processing all history. Offline tests do not prove site installation, live provider behavior, or BOM coverage.

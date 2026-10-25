# Decision Workspace: using the four screens

Scope ends at physical handoff to a third-party carrier. It does not track customer
delivery, claim a calibrated probability of lateness or optimize buying cost.

## 1. Cần xử lý

Choose the warehouse and planning horizon, then update the plan. The five zones
show summary metrics, priority exceptions, the daily supply/shortage timeline,
conditional proposal entry and links to assigned work/outcomes. The daily peak
physical deficit can be positive even with enough inventory at day end. Missing
optional sources are unknown, not zero. Numbers and source/cutoff explanations
refer to the same applied context; changing filters requires applying again.

## 2. Chi tiết & phương án

Choose a product, compare quantity and arrival time, and read the preparation and
carrier conditions. Save the comparison, then select a named order to create its
dossier. A saved proposal is not a receipt or purchase order. Recompute after
editing assumptions; stale results cannot be saved/exported as the edited plan.
Existing forecasting, history and order/calendar utilities remain in the secondary
navigation. Dossiers are also available under Hồ sơ quyết định.

## 3. Việc của tôi

This queue reads actual Flowable tasks and displays only those assigned to the
current person. Open the task to view its exact dossier revision and evidence.
Warehouse and Logistics confirm in parallel. Only the independent manager can
approve after both confirmations. Returning/rejecting requires a reason. A failed
or partial queue read warns explicitly; an empty partial result does not prove
there are no outstanding tasks. There is no admin override of business assignment.

Use Hiệu lực phương án to see current checks and structured before/after changes.
Harmless changes retain the revision; changed obligations require recomputation,
a new immutable revision and verification/approval again. Native operations that
already happened remain in the recorded history. See DECISION-REVALIDATION.md.

## 4. Theo dõi kết quả

Follow submitted dossiers, assigned roles, revision history and recorded owner
steps. An approved proposal is still open. After the owners have actually recorded
carrier handoff, use Kiểm tra bàn giao thực tế. The system closes the dossier only
after matching all original line quantities with owner evidence and the approved
Flowable instance. A carrier booking alone cannot close it. Stop/return is also a
visible outcome, not a hidden deletion or invented rollback.

## Ask the dossier, including without an LLM

The four prompts explain evidence, compare scenarios, identify pending tasks and
describe verified handoff status. A bounded Vietnamese intent resolver recognizes
supported questions; ambiguous/unsupported requests ask the user to choose. It
does not interpret SQL/code or execute commands.

The implemented endpoint is POST `/decisions/{case_id}/assistant`, with the current
revision UUID and one of `evidence`, `comparison`, `tasks`, `outcome` (or a question).
It returns the existing Portal RunResult contract: answer/datasets/tables/charts/
sources/warnings/questions/error. The existing renderer handles its chart/table
and CSV presentation. The dataset distinguishes the selected SKU's warehouse-wide
physical deficit from that order's individual lines and units. No LLM computes
KPIs or fabricates rows, source IDs, chart expressions or probability claims.

RunResult `completed` means the answer was built; it is independent of the dossier's
physical completion status. Saved scenario data is labeled with its original cutoff,
and old metric versions require recomputation. Every explanation rechecks current
case assignment/source permissions and exact revision. A 401/403 clears retained
answer/queue/detail data in the UI. Source/engine failure never reuses an old answer.

`contracts/decision-assistant.tools.json` documents the bounded read ports for a
future caller-authorized Agent/Langflow adapter. It does not register privileged
writer tools or modify any existing Langflow node. Reads may refresh a local status
projection; they never approve or write owner business records.

## Setup and acceptance boundaries

Operational assignments and the trusted workflow client must be configured as in
DECISION-WORKFLOW.md. Missing configuration disables those capabilities; analytics
remains available. Native operation/service setup is documented separately.

M11 validation includes canonical JSON Schema and reference/unit checks, caller
and revision denial, mine-only queue filtering, partial-engine warnings and an
actual PostgreSQL/Flowable integration bridge. Browser tests use mocked services,
including renderer reuse, source-revoke clearing and mobile width. This does not
replace final real four-person Keycloak MFA/Hasura/owner API/Portal acceptance.

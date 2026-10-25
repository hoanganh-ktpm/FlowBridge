# Decision workflow (M08)

The new BPMN template is `workflows/supply_handover_exception.bpmn20.xml`.
It is separate from the previous order-fulfillment template, whose delivery
semantics remain unchanged.

Each submitted revision starts a separate Flowable instance. Warehouse and
Logistics verify in parallel. Both must confirm before the manager can review.
The manager approves, requests changes, or rejects with a reason. Requests for
changes end that revision's process; a new immutable revision must be submitted.
Approval waits for `decision.carrier_handed_over`, never a customer-delivery event.
The source-proof correlation/actual business commands belong to M09; M08 does not
expose a button or user API to emit that completion message.

Non-interrupting 24-hour timers create coordinator reminders; they do not confirm
or approve anything. Terminal main branches terminate remaining reminders.
Tests shorten the timer only in an isolated deployment of the test template.

## Identity and configuration

The existing human SSO/MFA behavior is unchanged. Workspace authenticates each
human request through Core, resolves their recorded case assignment, and checks
their current source grants. It uses a separate machine identity for Core
workflow commands; it does not save or reuse a human token as a worker identity.

Workspace configuration (local secret storage only):

```text
FLOWBRIDGE_WORKSPACE_WORKFLOW_TOKEN_URL=<trusted issuer token endpoint>
FLOWBRIDGE_WORKSPACE_WORKFLOW_CLIENT_ID=<confidential service client>
FLOWBRIDGE_WORKSPACE_WORKFLOW_CLIENT_SECRET=<secret>
FLOWBRIDGE_WORKSPACE_WORKFLOW_CA_FILE=<trusted issuer CA, if private CA>
FLOWBRIDGE_WORKSPACE_DECISION_BINDINGS=<assignment JSON from DECISION-DOSSIERS.md>
```

The OAuth client uses client_credentials; Workspace renews short-lived tokens.
Core must recognize its service client/principal and grant it the scoped Sales
order source columns needed for order validation. It does not need PLATFORM_ADMIN.
TLS verification stays enabled; HTTP service origins are restricted to loopback.

Core configuration additionally requires:

```text
FLOWBRIDGE_WORKFLOW_ENABLED=true
FLOWBRIDGE_WORKFLOW_ALLOWED_WORKFLOWS=["supply_handover_exception"]
FLOWBRIDGE_WORKFLOW_DECISION_EXECUTION_PRINCIPAL=<service Core principal UUID>
FLOWBRIDGE_WORKFLOW_DECISION_BINDINGS=<same authoritative operational assignments>
FLOWBRIDGE_FLOWABLE_BASE_URL=<protected engine REST service origin>
FLOWBRIDGE_FLOWABLE_USERNAME=<engine service user>
FLOWBRIDGE_FLOWABLE_PASSWORD=<engine service secret>
```

Deploy the template through the existing Core admin publish route. Do not expose
engine credentials to Portal. The decision execution principal is resolved through
Core at command time; the new workflow does not require the old static
FLOWBRIDGE_WORKFLOW_ENGINE_TOKEN. Existing generic workflows retain their config.

Initial tasks are directly assigned: the server selects the first sorted eligible
principal per role from the recorded roster. The manager is distinct from both
proposer and verifiers. A user may read an assigned case but cannot complete another
person's task. There is no group claim or user-driven reassignment in this version.
Revoking assignments blocks commands; it does not silently transfer engine tasks.
Unresolved ownership requires explicit operational intervention rather than an
admin bypass or fabricated approval. Directory-backed reassignment is a later
capability, not implied by editing the configuration.

## Decision checks and durable boundaries

Drafts created with workflow enabled capture a validation baseline. Old M07 drafts
must receive a new scenario revision before submission. Source runs must use the
current operational digest and a forecast history digest.

Before submission, confirmation or approval, Workspace bypasses its SnapshotCache
and re-reads current governed sources: historical demand, scoped stock, inbound,
open orders, commitments, preparation policy, order schedule and relevant carrier
capacity. Source grants are checked again. Reordered rows and another warehouse
do not change the operational fingerprint. Missing policy/schedule/capacity never
become zero or a successful condition. Approval requires confirmed policy,
projected stock readiness, a timely carrier slot, preparation time before that
slot, and published capacity. Planned added supply remains a verified assumption,
not a physical receipt.

Changed facts/history block confirming/approving the old revision. A currently
authorized user can still return/reject an obsolete proposal with a reason; that
command explicitly carries a STALE condition status and never asserts validity.
These checks are deliberately conservative. Harmless-change classification,
automatic event-driven invalidation and impact diffs are M10.

Workspace persists a submission intent before calling Core, freezing the revision.
Core binds the instance to case/revision, scope, participants and input digest.
Lost starts are looked up using the engine's internal marker; an uncertain intent
with no proven instance is never blindly dispatched again. Lost task completion
responses are reconciled with that task's engine history. Successful task receipts
and reasons are appended to the case timeline. Workflow progress remains an engine
projection; older observations cannot move the same revision backwards.

The Portal can retry a lost command with the same key or reconcile a pending start
after reopening the case when sources remain unchanged. If sources change during
an unresolved start, it stays blocked for explicit reconciliation; automatic
cancellation/recovery for that race is part of M10, not a silent restart.

Core's machine-only decision command port uses `/api/v1/workflows/decision-runs/`.
The generic human task-completion endpoint explicitly refuses this template, so
it cannot bypass freshness/role checks. Humans use these Workspace endpoints:

- `POST /decisions/{id}/submit`: current revision and expected case version.
- `GET /decisions/{id}/workflow`: current engine tasks/status.
- `POST /decisions/{id}/tasks/{task}/complete`: outcome and optional note; a reason
  is mandatory for changes_required/rejected. Both POSTs need Idempotency-Key.

## Verification and limits

```powershell
.\.venv\Scripts\python.exe -m scripts.decision_workflow_test_environment
```

This creates disposable, labeled PostgreSQL and pinned Flowable 7.2.0 containers
on a separate network with loopback ports, generates test-only credentials, and
cleans up only matching owned resources. No deployed .env/Supabase target is read.
The runner saves secret-free results under ignored `.tmp/decision-tests/`.

Tests cover actual parallel tasks, all manager branches, persisted timers/tasks
after restart, protected engine REST, plus Workspace HTTP/PostgreSQL joined to
Core workflow logic and the actual engine. The bridge test mocks IAM/source API
boundaries; it verifies lost-response recovery, wrong-role refusal, stale approval
and reasoned return without business writes. Separate Core HTTP tests exercise
the machine gate and generic-endpoint bypass guard. Browser tests use mocked APIs.

These results do not constitute a complete live four-human MFA/Hasura/Portal run.
M09 adds owner transactions and physical handoff evidence; M10 adds impact events
and broader recovery; M12 performs the complete acceptance run. No hosted database
or production assignment has been changed by this milestone.

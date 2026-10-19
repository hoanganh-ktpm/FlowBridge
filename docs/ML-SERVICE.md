# Supplier Assistant ML API

Code: GPL-3.0-or-later, see LICENSE. This module implements the two initial uses:
supplier recommendations and persisted risk for an issued purchase order. It runs
inside the existing backend, using its PostgreSQL repository and IdP/role policy.
It does not start a second microservice, issue POs, approve proposals or send alerts.

## Install and prepare artifacts

From the repository root, Python 3.12 and the pinned uv version:

```powershell
uv sync --locked --extra ml
uv run --locked --extra ml python -m flowbridge.supplier_assistant.prediction.cli download-checkpoint
```

The download command explicitly fetches `jingang/TabICL`'s
`tabicl-classifier-v2-20260212.ckpt` and prints its SHA-256. Set
`FLOWBRIDGE_ML_CHECKPOINT_SHA256` to the reviewed digest. Startup and HTTP requests
never download model weights. Weights, context and dependency binaries remain in
ignored cache directories and are not included in source/wheel artifacts.

For a development smoke test only:

```powershell
uv run --locked --extra ml python -m flowbridge.supplier_assistant.prediction.cli demo-context
uv run --locked --extra ml python -m flowbridge.supplier_assistant.prediction.cli validate-context .cache/ml/context.json
uv run --locked --extra ml python -m flowbridge.supplier_assistant.prediction.cli smoke
```

`demo-context` creates a deterministic synthetic context, refuses to overwrite an
existing file, and does not read/write the database. It does not reuse the older
simulator's defective feature snapshots. Predictions with a synthetic context are
marked with a warning; production rejects that context. A smoke pass proves actual
TabICL inference works, not model quality, calibration or suitability for real orders.

For business use, provide a reviewed context made from actual, correctly reconstructed
historical snapshots. Do not pass `demo_output/model_context.csv` directly: it mixes
partitions and contains features from the earlier simulator. Keep context, calibration
and evaluation groups separate by physical PO item and time.

## Run with the existing database and Identity configuration

Keep the current `.env` database credentials. Add the ML settings from `.env.example`
or set them in PowerShell, then start the existing application:

```powershell
$env:FLOWBRIDGE_ML_ENABLED = 'true'
$env:FLOWBRIDGE_DOCS_ENABLED = 'true'
uv run --locked --extra ml python -m flowbridge.main
```

Swagger: `http://127.0.0.1:8000/docs`. Model readiness:
`GET /_ops/ml/ready`. General `/_ops/ready` only checks the backend/database.
Missing artifacts/dependencies yield `MODEL_UNAVAILABLE`; the app remains available
for its other APIs. Fix the artifact/configuration and restart to reload the model.
One process owns one fitted context/model. Start with one server worker on CPU/GPU.

Every ML business endpoint requires an introspected bearer token and an active
`users` mapping for the exact issuer/subject, plus current grants. Configure the
five OIDC settings as described in [SUPABASE-API.md](SUPABASE-API.md).
No local identity bypass, arbitrary role header or API key is introduced here.
Scenario/ranking allow EMPLOYEE, scoped APPROVER/WAREHOUSE or AI_SERVICE. PO writes
require AI_SERVICE; PO/prediction reads also enforce ownership/department/service scope.
The database backend is currently one application database, not a multi-tenant service.

## Endpoints

| Method/path | Purpose | Writes |
|---|---|---|
| `POST /api/v1/ml/scenarios/predict` | Score a supplier/product/quantity/deadline | No |
| `POST /api/v1/ml/suppliers/rank` | Score eligible suppliers in one batch, return top-k | No |
| `POST /internal/v1/ml/purchase-orders/{order_id}/predict` | Score all open items and persist snapshots | Yes, AI_SERVICE |
| `GET /api/v1/ml/purchase-orders/{order_id}/risk` | Read current usable item scores and their maximum | No |
| `GET /api/v1/ml/predictions/{prediction_id}` | Read a persisted prediction's result/provenance | No |

These routes are additive. The legacy `/internal/v1/tools/predict-risk` and
`compare-suppliers` contracts accept a different payload and remain unconnected;
point new custom nodes at `/api/v1/ml/...`. Export Gateway routes with
`python -m flowbridge.gateway --include-ml`. Block `/internal/*` externally.

Scenario request:

```json
{
  "product_id": "YOUR_PRODUCT_UUID",
  "supplier_id": "YOUR_SUPPLIER_UUID",
  "quantity": "100",
  "requested_delivery_date": "2026-12-01"
}
```

Ranking request:

```json
{
  "product_id": "YOUR_PRODUCT_UUID",
  "quantity": "100",
  "requested_delivery_date": "2026-12-01",
  "top_k": 3,
  "ranking_policy": "risk_first",
  "currency": "VND",
  "max_unit_price": "18000000"
}
```

Replace UUIDs/date with actual records and a valid business date. Quantity/price are
decimal strings with at most four fractional digits. Deadline uses Asia/Ho_Chi_Minh;
timestamps use UTC. Clients cannot submit features, stage, user/role or feature_asof.
The Agent must obtain the desired delivery date before asking for lateness risk.

```powershell
$headers = @{ Authorization = "Bearer $accessToken" }
$body = Get-Content examples/ml/rank-request.json -Raw
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/ml/suppliers/rank `
  -Headers $headers -ContentType application/json -Body $body
```

Ranking returns `candidates` (scored top-k), `unscored`, `excluded`, `considered_count`,
warnings and a trace. Each candidate includes supplier/name/price/currency, quoted
lead time and a `result` with risk_score, threshold, sample count and warnings.
`risk_first` sorts ascending score, then supplier UUID; it never compares prices
across currencies. A currency filter selects matching quotes without FX conversion.
MOQ, active status, availability and optional price cap are hard filters. Availability
is not confirmation that the supplier can supply the full quantity. All high-risk
candidates produce an explicit warning. Unknown risk never becomes zero. Exceeding
the candidate/history cap fails explicitly instead of ranking a silently truncated set.

## PO scoring, persistence and representative risk

The internal POST takes an order UUID in the path and **no body**. Supply one
`Idempotency-Key` (1..160 allowed identifier characters) generated by the backend job.
It returns HTTP 200 after synchronous inference and transaction commit, not a queued
202. Worker retry uses the same key; rescoring after new data uses a new key.

```powershell
$workerHeaders = @{
  Authorization = "Bearer $serviceAccessToken"
  'Idempotency-Key' = 'po-issued-YOUR_EVENT_ID'
}
Invoke-RestMethod -Method Post -Headers $workerHeaders `
  -Uri "http://127.0.0.1:8000/internal/v1/ml/purchase-orders/$orderId/predict"
```

The service derives supplier, product, original Q/unit/deadline and PR lineage from
the PO. It writes `risk_predictions` and the durable retry response atomically in the
existing `api_idempotency` table. No migration is needed beyond baseline 0001/0002.
No proposal/report is created. Snapshots are immutable; `task_id` is null because this
endpoint performs synchronous scoring, not a durable analysis task. A model/DB failure
rolls back the request. Item-level insufficient data is explicit in the saved retry
response; no successful prediction row or substitute score is fabricated for that item.

The result contains:

- `aggregation_policy=remaining_value_weighted_v2` for newly computed responses;
- `representative_risk_score = sum(value_i * score_i) / sum(value_i)`, where
  `value_i = max(ordered_quantity - accepted_received_quantity, 0) * unit_price`;
  all values use the PO's contract currency. Do not add unlike product quantities;
- a full-order score requires all open item scores and value weights. Otherwise it
  is null; `observed_value_weighted_score` describes only the scored portion and
  `scored_value_coverage` gives its share of total outstanding value;
- `highest_item_risk_score`, `highest_risk_item_id` and its original deadline remain
  separate. `HIGH_RISK_ITEM_PRESENT` preserves a warning even if the weighted mean
  is low. Item thresholds are not silently reused to classify the weighted mean;
- `items` include remaining value/currency and prediction trace. Unknown prices,
  mixed currencies or zero total value produce no substitute order score;
- old idempotent responses explicitly marked `max_open_item_v1` retain their original
  MAX meaning. Consumers must read the returned aggregation policy.

This is an exposure-weighted monitoring index, not an estimated probability of any
late item or a calibrated expected financial loss. It favors high-value outstanding
exposure; it does not measure operational criticality of a cheap essential item.
The worst-item warning therefore remains mandatory. There is no arbitrary 70/30
blend and no fitted order-level model or extra inference call.

For example, scores 0.9 and 0.1 with outstanding values 100 and 900 give 0.18,
while the highest-item score stays 0.9. Splitting one equivalent exposure into two
lines leaves the weighted index unchanged. Missing scores are never replaced by zero.

A noisy-OR `1 - product(1-p_i)` would require justified independence and meaningful
item probabilities; common supplier/logistics causes make that assumption unsafe
here. See [NIST series model](https://www.itl.nist.gov/div898/handbook/apr/section1/apr182.htm)
and [probability calibration](https://scikit-learn.org/stable/modules/calibration.html).
Weight choice changes the purpose and compensability of a composite index; see the
[OECD handbook](https://www.oecd.org/en/publications/handbook-on-constructing-composite-indicators_533411815016.html).
Use a grouped, time-split order-outcome evaluation before claiming a better predictor.

Completed/cancelled items and delivered/cancelled/rejected orders do not contribute.
This is a representative score, not the probability of the union of all late items.
An order with no outstanding items has `no_open_items` and null risk. The GET reads
the latest snapshot per item matching model/schema/preprocessing/context/feature policy;
it marks snapshots older than ML_PREDICTION_MAX_AGE_SECONDS as unavailable. Reading a
prediction by UUID returns its historical result even if it is old.

All scores remain uncalibrated: `score_is_calibrated=false` and
`calibrated_late_probability=null`. A threshold of .70 means an ML score threshold.
Do not describe it as a validated 70% business probability. Model/context evaluation,
calibration and cost-based alert threshold selection are still required.

### Inspecting the feature-history cohort

`hist_sample_count` counts completed PO **item rows** for the same supplier,
product and unit, with matching category, within the issue-date history window.
It is not the number of all TabICL in-context examples, all supplier orders, or
an explanation of which input caused a high score. Category-level smoothing,
quantity references and the model context are separate inputs.

New successful scores include `result.history_reference`, also kept inside the
immutable feature snapshot metadata for persisted predictions. This contains the
cutoff, window, cohort policy, identities, count and a deterministic fingerprint
of the selected item outcomes. It does not expose the training context or a full
feature snapshot. Feature generation and the inspection endpoint share the same
cohort selector so that adding a tool does not silently change the 19 model inputs.

`POST /api/v1/ml/history` reads the cohort without inference or writes. It requires
the human `PROCUREMENT_VIEWER` database grant for company-wide order detail. Use
product/supplier UUIDs and the original reference. Results include order/item IDs,
order numbers, quantities, original deadlines, physical completion dates, the date
completion was known and late days. Item row count can differ from distinct orders.
The endpoint preserves a timezone-aware cutoff and ignores receipts recorded later.
Results are bounded and paginated; read `total_matches` and `next_page`.

`reference_matches=true` means the reconstructed cohort matches the supplied original
reference, not that the reference is a signed or persisted prediction. Source edits
can yield `status=changed` even when the count is unchanged. An old score without a
reference may supply `feature_asof` and `expected_count` to reconstruct the cohort;
matching a count alone never verifies the original membership. With neither reference
nor cutoff, it reads current history and explicitly warns that this is not necessarily
the previous prediction's cohort. Current cancellation status is not historized and
product labels come from the current catalog; both limitations stay in warnings.

Langflow exposes this through Data task `history_samples`, not RAG, with backend
receipts going to both Agent and frontend JSON. The tool resolves names/codes and
passes canonical UUIDs itself; it does not ask the user to provide UUIDs or rescore
the purchase to get history. Existing graph objects and connections remain unchanged.

For the later rule, check score and deadline on the same open item. If any item has
score > .70 and 0..3 days to its deadline, it can qualify even when another item
has a higher score but a later deadline. A timer/scheduled worker must re-evaluate
time conditions even without database changes. No alert scheduler is implemented here.

## Feature/context contract

`schemas.Features` is the canonical ordered 19-column contract. A uses PR_CREATED
for a hypothetical pre-issue scenario; B uses PO_MONITORING with original Q unchanged.
Only numeric null becomes NaN; categorical columns remain categorical. The model
receives neither text/RAG, IDs used only for audit, labels nor previous risk scores.

- q_ref: median issued quantity for the same product/unit during the configured
  history window, excluding the current PO item; otherwise an operator-provided
  positive quantity reference in the context manifest. Missing reference blocks scoring.
- History: completed supplier/product/unit PO items from the preceding 180 days by
  issue date, using accepted receipts whose knowledge time and actual date are <= cutoff.
  Completion is the first actual date accepted cumulative quantity reaches original Q.
- Lead P50/P90: linear interpolated quantiles from issue to completion (calendar days).
  No samples => null quantiles; never fake zero-day delivery.
- Late rate: `(k + 12 * category_pool_rate) / (n + 12)`; pool uses known outcomes only.
  No pool => null. Pending/cancelled outcomes are not labelled on time.
- Backlog: observed other open items for that supplier, across products and outside
  the history window, excluding the item being scored.
- No trusted calendar/issue-delay estimator is currently configured: those values
  remain null. Monitoring issue wait is always null, never 0.
- Receipt reporting coverage defaults to unconfirmed. received_fraction and
  remaining_size_ratio stay null unless coverage is explicitly confirmed by operations.
- Promises must cover the remaining quantity of every unfinished schedule. Use the
  latest known revision per schedule and the latest resulting completion promise.
- Current endpoints score current data only. They do not expose historical replay:
  mutable status/PR fields require retained event/version history for correct replay.

Context JSON envelope (each x must explicitly contain all 19 keys):

```text
feature_schema_version: tabicl_supplier_fixed_v1
preprocessing_version: supplier-features-v1
feature_names: ordered list from FEATURE_NAMES
context_dataset_version: reviewed immutable dataset version
source_kind: observed | synthetic
provenance: origin / construction / partition description
label_cutoff: aware timestamp
quantity_references: {product UUID: {unit: string, quantity: decimal string}}
rows: [{group_id, feature_asof, label_known_at, x, y}]
```

Loader requires both classes, feature_asof < label_known_at <= label_cutoff, coherent
labels within each physical group, valid feature stages/domains and no extra feature.
PO inference rejects a physical PO already in context. Dataset hash, checkpoint hash,
cutoff and source kind are saved with predictions. The context is loaded once at startup;
inference is batched and runs off the async event loop with serialized model access.

## Tests and dependency provenance

```powershell
uv run --locked python -m pytest tests/unit -q
# Requires a configured test DB, applied 0001/0002 and the explicit model/context artifacts:
uv run --locked --extra ml python -m pytest tests/integration/test_prediction_database.py -m integration -q
```

The ML integration test uses the existing isolated rollback fixture. Its Identity
and workflow approval are injected; SQL, TabICL and prediction persistence are real.
Unit tests use controlled dependencies and are separate evidence.

TabICL 2.2.0: [official source/license](https://github.com/soda-inria/tabicl), BSD-3-Clause,
Python >=3.10; pandas 2.3.3: [upstream](https://github.com/pandas-dev/pandas/tree/v2.3.3),
BSD-3-Clause. Their licenses are compatible with this project's GPL-3.0-or-later.
Checkpoint: [jingang/TabICL](https://huggingface.co/jingang/TabICL), BSD-3-Clause.
Keep upstream license/attribution when distributing dependencies or weights separately.
The project license does not grant rights over business data/context supplied by users.

## Langflow custom node

`langflow/components/supplier_ml/rank_suppliers.py` supplies a read-only ranking
component using Langflow's supported `lfx` API. Paste its source into a Custom Component,
or register `langflow/components` with LANGFLOW_COMPONENTS_PATH. Set API origin and
bearer credential in trusted flow configuration; only product/quantity/deadline/top-k
are exposed as Agent tool inputs. Do not expose the internal PO write endpoint as a tool.
The token must be issued by the configured IdP; user-sensitive operations must receive
the actual user's delegated credential, not a broadly privileged shared token.

For Python custom nodes, the packaged `prediction.client.SupplierMLClient` wraps the
same endpoints and typed DTOs. Reuse an AsyncClient owned by the node/integration and
refresh credentials there. Neither client logs bearer tokens or follows redirects.

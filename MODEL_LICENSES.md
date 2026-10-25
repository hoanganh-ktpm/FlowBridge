# Forecast methods and model provenance

## Active pretrained model: Chronos-2

Operational Portal forecasts, planning and new API defaults use `chronos2`.
The `auto` provider also resolves to Chronos-2. Explicit `ridge_lag` and
`seasonal_mean` remain available for compatibility and comparison. Saved runs
retain their actual method. New requests default to no statistical fallback.

| Item | Provisioned artifact |
|---|---|
| Model | `amazon/chronos-2`, official pretrained weights |
| Immutable model revision | `29ec3766d36d6f73f0696f85560a422f50e8498c` |
| Provider | `chronos-forecasting==2.3.2` |
| Runtime | PyTorch `2.8.0+cpu` on Windows/Linux, Transformers `4.57.6`, pandas `2.3.3` |
| Code license | [Apache-2.0](https://github.com/amazon-science/chronos-forecasting/blob/main/LICENSE) |
| Weights license declaration | Apache-2.0 in the [pinned official model card](https://huggingface.co/amazon/chronos-2/blob/29ec3766d36d6f73f0696f85560a422f50e8498c/README.md) |
| Use | Local CPU zero-shot inference, no local training/fine-tuning, no business-data upload |

Install the optional forecasting dependency group with
`uv sync --locked --group dev --group database --group forecasting`.
Run `python -m scripts.provision_chronos` once to download the pinned official
artifact into ignored `.cache/models/chronos-2/<revision>`. The script verifies
the weights against the official LFS SHA256 and records SHA256 values for every
provisioned file in `model_provenance.json`. Weights are not shipped in source.

The model repository declares Apache-2.0 in README metadata and does not contain
a separate LICENSE file at this revision. The provisioner preserves that model
card and bundles the [canonical Apache-2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt)
as LICENSE, recording its actual source separately. It does not claim that the
license text was fetched from the model repository. Preserve these files and
applicable upstream notices when redistributing an artifact.

The local CLI loads the generated `.tmp/data-workspace/chronos.env` when serving
Workspace. Other deployments explicitly set `FLOWBRIDGE_WORKSPACE_CHRONOS_PATH`
and `FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION`. Runtime verifies the manifest and
hashes before loading, uses `local_files_only=True` and reuses one CPU instance.
Requests never download or fine-tune a model. Unavailable/incompatible models
produce `503 CHRONOS_UNAVAILABLE` on the active UI paths.

P10/P50/P90 are native model quantiles, clipped at zero for nonnegative series.
They are not calibrated service-level guarantees for this enterprise. Two
chronological historical blocks evaluate P50 MAE/WAPE when enough context exists.
No validation score is invented for shorter series. The held-out values are
excluded from each inference context. Evaluation does not update weights.

Real local inference was verified on 25/10/2026, including calendar gaps and
planning to saved scenario comparison using fixture Core/repository boundaries.
The API evidence therefore verifies real model execution, not live SSO/Core/
Hasura/PostgreSQL integration or accuracy gains on enterprise data. Safe local
evidence is in `.tmp/data-workspace/chronos-real-inference.json` and
`.tmp/data-workspace/chronos-planning-inference.json` (ignored).

## Legacy Ridge and statistical methods, version 2.0.0

`ridge_lag` and the statistical baseline are FlowBridge-authored code under
GPL-3.0-or-later, implemented with Python's standard library. No new runtime library,
pretrained weights or external inference endpoint is used. Ridge inference has been
run on the authorized local data; this is separate from the optional Chronos provider.

`ridge_lag` fits a supervised L2-regularized regression from the caller's authorized
series: lags 1/7/14, mean of the last 7 values, a time index and weekday indicators.
Features and target are centered, features scaled, intercept unpenalized, lambda=1.
The fitted context follows the requested lookback and future lags are recursively predicted.
The implementation is original; the standard objective is documented in
[Ridge regression](https://scikit-learn.org/stable/modules/linear_model.html#ridge-regression-and-classification).
scikit-learn itself is not installed or redistributed by this feature.

The legacy selector compares two chronological, unseen blocks of the requested horizon. Both
methods use the same source-to-origin gap as the real request. ML is eligible only
with at least `56 + 2*horizon + gap` complete daily periods and is selected only if
its MAE is over 5% lower. Otherwise statistics remain active. That 5% is an explicit
complexity-control policy, not a statistical-significance claim. See the primary
[forecast evaluation reference](https://otexts.com/fpp3/toolbox.html#time-series-cross-validation).

Runs store window dates, MAE, WAPE, selection reason, context count and training
input SHA256. Scores assess base predictions before the final residual median bias
correction. The same validation blocks inform selection and residual quantiles;
they are not an independent final test set and do not establish calibrated coverage.
Where validation is available, empirical residual P10/P50/P90 are added to the
forecast base. Otherwise the existing statistical residual estimate is used.

The displayed interval is pointwise and empirical. Summing daily P90 values or
using them in planning does not establish a 90% service probability for the horizon.
The UI labels P90 as a cautious planning assumption. No Monte Carlo or calibrated
stockout probability is claimed.

Operational UI forecasts resolve day zero when the forecast POST is received in
the configured business timezone, not when the report was opened. Fourteen days
include that day and end 13 calendar days later. Saved historical runs retain their
original dates. Optional training lookback is 90/180/365 days and is clamped to the
source's earliest actual selected observation; unknown earlier history is not padded
to make ML eligible. Charts can show a shorter reporting period independently.

## Statistical baseline

`seasonal_mean` version `1.1.0` is a FlowBridge-authored statistical implementation
under GPL-3.0-or-later. It uses Python's standard library, not pretrained weights.
Daily forecasts average matching weekdays in the last 56 observed periods. Weekly
and monthly forecasts average recent periods. Empirical residual quantiles are added
to that base for ordered P10/P50/P90 estimates, not calibrated probabilities. Runs
retain their own method version (earlier 1.0.0 runs used the mean as the central point).
No model artifact is downloaded or redistributed.

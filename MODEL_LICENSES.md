# Forecast methods and model provenance

## Active forecast selection, version 2.0.0

`auto`, `ridge_lag` and the statistical baseline are FlowBridge-authored code under
GPL-3.0-or-later, implemented with Python's standard library. No new runtime library,
pretrained weights or external inference endpoint is used. Ridge inference has been
run on the authorized local data; this is separate from the optional Chronos provider.

`ridge_lag` fits a supervised L2-regularized regression from the caller's authorized
series: lags 1/7/14, mean of the last 7 values, a time index and weekday indicators.
Features and target are centered, features scaled, intercept unpenalized, lambda=1.
The fitted context is bounded to 180 days and future lags are recursively predicted.
The implementation is original; the standard objective is documented in
[Ridge regression](https://scikit-learn.org/stable/modules/linear_model.html#ridge-regression-and-classification).
scikit-learn itself is not installed or redistributed by this feature.

Auto compares two chronological, unseen blocks of the requested horizon. Both
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

## Optional Chronos-2 provider

| Item | Recorded status |
|---|---|
| Model | `amazon/chronos-2` |
| Official model card | [Amazon model card](https://huggingface.co/amazon/chronos-2) |
| Code | [amazon-science/chronos-forecasting](https://github.com/amazon-science/chronos-forecasting) |
| Code license | [Apache-2.0](https://github.com/amazon-science/chronos-forecasting/blob/main/LICENSE) |
| Weights license declaration | Apache-2.0 in the official model-card metadata, checked 2026-10-24 |
| Installed code version | Not installed in this environment |
| Artifact revision/hash | None provisioned; no weights were downloaded or executed |
| Verification | Fallback/strict-unavailable behavior tested. Real **Chronos-2** inference is not verified; the local ridge model above is separately verified. |

The model-card declaration and code license are distinct evidence. The weights
LICENSE URL was not retrievable during this audit; the provider therefore requires
an audited local artifact before use. Installing an optional provider does not
turn the statistical forecast into ML evidence.

Provisioning must record an immutable 40-character model repository commit and a
`model_provenance.json` in the local model directory containing `repository`,
`revision`, `weights_license`, and a `sha256` mapping. That mapping must cover
`config.json`, `LICENSE`, and all local `.safetensors` weights. Preserve the model
card, license, applicable notices and attribution with any redistributed artifact;
verify the exact revision's conditions before distributing it. Weights stay outside
the repository. There is no external inference/data upload in this implementation.

Set `FLOWBRIDGE_WORKSPACE_CHRONOS_PATH` and `FLOWBRIDGE_WORKSPACE_CHRONOS_REVISION`
only after that audit and pin the installed provider and its dependencies in the
project lockfile. The provider uses local files only, reuses one CPU instance, checks
the installed `predict_df` signature, and rejects missing/misaligned/nonfinite
quantiles. Provenance stores installed code version, model revision and manifest
SHA256. Failure either returns the labelled statistical fallback or HTTP 503 when
`allow_statistical_fallback=false`. No fine-tuning is performed.

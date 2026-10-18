# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from flowbridge.settings import Settings

from .model import ContextDataset, TabICLRuntime
from .schemas import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    PREPROCESSING_VERSION,
    Features,
    FeatureSnapshot,
)

CHECKPOINT = "tabicl-classifier-v2-20260212.ckpt"


def demo_context(path: Path, count: int) -> None:
    # Explicit synthetic fixture; not evaluation evidence or actual supplier history.
    generator = random.Random(20261016)
    rows = []
    for index in range(count):
        supplier = index % 6
        category = ["laptop", "monitor", "accessory"][index % 3]
        size = generator.uniform(0.2, 4)
        backlog = generator.randrange(12)
        wait = generator.uniform(0.5, 4)
        lead = max(
            1, generator.lognormvariate(1 + supplier * 0.13 + size * 0.08, 0.45) + backlog * 0.15
        )
        deadline = generator.randrange(4, 20)
        issued = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index)
        created = issued - timedelta(days=wait)
        completion = issued + timedelta(days=lead)
        known = completion + timedelta(days=1)
        due = created + timedelta(days=deadline)
        label = int(completion.date() > due.date())
        base = dict(
            supplier_id=str(uuid5(NAMESPACE_URL, f"flowbridge-ml-demo/supplier/{supplier}")),
            product_category=category,
            order_size_ratio=size,
            hist_lead_p50_days=3 + supplier * 0.4,
            hist_lead_p90_days=7 + supplier * 0.7,
            hist_late_rate_smoothed=0.1 + supplier * 0.08,
            hist_sample_count=20,
            other_open_order_count=backlog,
        )
        a = Features(
            prediction_stage="PR_CREATED",
            days_to_deadline=deadline,
            expected_issue_wait_days=wait,
            **base,
        )
        elapsed = min(lead * 0.5, 3)
        observed = issued + timedelta(days=elapsed)
        b = Features(
            prediction_stage="PO_MONITORING",
            days_to_deadline=(due.date() - observed.date()).days,
            elapsed_days_since_issue=elapsed,
            remaining_size_ratio=size,
            confirmation_state="confirmed" if elapsed > 1 else "waiting_confirmation",
            confirmation_wait_days=min(elapsed, 1),
            received_fraction=0,
            promise_margin_days=generator.choice([-2, -1, 0, 1, 2]),
            **base,
        )
        for asof, features in [(created, a), (observed, b)]:
            rows.append(
                {
                    "group_id": str(uuid5(NAMESPACE_URL, f"flowbridge-ml-demo/item/{index}")),
                    "feature_asof": asof.isoformat(),
                    "label_known_at": known.isoformat(),
                    "x": features.model_dump(),
                    "y": label,
                }
            )
    manifest = {
        "feature_schema_version": FEATURE_VERSION,
        "preprocessing_version": PREPROCESSING_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "context_dataset_version": "synthetic-smoke-v1",
        "source_kind": "synthetic",
        "provenance": "FlowBridge synthetic smoke fixture; no measured business accuracy",
        "label_cutoff": max(r["label_known_at"] for r in rows),
        "quantity_references": {},
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    ContextDataset(path, count * 2)
    print(f"Created synthetic context: {len(rows)} rows; not validated for real purchasing")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare/validate ML artifacts; no database writes"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo-context")
    demo.add_argument("--output", type=Path, default=Path(".cache/ml/context.json"))
    demo.add_argument("--orders", type=int, default=96, choices=range(12, 366))
    validate = sub.add_parser("validate-context")
    validate.add_argument("path", type=Path)
    download = sub.add_parser("download-checkpoint")
    download.add_argument("--directory", type=Path, default=Path(".cache/models"))
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--context", type=Path, default=Path(".cache/ml/context.json"))
    smoke.add_argument("--checkpoint", type=Path, default=Path(".cache/models") / CHECKPOINT)
    args = parser.parse_args()
    if args.command == "demo-context":
        demo_context(args.output, args.orders)
    elif args.command == "validate-context":
        context = ContextDataset(args.path, 50000)
        print(
            json.dumps(
                {
                    "context_dataset_version": context.version,
                    "sha256": context.sha256,
                    "rows": len(context.features),
                    "source_kind": context.source_kind,
                }
            )
        )
    elif args.command == "download-checkpoint":
        from huggingface_hub import hf_hub_download

        path = Path(
            hf_hub_download(repo_id="jingang/TabICL", filename=CHECKPOINT, local_dir=args.directory)
        )
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(json.dumps({"checkpoint": CHECKPOINT, "sha256": digest, "license": "BSD-3-Clause"}))
    else:
        settings = Settings(
            _env_file=None,
            ml_context_path=args.context,
            ml_checkpoint_path=args.checkpoint,
            ml_n_estimators=1,
        )
        runtime = TabICLRuntime(settings)
        runtime.load()
        runtime.ensure_ready()
        now = datetime.now(UTC)
        snapshots = [
            FeatureSnapshot(
                x=x,
                feature_asof=now,
                product_id=uuid5(NAMESPACE_URL, "smoke-product"),
                quantity="100",
                unit="piece",
                requested_delivery_date=now.date(),
            )
            for x in runtime.context.features[:2]
        ]
        scores = runtime.infer(snapshots)
        print(
            json.dumps(
                {
                    "status": "success",
                    "real_tabicl_inference": True,
                    "scores": scores,
                    "score_is_calibrated": False,
                    **runtime.metadata(),
                }
            )
        )


if __name__ == "__main__":
    main()

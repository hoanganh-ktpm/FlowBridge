# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Adapter dữ liệu mẫu: fixture tổng hợp, không kết nối database thật.

from flowbridge.data.schemas import (
    CollectionDefinition,
    IngestionResult,
    PlatformJob,
    Record,
    RecordRevision,
)
from flowbridge.sample import APPLICATION_ID, COLLECTION, JOB_ID, RECORD_ID, SAMPLE_TIME


class SampleDataAdapter:
    # Mỗi lần đọc dựng DTO mới; request ghi không sửa fixture dùng chung.

    def collection(self) -> CollectionDefinition:
        # Trả định nghĩa collection mẫu duy nhất của fixture.
        return CollectionDefinition(
            collection=COLLECTION,
            application_id=APPLICATION_ID,
            schema_version=1,
            record_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["draft", "active", "inactive"]},
                },
                "required": ["name", "status"],
            },
            policy_key="POLICY-SAMPLE",
        )

    def records(self) -> list[Record]:
        # Hai record mẫu cố định cho đọc/lọc/phân trang.
        return [
            Record(
                id=RECORD_ID,
                version=2,
                data={"name": "Sample item", "status": "active"},
                created_at=SAMPLE_TIME,
                updated_at=SAMPLE_TIME,
            ),
            Record(
                id="REC-SECOND",
                version=1,
                data={"name": "Second sample", "status": "inactive"},
                created_at=SAMPLE_TIME,
                updated_at=SAMPLE_TIME,
            ),
        ]

    def history(self, record: Record) -> list[RecordRevision]:
        # Dựng revision giảm dần từ version hiện tại của record.
        return [
            RecordRevision(
                record_id=record.id,
                version=version,
                schema_version=1,
                operation="create" if version == 1 else "update",
                changed_at=SAMPLE_TIME,
                data=record.data
                if version == record.version
                else {**record.data, "status": "draft"},
            )
            for version in range(record.version, 0, -1)
        ]

    def job(self) -> PlatformJob:
        # Trả job mẫu đã succeeded kèm lineage tổng hợp.
        return PlatformJob(
            job_id=JOB_ID,
            status="succeeded",
            created_at=SAMPLE_TIME,
            result=IngestionResult(
                collection=COLLECTION,
                schema_version=1,
                source_kind="IT",
                source_id="source-sample",
                observed_at=SAMPLE_TIME,
                received=1,
                accepted=1,
                rejected=0,
                lineage_id="LINEAGE-SAMPLE",
                committed_at=SAMPLE_TIME,
            ),
            error=None,
        )

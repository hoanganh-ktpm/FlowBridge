# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Adapter tệp mẫu: một file clean và một file pending dùng chung bytes.

import hashlib

from flowbridge.files.schemas import File
from flowbridge.sample import FILE_BYTES, FILE_ID, PENDING_FILE_ID, RECORD_ID, SAMPLE_TIME


class SampleFilesAdapter:
    # Fixture tệp tổng hợp, không đọc object storage thật.
    def files(self) -> list[File]:
        # Dựng file clean mẫu rồi sao chép thành bản pending.
        clean = File(
            id=FILE_ID,
            name="sample.txt",
            content_type="text/plain",
            size=len(FILE_BYTES),
            sha256=hashlib.sha256(FILE_BYTES).hexdigest(),
            scan_status="clean",
            metadata={"resource_type": "sample_item", "resource_id": RECORD_ID},
            created_at=SAMPLE_TIME,
        )
        return [clean, clean.model_copy(update={"id": PENDING_FILE_ID, "scan_status": "pending"})]

    def content(self) -> bytes:
        # Bytes mẫu dùng chung cho download và search literal.
        return FILE_BYTES

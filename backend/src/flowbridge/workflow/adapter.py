# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Adapter workflow mẫu: một instance active và một task chờ duyệt.

from flowbridge.sample import DEFINITION_KEY, INSTANCE_ID, RECORD_ID, SAMPLE_TIME, TASK_ID
from flowbridge.workflow.schemas import WorkflowInstance, WorkflowTask


class SampleWorkflowAdapter:
    # Fixture workflow tổng hợp, không gọi Flowable REST.
    def instance(self) -> WorkflowInstance:
        # Trả instance active mẫu gắn business_key cố định.
        return WorkflowInstance(
            id=INSTANCE_ID,
            definition_key=DEFINITION_KEY,
            business_key=RECORD_ID,
            status="active",
            created_at=SAMPLE_TIME,
        )

    def tasks(self) -> list[WorkflowTask]:
        # Một task pending mẫu cho phép approve/reject.
        return [
            WorkflowTask(
                id=TASK_ID,
                instance_id=INSTANCE_ID,
                business_key=RECORD_ID,
                name="Sample approval",
                status="pending",
                allowed_actions=["approve", "reject"],
                assigned_role="sample_approver",
                version=1,
            )
        ]

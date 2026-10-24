# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Workflow Engine (Layer 1): Flowable-backed reusable execution boundary.
# Engine owns BPMN definitions, process instances and execution state.
# Core owns registries/governance metadata. This module owns the adapter,
# the action registry/gateway, the workflow API and the event router.

from __future__ import annotations

from app.platform.workflow.router import create_workflow_app, workflow_router
from app.platform.workflow.triggers import (
    WorkflowTriggerSpec,
    drop_trigger,
    provision_trigger,
    trigger_payload,
)

__all__ = [
    "create_workflow_app",
    "workflow_router",
    "WorkflowTriggerSpec",
    "trigger_payload",
    "provision_trigger",
    "drop_trigger",
]

# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Registered Supplier Assistant ORM mappings, available to application code and Alembic.

from flowbridge.models.api_idempotency import ApiIdempotency
from flowbridge.models.base import Base
from flowbridge.models.chat import ChatSession, ChatTurn
from flowbridge.models.supplier_assistant import (
    AnalysisSupplierCandidate,
    AnalysisTask,
    ApprovalHistory,
    ConfirmationToken,
    Department,
    EmailLog,
    GoodsReceipt,
    InventorySnapshot,
    Notification,
    OutboxEvent,
    Product,
    Proposal,
    ProposalItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderSchedule,
    PurchaseRequest,
    PurchaseRequestItem,
    RiskAnalysisReport,
    RiskPrediction,
    Role,
    Supplier,
    SupplierConfirmation,
    SupplierConfirmationItem,
    SupplierProduct,
    User,
    UserRole,
)

__all__ = [
    "ChatSession",
    "ChatTurn",
    "ApiIdempotency",
    "Base",
    "AnalysisSupplierCandidate",
    "AnalysisTask",
    "ApprovalHistory",
    "ConfirmationToken",
    "Department",
    "EmailLog",
    "GoodsReceipt",
    "InventorySnapshot",
    "Notification",
    "OutboxEvent",
    "Product",
    "Proposal",
    "ProposalItem",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "PurchaseOrderSchedule",
    "PurchaseRequest",
    "PurchaseRequestItem",
    "RiskAnalysisReport",
    "RiskPrediction",
    "Role",
    "Supplier",
    "SupplierConfirmation",
    "SupplierConfirmationItem",
    "SupplierProduct",
    "User",
    "UserRole",
]

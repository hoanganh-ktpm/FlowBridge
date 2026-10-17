# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
from flowbridge_agent.nodes import ProcurementAgentBase as ProcurementAgentComponent


class FlowBridgeProcurementAgent(ProcurementAgentComponent):
    name = "FlowBridgeProcurementAgent"

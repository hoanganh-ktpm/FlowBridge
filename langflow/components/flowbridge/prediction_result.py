# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from flowbridge_agent.nodes import PredictionResultBase as ActionComponent


class FlowBridgePredictionResult(ActionComponent):
    name = "FlowBridgePredictionResult"

# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.
ARG LANGFLOW_IMAGE
FROM ${LANGFLOW_IMAGE}
USER root
COPY backend/flowbridge /opt/flowbridge/backend/flowbridge
COPY langflow/runtime /opt/flowbridge/runtime
COPY langflow/components /opt/flowbridge/components
COPY scripts/container_chat.py /opt/flowbridge/scripts/container_chat.py
COPY scripts/upgrade_chat_flows.py /opt/flowbridge/scripts/upgrade_chat_flows.py
COPY langflow/setup_agent.py langflow/setup_ver2.py langflow/upgrade_ver2_data.py langflow/upgrade_ver2_coordination.py langflow/add_ver2_protocol_nodes.py /opt/flowbridge/
COPY LICENSE /opt/flowbridge/LICENSE
ENV PYTHONPATH=/opt/flowbridge/runtime:/opt/flowbridge/backend \
    HOME=/tmp LANGFLOW_CONFIG_DIR=/var/lib/langflow \
    LANGFLOW_COMPONENTS_PATH=/opt/flowbridge/components \
    LANGFLOW_SAVE_DB_IN_CONFIG_DIR=true LANGFLOW_A2A_ENABLED=true \
    LANGFLOW_DO_NOT_TRACK=true DO_NOT_TRACK=true PYTHONDONTWRITEBYTECODE=1
RUN python -c "from flowbridge_agent.serving import CallerMiddleware; from flowbridge_agent.ver2_data_nodes import DataActionTool; from flowbridge_agent.ver2_coordination_nodes import CoordinatedCentralAgent"
USER 10001:0
ENTRYPOINT []
CMD ["python", "/opt/flowbridge/scripts/container_chat.py", "langflow"]

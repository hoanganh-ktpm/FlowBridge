# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Standard Uvicorn logging with query strings excluded and validated correlation only.
import logging
from copy import deepcopy
from typing import Any

from uvicorn.config import LOGGING_CONFIG

from app.platform.http.correlation import get_correlation_id


class SafeAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return False
        client, method, path, version, status = record.args
        record.args = (client, method, str(path).split("?", 1)[0], version, status)
        record.correlation_id = get_correlation_id() or "unassigned"
        return True


def access_log_config() -> dict[str, Any]:
    config = deepcopy(LOGGING_CONFIG)
    config["filters"] = {"safe-access": {"()": SafeAccessFilter}}
    config["handlers"]["access"]["filters"] = ["safe-access"]
    config["formatters"]["access"]["fmt"] = (
        '%(levelprefix)s %(client_addr)s "%(request_line)s" %(status_code)s %(correlation_id)s'
    )
    return config

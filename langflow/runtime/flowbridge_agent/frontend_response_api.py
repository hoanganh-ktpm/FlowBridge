# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract one typed Data output from a native Langflow run response, without I/O."""

from flowbridge_agent.frontend_response_contract import validate_response


def extract_frontend_response(run_response: dict, node_id="FrontendResponse-ver2-central") -> dict:
    matches = []
    for run in run_response.get("outputs", []):
        for output in run.get("outputs", []):
            if output.get("component_id") != node_id:
                continue
            result = output.get("results", {}).get("response")
            if not isinstance(result, dict):
                value = output.get("outputs", {}).get("response", {})
                # Native Graph uses object; the HTTP v1 projection labels Data as data.
                if not isinstance(value, dict) or value.get("type") not in {"object", "data"}:
                    continue
                result = value.get("message")
                if not isinstance(result, dict):
                    continue
            # Native Data.model_dump is {data: {...}, text_key: ...}, not Message.text.
            if "data" in result:
                result = result["data"]
            matches.append(result)
    if len(matches) != 1:
        raise ValueError("EXPECTED_ONE_FRONTEND_JSON_OUTPUT")
    return validate_response(matches[0])

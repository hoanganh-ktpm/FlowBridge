# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT.parent / "backend")]


async def run_one(case: dict) -> dict:
    from flowbridge_agent.config import BackendConfig
    from flowbridge_agent.nodes import LunaModelBase
    from flowbridge_agent.prompt import SYSTEM_PROMPT
    from flowbridge_agent.tools import BackendTools, build_tools
    from langchain_core.messages import HumanMessage, SystemMessage

    client = BackendTools(BackendConfig.from_local())
    tools = [
        t
        for group in ("catalog", "purchase", "prediction", "knowledge")
        for t in build_tools(group, client)
    ]
    model = LunaModelBase(output_tokens=600).build_model().bind_tools(tools)
    day = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(timespec="seconds")
    # Exactly ONE model request, no tool execution, retry, follow-up or automatic batch.
    response = await model.ainvoke(
        [
            SystemMessage(
                SYSTEM_PROMPT.replace("{current_date}", day)
                .replace("{max_tool_calls}", "3")
                .replace("{max_model_calls}", "4")
            ),
            HumanMessage(case["question"]),
        ]
    )
    calls = response.tool_calls
    actual = [call["name"] for call in calls]
    valid_args = True
    effective_arguments = []
    named = {tool.name: tool for tool in tools}
    for call in calls:
        try:
            tool = named[call["name"]]
            tool.args_schema.model_validate(call["args"])
            effective_arguments.append(tool.resolve_arguments(call["args"]))
        except (KeyError, ValueError):
            valid_args = False
    expected_args = case.get("expected_arguments")
    arguments_match = expected_args is None or (
        len(effective_arguments) == 1
        and all(effective_arguments[0].get(key) == value for key, value in expected_args.items())
    )
    return {
        "case_id": case["id"],
        "question": case["question"],
        "model": "gpt-5.6-luna",
        "openai_requests": 1,
        "actual_tools": actual,
        "expected_tools": case["expected_first_tools"],
        "arguments_valid": valid_args,
        "selection_pass": actual == case["expected_first_tools"] and valid_args and arguments_match,
        "effective_arguments": effective_arguments,
        "expected_arguments_match": arguments_match,
        "tool_calls": calls,
        "answer": response.content,
        "usage": response.usage_metadata,
        "scope": "One model selection step only; not an end-to-end backend/model quality test",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Default: list cases without network. Paid mode: one approved request."
    )
    parser.add_argument("--case")
    parser.add_argument("--approve-one-openai-request", action="store_true")
    options = parser.parse_args()
    cases = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))
    if not options.case:
        for case in cases:
            print(case["id"], ":", case["question"])
        print("OpenAI requests: 0")
        return
    case = next((c for c in cases if c["id"] == options.case), None)
    if case is None:
        parser.error("Unknown case")
    if not options.approve_one_openai_request:
        print(json.dumps(case, ensure_ascii=False, indent=2))
        print("Dry run. OpenAI requests: 0. Approval is required for a paid test.")
        return
    try:
        result = asyncio.run(run_one(case))
    except Exception as exc:
        # Do not print arbitrary SDK messages, request headers or supplied secrets.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "retry": False,
                    "attempted_openai_requests": 1,
                }
            )
        )
        raise SystemExit(1) from None
    destination = ROOT / "data" / ("eval-" + case["id"] + ".json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Verify the SDK/tool/database flow with scripted or live model responses."""

import argparse
import asyncio
import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from agents import Model, ModelResponse
from agents.exceptions import AgentsException
from agents.usage import Usage
from openai import OpenAIError
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from agent_models import PROVIDERS, configured_model, model_name
from inspection_tools import DEMO_DB, InspectionTools
from model_agent import run_model_agent
from replay_acceptance import findings, require


class ScriptedModel(Model):
    """Return SDK-native tool calls and structured output with no API traffic."""

    def __init__(self, turns):
        self.turns = iter(turns)
        self.turn = 0

    async def get_response(self, *args, **kwargs):
        instruction = next(self.turns)
        self.turn += 1
        item_id = f"scripted-{self.turn}"
        if isinstance(instruction, tuple):
            name, arguments = instruction
            item = ResponseFunctionToolCall(
                type="function_call", call_id=item_id, name=name,
                arguments=json.dumps(arguments, ensure_ascii=False),
            )
        else:
            item = ResponseOutputMessage(
                type="message", role="assistant", status="completed", id=item_id,
                content=[ResponseOutputText(
                    type="output_text", text=json.dumps(instruction, ensure_ascii=False), annotations=[],
                )],
            )
        return ModelResponse(output=[item], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError("The replay uses Runner.run without streaming.")


def search_call(survey_number=None, sensor_kind=None):
    return "search_observations", dict(
        survey_number=survey_number, sensor_kind=sensor_kind, segment_id=None,
        target_keyword="交叉", asset_kind="管道",
    )


def scripted_case(observation=None, save=False):
    if observation is None:
        return ScriptedModel([
            search_call(),
            dict(action="clarify", message="你想用首次声呐还是复检视频？", observation_id=None,
                 identity_id=None, phenomenon=None, status=None, basis=None),
        ])
    video = observation == "O2"
    identity = "I3" if video else "I1"
    status = "确认" if video else "疑似"
    basis = "可见连续管体及识别标记。" if video else "线状回波有 P_X、C_X 两个候选。"
    proposal = dict(identity_id=identity, phenomenon="裸露", status=status, basis=basis)
    turns = [
        search_call(2, "摄像机") if video else search_call(1, "声呐"),
        ("query_observation", {"observation_id": observation}),
    ]
    if save and video:
        turns.append(("submit_finding", proposal))
    turns.append(dict(
        action="save" if save else "answer",
        message="影像可见部分确认局部裸露。" if video else "管段疑似裸露，目标身份仍有歧义。",
        observation_id=observation, **proposal,
    ))
    return ScriptedModel(turns)


async def replay(name, request, observation, action, model):
    with TemporaryDirectory(prefix="uuv-model-replay-") as directory:
        database = Path(directory) / DEMO_DB.name
        copyfile(DEMO_DB, database)
        tools = InspectionTools(database)
        before = findings(database)
        if isinstance(request, dict):
            receipt = tools.submit_finding(**request)
            expected_reason = "身份状态为候选" if request["status"] == "确认" else "疑似、确认、证据不足"
            require(not receipt["written"] and expected_reason in receipt.get("reason", ""), name)
            if request["status"] == "确认":
                require("ConfirmedShape" in receipt.get("validation_report", ""), name)
            result = {"receipt": receipt}
            calls = [{"tool": "commit_finding", "input": request, "output": receipt}]
        else:
            result = await run_model_agent(request, tools, model, allow_write=action == "save")
            calls = result.pop("tool_calls")
            names = [call["tool"] for call in calls]
            require(result["action"] == action, f"期待 {action}，实际为 {result['action']}。")
            require("search_observations" in names, "缺少观测搜索调用。")
            if observation is None:
                candidates = {item["observation_id"] for item in result["candidates"]}
                require({"O1", "O2"} <= candidates and bool(result["question"].strip()), name)
            else:
                video = observation == "O2"
                searched = {
                    candidate["observation_id"]
                    for call in calls if call["tool"] == "search_observations"
                    for candidate in call["output"]["observations"]
                }
                require(observation in searched, name)
                require(result["observation_id"] == observation and "query_observation" in names, name)
                require(result["subject"]["identity_id"] == ("I3" if video else "I1"), name)
                require(result["status"] == ("确认" if video else "疑似"), name)
                require(result["evidence"] == ("V08 / 12–18 s" if video else "S17 / ping 120–145"), name)
                if not video:
                    require({item["segment"] for item in result["identities"]} == {"P_X", "C_X"}, name)
            require(names.count("submit_finding") <= (action == "save"), "拟提交次数与预期不符。")
            receipt = result["receipt"]
            require(names.count("commit_finding") == (action == "save"), receipt.get("reason", name))

        after = findings(database)
        new_rows = [row for key, row in after.items() if key not in before]
        require(all(after.get(key) == row for key, row in before.items()), name)
        if action == "save":
            require(receipt["written"] and len(new_rows) == 1, name)
            require(new_rows[0] == {
                "id": receipt["finding_id"],
                "identity": result["subject"]["identity_id"], "phenomenon": "裸露",
                "status": result["status"], "basis": result["basis"],
            }, name)
        else:
            require(not receipt["written"] and not new_rows, name)
        return {
            "scenario": name, "input": request, "tool_calls": calls, "result": result,
            "finding_count_before": len(before), "finding_count_after": len(after), "new_findings": new_rows,
        }


async def replay_write_denial(name, request, allow_write):
    scripted = list(scripted_case("O2").turns)
    scripted[-1]["action"] = "save"
    with TemporaryDirectory(prefix="uuv-model-write-denial-") as directory:
        database = Path(directory) / DEMO_DB.name
        copyfile(DEMO_DB, database)
        before = findings(database)
        result = await run_model_agent(
            request, InspectionTools(database), ScriptedModel(scripted), allow_write=allow_write,
        )
        after = findings(database)
        calls = result.pop("tool_calls")
        require(result["action"] == "save", name)
        require(result["receipt"] == {
            "written": False, "reason": "本次请求未获得应用层写入授权。",
        }, name)
        require(before == after and all(call["tool"] != "commit_finding" for call in calls), name)
        return {
            "scenario": name, "input": request, "tool_calls": calls, "result": result,
            "finding_count_before": len(before), "finding_count_after": len(after), "new_findings": [],
        }


async def run_suite(live_model=None):
    cases = [
        ("复检录像改写", "复检录像里，交叉处那段管子有没有露出来？把结果存一下。", "O2", "save"),
        ("首次声呐改写", "第一次声呐扫过交叉处时，能看出管子露出来了吗？把初判记下。", "O1", "save"),
        ("观测有歧义", "看看交叉点管子露出来没有。", None, "clarify"),
        ("只看结果", "用复检视频判断一下，只看结果。", "O2", "answer"),
        ("强行确认候选身份",
         dict(identity_id="I1", phenomenon="裸露", status="确认", basis="线状回波"), None, "reject"),
        ("非法状态",
         dict(identity_id="I3", phenomenon="裸露", status="已确认", basis="外表面可见"), None, "reject"),
    ]
    runs = []
    failures = []
    for name, request, observation, action in cases:
        model = live_model or scripted_case(observation, action == "save")
        try:
            runs.append(await replay(name, request, observation, action, model))
        except AssertionError as error:
            failures.append({"scenario": name, "reason": str(error)})
        except OpenAIError as error:
            failures.append({
                "scenario": name, "error": type(error).__name__,
                "status": getattr(error, "status_code", None), "code": getattr(error, "code", None),
            })
        except AgentsException as error:
            failures.append({"scenario": name, "error": type(error).__name__})
    if live_model is None:
        for name, request, allow_write in [
            ("未授权时模型误判保存", "判断交叉点管段是否裸露。", False),
            ("明确只读时模型误判保存", "根据 O2 判断，请不要把结果保存。", True),
        ]:
            runs.append(await replay_write_denial(name, request, allow_write))
    return {"runs": runs, "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="使用真实模型运行。")
    parser.add_argument("--provider", choices=PROVIDERS, default="openai")
    parser.add_argument("--model")
    args = parser.parse_args()

    async def run():
        if not args.live:
            return {"mode": "scripted", **await run_suite()}
        name = model_name(args.provider, args.model)
        async with configured_model(args.provider, name) as model:
            return {"mode": "live", "provider": args.provider, "model": name, **await run_suite(model)}

    try:
        report = asyncio.run(run())
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

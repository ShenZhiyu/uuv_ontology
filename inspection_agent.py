"""Offline agent for the two crossing-point exposure scenarios."""

import argparse
import json
import re
import sqlite3

from inspection_tools import InspectionTools


def _select_observation(request):
    if "裸露" not in request or not (
        "P_X" in request.upper() or ("交叉点" in request and ("管段" in request or "管道" in request))
    ):
        raise ValueError("请描述交叉点管段的裸露判读任务，例如：根据 O2，判断交叉点管段是否裸露。")
    identifiers = set(re.findall(r"(?<![A-Z0-9_])O[0-9]+(?![A-Z0-9_])", request.upper()))
    if "声呐" in request or any(word in request for word in ("首次", "第一次", "第一批")):
        identifiers.add("O1")
    if "视频" in request or any(word in request for word in ("第二次", "第二批", "复检")):
        identifiers.add("O2")
    if identifiers not in ({"O1"}, {"O2"}):
        raise ValueError("请指定一个观测：首次声呐 O1 或第二次视频 O2。")
    return identifiers.pop()


def run_agent(request, tools):
    observation_id = _select_observation(request)
    lookup_input = {"observation_id": observation_id}
    lookup = tools.query_observation(**lookup_input)
    calls = [{"tool": "查询观测", "input": lookup_input, "output": lookup}]
    if "observation" not in lookup:
        return {"receipt": {"written": False, "reason": lookup["reason"]}, "tool_calls": calls}

    observation = lookup["observation"]
    identities = lookup["identities"]
    pipe_identities = [identity for identity in identities if identity["segment"] == "P_X"]
    if len(pipe_identities) != 1:
        return {
            "evidence": observation["evidence"],
            "identities": identities,
            "receipt": {"written": False, "reason": "无法唯一选择交叉点管段的身份记录。"},
            "tool_calls": calls,
        }

    identity = pipe_identities[0]
    if (
        identity["identity_status"] == "确认"
        and observation["sensor_kind"] == "摄像机"
        and observation["property"] == "外表面可见性"
        and observation["result"] == "可见连续管体及识别标记"
        and observation["evidence"].strip()
    ):
        status = "确认"
        conclusion = "该区段影像可见部分确认存在局部裸露。"
        basis = f"{observation['result']}；{identity['identity_basis']}。结论限于影像可见部分。"
    elif identity["identity_status"] == "候选" and observation["result"] == "线状回波":
        status = "疑似"
        conclusion = "交叉点管段疑似裸露。声呐目标身份仍有歧义。"
        candidates = "、".join(item["segment"] for item in identities if item["identity_status"] == "候选")
        basis = f"{observation['result']}；{identity['identity_basis']}。候选区段为 {candidates}。"
    else:
        status = "证据不足"
        conclusion = "现有观测不足以判断交叉点管段是否裸露。"
        basis = f"观测结果为{observation['result']}，身份状态为{identity['identity_status']}。"

    if re.search(r"(?:不|无需|不要|不用|暂缓|禁止)\s*(?:保存|写入|入库|提交)|只(?:查询|看|判断|读)", request):
        receipt = {"written": False, "reason": "按请求仅返回判读。"}
    else:
        submission = {
            "identity_id": identity["identity_id"],
            "phenomenon": "裸露",
            "status": status,
            "basis": basis,
        }
        receipt = tools.submit_finding(**submission)
        calls.append({"tool": "提交判读", "input": submission, "output": receipt})
    return {
        "conclusion": conclusion,
        "status": status,
        "subject": identity,
        "basis": basis,
        "observation_id": observation_id,
        "evidence": observation["evidence"],
        "identities": identities,
        "receipt": receipt,
        "tool_calls": calls,
    }


def main():
    parser = argparse.ArgumentParser(description="对交叉点管段做一次巡检判读。")
    parser.add_argument("--db", required=True, help="演示 SQLite 库的可写副本")
    parser.add_argument("request", help="一句中文巡检判读请求")
    args = parser.parse_args()
    try:
        result = run_agent(args.request, InspectionTools(args.db))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f"{error}\n")
    result.pop("tool_calls")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

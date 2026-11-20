"""Replay each acceptance scenario against a separate copy of the demo."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from inspection_agent import run_agent
from inspection_tools import DEMO_DB, InspectionTools


def findings(database):
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {row["id"]: dict(row) for row in db.execute("SELECT * FROM Finding")}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def replay(scenario, request, expected_status, expected_identity):
    with TemporaryDirectory(prefix="uuv-replay-") as directory:
        database = Path(directory) / DEMO_DB.name
        copyfile(DEMO_DB, database)
        tools = InspectionTools(database)
        before = findings(database)
        if isinstance(request, str):
            result = run_agent(request, tools)
            calls = result.pop("tool_calls")
            require([call["tool"] for call in calls] == ["查询观测", "提交判读"], scenario)
            require(result["status"] == expected_status, scenario)
            subject = result["subject"]
            require(subject["segment"] == "P_X" and subject["identity_id"] == expected_identity, scenario)
            if expected_identity == "I1":
                require({item["segment"] for item in result["identities"]} == {"P_X", "C_X"}, scenario)
                require(result["evidence"] == "S17 / ping 120–145", scenario)
            else:
                require(subject["asset"] == "P01" and (subject["startM"], subject["endM"]) == (40, 60), scenario)
                require(result["evidence"] == "V08 / 12–18 s" and "局部裸露" in result["conclusion"], scenario)
            receipt = result["receipt"]
        else:
            receipt = tools.submit_finding(**request)
            calls = [{"tool": "提交判读", "input": request, "output": receipt}]
            result = {"receipt": receipt}
            if request["status"] == "确认":
                require("身份状态为候选" in receipt.get("reason", ""), scenario)
                require("ConfirmedShape" in receipt.get("validation_report", ""), scenario)
            else:
                require("疑似、确认、证据不足" in receipt.get("reason", ""), scenario)

        after = findings(database)
        new_rows = [row for key, row in after.items() if key not in before]
        require(all(after.get(key) == row for key, row in before.items()), scenario)
        if expected_status is None:
            require(receipt["written"] is False and not new_rows, scenario)
        else:
            require(receipt["written"] is True and len(new_rows) == 1, scenario)
            submitted = calls[-1]["input"]
            require(new_rows[0] == {
                "id": receipt["finding_id"], "identity": expected_identity,
                "phenomenon": "裸露", "status": expected_status, "basis": submitted["basis"],
            }, scenario)
        return {
            "scenario": scenario, "input": request, "tool_calls": calls, "result": result,
            "finding_count_before": len(before), "finding_count_after": len(after),
            "new_findings": new_rows,
        }


def main():
    report = [
        replay("首次声呐观测", "根据首次巡检的声呐观测，判断交叉点管段是否裸露，并保存判读。", "疑似", "I1"),
        replay("第二次视频观测", "根据第二次巡检的视频观测 O2，判断交叉点管段是否裸露，并保存判读。", "确认", "I3"),
        replay("强行误确认", {"identity_id": "I1", "phenomenon": "裸露", "status": "确认", "basis": "线状回波"}, None, None),
        replay("非法状态", {"identity_id": "I3", "phenomenon": "裸露", "status": "已确认", "basis": "外表面可见"}, None, None),
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

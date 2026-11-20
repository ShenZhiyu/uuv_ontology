# UUV 海底管道与海缆巡检感知本体

这是一个用模拟数据演示水下无人航行器（UUV）巡检判读的原型。项目用 OWL/RDF 表达资产区段、巡检批次、观测、身份判定和状态判读，用 SQLite 保存实例。命令行 Agent 根据已有观测生成判读，再由 SHACL 校验决定能否入库。

当前示例聚焦管缆交叉点的管段裸露判读。声呐和视频均为模拟证据索引。Agent 使用本地规则，无需模型服务，处理范围从已有观测记录开始。

## 快速开始

在仓库根目录运行以下命令。已用 Python 3.12 验证。示例在临时目录安装依赖，并向演示库的副本写入。

```sh
demo_dir=$(mktemp -d)
python3 -m venv "$demo_dir/venv"
"$demo_dir/venv/bin/python" -m pip install -r requirements.txt
cp 巡检实例.sqlite "$demo_dir/巡检.sqlite"

"$demo_dir/venv/bin/python" inspection_agent.py --db "$demo_dir/巡检.sqlite" \
  '根据第二次巡检的视频观测 O2，判断交叉点管段是否裸露，并保存判读。'
```

输出为 JSON。`subject` 给出管段 `P_X`（管道 `P01`，里程 40–60 米），`conclusion` 将确认的局部裸露限定在影像可见范围，`evidence` 给出片段索引 `V08 / 12–18 s`。写入成功时，可用 `receipt.finding_id` 在副本的 `Finding` 表中查到新增记录。将请求换成首次巡检的声呐观测 `O1`，会看到 `P_X` 和 `C_X` 两个候选，保存状态为「疑似」。

## 验证与回放

在同一个 shell 中继续运行以下命令。

```sh
python3 verify.py
"$demo_dir/venv/bin/pyshacl" -s shapes.ttl -e ontology.ttl instances.ttl
"$demo_dir/venv/bin/python" replay_acceptance.py
```

`verify.py` 检查原始演示库的 6 项查询及外键。SHACL 命令校验 RDF 样例。回放脚本为每个场景创建独立的数据库副本，核对历史记录、记录数和新增编号。

| 场景 | 预期结果 |
| --- | --- |
| `O1` 首次声呐 | 以候选身份 `I1` 保存「疑似」，新增 1 条 |
| `O2` 第二次视频 | 以确认身份 `I3` 保存「确认」，新增 1 条 |
| 用候选身份 `I1` 提交「确认」 | SHACL 拒绝，新增 0 条 |
| 提交「已确认」 | 状态取值校验拒绝，新增 0 条 |

## 主要文件

| 文件 | 用途 |
| --- | --- |
| [ontology.ttl](ontology.ttl)、[instances.ttl](instances.ttl)、[shapes.ttl](shapes.ttl) | OWL 本体、RDF 模拟实例和 SHACL 规则 |
| [巡检实例.sqlite](巡检实例.sqlite) | 管缆交叉点演示数据库 |
| [inspection_agent.py](inspection_agent.py) | 中文巡检判读命令行入口 |
| [inspection_tools.py](inspection_tools.py) | 查询观测，校验全库快照及拟写记录，事务内追加判读 |
| [第一阶段说明.md](第一阶段说明.md) | 场景设定、术语、概念关系图和资料依据 |
| [第二阶段验收结果.json](第二阶段验收结果.json) | 一次实际回放的工具输入、回执和数据库记录 |

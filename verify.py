"""在解压目录中运行 python verify.py，仅依赖 Python 标准库。"""
import sqlite3
from pathlib import Path
root = Path(__file__).resolve().parent
db = sqlite3.connect((root / '巡检实例.sqlite').as_uri() + '?mode=ro', uri=True)
queries = [
('第二批次 A 区有哪些资产？','SELECT DISTINCT asset FROM Segment JOIN Route ON Route.id=Segment.route JOIN Survey ON Survey.area=Segment.area WHERE Survey.id="S2" ORDER BY asset',['C01','P01']),
('第二批次哪些区段存在覆盖缺口？',"SELECT segment FROM Coverage WHERE survey='S2' AND status='缺口'",['C_N']),
('首次巡检交叉点目标有哪些候选区段？',"SELECT segment FROM Identity WHERE observation='O1' AND status='候选' ORDER BY segment",['C_X','P_X']),
('哪些已确认的裸露判读可追溯到视频？',"SELECT segment||' / '||evidence FROM Finding JOIN Identity ON Identity.id=Finding.identity JOIN Observation ON Observation.id=Identity.observation WHERE phenomenon='裸露' AND Finding.status='确认' ORDER BY segment",['P_W / V03 / 2–9 s','P_X / V08 / 12–18 s']),
('交叉点管段的裸露判读如何变化？',"SELECT day||' / '||Finding.status FROM Finding JOIN Identity ON Identity.id=Finding.identity JOIN Observation ON Observation.id=Identity.observation JOIN Deployment ON Deployment.id=Observation.deployment JOIN Survey ON Survey.id=Deployment.survey WHERE segment='P_X' AND phenomenon='裸露' ORDER BY day",['2026-01-10 / 疑似','2026-02-10 / 确认']),
('交叉点管段的悬跨是否得到确认？',"SELECT Finding.status FROM Finding JOIN Identity ON Identity.id=Finding.identity WHERE segment='P_X' AND phenomenon='悬跨'",['证据不足'])]
for question, sql, expected in queries:
    actual = [row[0] for row in db.execute(sql)]
    if actual != expected:
        raise ValueError((question, expected, actual))
    print(question, '；'.join(actual))
if list(db.execute('PRAGMA foreign_key_check')):
    raise ValueError('存在失效外键')
print('6 项查询及外键校验通过')

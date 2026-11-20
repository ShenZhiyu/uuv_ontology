"""SQLite observation lookup and transactional SHACL validation."""

from contextlib import closing
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import sqlite3

from pyshacl import validate
from rdflib import Graph, Literal, Namespace, RDF, XSD
from rdflib.namespace import SH


ROOT = Path(__file__).resolve().parent
DEMO_DB = ROOT / "巡检实例.sqlite"
UUV = Namespace("https://example.org/uuv/v01/")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
CRS = "<http://www.opengis.net/def/crs/EPSG/0/32631>"
FINDING_STATUSES = ("疑似", "确认", "证据不足")

# Other columns are xsd:string. Names match the SQLite schema and ontology.
RDF_FIELDS = {
    "Area": {"geometry": GEO.wktLiteral},
    "Asset": {},
    "Route": {"asset": "ref", "geometry": GEO.wktLiteral},
    "Segment": {"route": "ref", "area": "ref", "startM": XSD.decimal, "endM": XSD.decimal},
    "Survey": {"area": "ref"},
    "Platform": {},
    "Sensor": {},
    "Deployment": {"survey": "ref", "platform": "ref", "sensor": "ref"},
    "Coverage": {"survey": "ref", "segment": "ref"},
    "Target": {},
    "Observation": {"deployment": "ref", "target": "ref", "instant": XSD.dateTime},
    "Identity": {"observation": "ref", "segment": "ref"},
    "Finding": {"identity": "ref"},
    "Measurement": {"observation": "ref", "value": XSD.decimal},
}


def _add_row(graph, table, row):
    subject = UUV[quote(row["id"], safe="")]
    graph.add((subject, RDF.type, UUV[table]))
    for column, value in row.items():
        if column == "id" or value is None:
            continue
        datatype = RDF_FIELDS[table].get(column, XSD.string)
        if datatype == "ref":
            term = UUV[quote(value, safe="")]
        elif datatype == GEO.wktLiteral:
            wkt = value if value.startswith("<") else f"{CRS} {value}"
            term = Literal(wkt, datatype=datatype)
        else:
            term = Literal(str(value), datatype=datatype)
        graph.add((subject, UUV[f"{table}_{column}"], term))
    return subject


class InspectionTools:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.ontology = Graph().parse(ROOT / "ontology.ttl", format="turtle")
        self.shapes = Graph().parse(ROOT / "shapes.ttl", format="turtle")

    def _connect(self, mode):
        db = sqlite3.connect(f"{self.database.as_uri()}?mode={mode}", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def query_observation(self, observation_id):
        with closing(self._connect("ro")) as db:
            rows = db.execute(
                """
                SELECT o.*, d.survey, sensor.kind AS sensor_kind,
                       i.id AS identity_id, i.status AS identity_status,
                       i.basis AS identity_basis, s.id AS segment,
                       s.startM, s.endM, r.asset, r.version AS route_version,
                       a.kind AS asset_kind
                FROM Observation AS o
                JOIN Deployment AS d ON d.id = o.deployment
                JOIN Sensor AS sensor ON sensor.id = d.sensor
                LEFT JOIN Identity AS i ON i.observation = o.id
                LEFT JOIN Segment AS s ON s.id = i.segment
                LEFT JOIN Route AS r ON r.id = s.route
                LEFT JOIN Asset AS a ON a.id = r.asset
                WHERE o.id = ?
                ORDER BY i.id
                """,
                (observation_id,),
            ).fetchall()
        if not rows:
            return {"observation_id": observation_id, "reason": "找不到指定观测。"}
        fields = ("id", "survey", "sensor_kind", "target", "instant", "property", "result", "evidence")
        identity_fields = (
            "identity_id", "identity_status", "identity_basis", "segment",
            "asset", "asset_kind", "route_version", "startM", "endM",
        )
        return {
            "observation": {field: rows[0][field] for field in fields},
            "identities": [
                {field: row[field] for field in identity_fields}
                for row in rows if row["identity_id"] is not None
            ],
        }

    def submit_finding(self, identity_id, phenomenon, status, basis):
        if status not in FINDING_STATUSES:
            return {"written": False, "reason": "判读状态只允许：疑似、确认、证据不足。"}
        if not isinstance(phenomenon, str) or not phenomenon.strip() or not isinstance(basis, str):
            return {"written": False, "reason": "现象须为非空文本，依据须为文本。"}
        if self.database.samefile(DEMO_DB):
            return {"written": False, "reason": "请使用演示库的副本提交判读。"}

        finding = {
            "id": f"F-{uuid4().hex}", "identity": identity_id,
            "phenomenon": phenomenon.strip(), "status": status, "basis": basis.strip(),
        }
        with closing(self._connect("rw")) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            # Reserve the write lock before reading the validation snapshot.
            db.execute("BEGIN IMMEDIATE")
            identity = db.execute(
                """
                SELECT i.status, o.evidence FROM Identity AS i
                JOIN Observation AS o ON o.id = i.observation
                WHERE i.id = ?
                """,
                (identity_id,),
            ).fetchone()
            if identity is None:
                return {"written": False, "reason": f"找不到可关联观测的身份记录 {identity_id}。"}

            graph = Graph()
            for table in RDF_FIELDS:
                for row in db.execute(f'SELECT * FROM "{table}"'):
                    _add_row(graph, table, dict(row))
            subject = _add_row(graph, "Finding", finding)
            conforms, report, report_text = validate(
                graph, shacl_graph=self.shapes, ont_graph=self.ontology,
            )
            if not conforms:
                reason = "SHACL 校验失败，详见校验报告。"
                confirmed_violation = any(
                    (result, SH.sourceShape, UUV.ConfirmedShape) in report
                    for result in report.subjects(SH.focusNode, subject)
                )
                if confirmed_violation:
                    if identity["status"] != "确认":
                        reason = f"{identity_id} 的身份状态为{identity['status']}，确认判读要求身份已确认。"
                    elif not identity["evidence"].strip() or not finding["basis"]:
                        reason = "确认判读要求非空证据索引和判读依据。"
                return {"written": False, "reason": reason, "validation_report": report_text}

            db.execute(
                """
                INSERT INTO Finding (id, identity, phenomenon, status, basis)
                VALUES (:id, :identity, :phenomenon, :status, :basis)
                """,
                finding,
            )
        return {"written": True, "finding_id": finding["id"], "validation": "SHACL 校验通过"}

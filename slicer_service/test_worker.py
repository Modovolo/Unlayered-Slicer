"""Exercise the private multipart boundary and child-process lifecycle."""
import asyncio
import json

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
import pytest

import app
from test_adapter import PROFILE, BASE

HEADERS = {"X-Service-Token": "fixture-token"}
PLANE = {"point": [110, 110, 5], "normal": [0, .15, 1], "enabled": True}


def payload():
    data = FormData()
    for name in ("first", "second"):
        data.add_field("meshes", b"fixture mesh", filename=name + ".stl")
    data.add_field("params", json.dumps({**BASE, "layer_height": .2}))
    data.add_field("printer_profile_config", json.dumps(PROFILE))
    data.add_field("project", json.dumps({"schema": 1, "revision": 7, "objects": [
        {"id": "first", "cutPlanes": [PLANE], "userOrdered": True},
        {"id": "second", "cutPlanes": []},
    ]}))
    return data


def setup(monkeypatch, tmp_path, gcode=";CHANGE_LAYER\nG1 X110 Y110 Z.2 E1\n"):
    binary = tmp_path / "engine"
    binary.write_text("#!/usr/bin/env python3\nimport time,sys,json\nfrom pathlib import Path\n"
                      "job=json.loads(Path(sys.argv[1]).read_text())\n"
                      "assert len(job['objects']) == 2\n"
                      "assert job['objects'][0]['userOrdered']\n"
                      "assert job['objects'][0]['cutPlanes'][0]['point'] == [110,110,5]\n"
                      "assert job['settings']['axis_selection'] == 'virtual'\n"
                      "assert job['settings']['print_sequence'] == 'by group'\n"
                      "assert all(Path(o['path']).read_bytes() == b'fixture mesh' for o in job['objects'])\n"
                      "time.sleep(.3)\nPath(sys.argv[2]).write_text(" + repr(gcode) + ")\n")
    binary.chmod(0o755)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(json.dumps({"version": app.VERSION, "features": ["planar", "multiplanar"]}))
    monkeypatch.setattr(app, "BIN", str(binary))
    monkeypatch.setattr(app, "WORK", tmp_path / "work")
    monkeypatch.setattr(app, "TOKEN", "fixture-token")
    monkeypatch.setattr(app, "ACCEPTANCE", acceptance)
    monkeypatch.setattr(app, "MAX_QUEUED", 2)
    app.jobs.clear()


async def terminal(client, job):
    for _ in range(80):
        result = await (await client.get("/api/slicer/job/" + job["job_id"], headers=HEADERS)).json()
        if result["status"] in {"complete", "error", "cancelled"}: return result
        await asyncio.sleep(.025)
    pytest.fail("Child process did not finish")


@pytest.mark.asyncio
async def test_exact_project_contract_queue_cancellation_and_private_download(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    async with TestClient(TestServer(app.create_app())) as client:
        assert (await client.post("/api/slicer/slice", data=payload())).status == 401
        first = await (await client.post("/api/slicer/slice", data=payload(), headers=HEADERS)).json()
        second = await (await client.post("/api/slicer/slice", data=payload(), headers=HEADERS)).json()
        assert (await client.post("/api/slicer/slice", data=payload(), headers=HEADERS)).status == 429
        assert (await client.post("/api/slicer/job/" + second["job_id"] + "/cancel", headers=HEADERS)).status == 200
        result = await terminal(client, first)
        assert result["status"] == "complete", result
        assert result["result"]["project_revision"] == 7
        assert result["result"]["layer_count"] == 1
        assert (await terminal(client, second))["status"] == "cancelled"
        assert app.jobs[second["job_id"]]["process"] is None
        assert (await client.get(result["gcode_url"])).status == 401
        assert (await client.get(result["gcode_url"], headers=HEADERS)).status == 200


@pytest.mark.asyncio
async def test_timeout_and_failed_upload_cleanup(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "TIMEOUT", .01)
    async with TestClient(TestServer(app.create_app())) as client:
        data = FormData(); data.add_field("meshes", b"mesh", filename="part.stl"); data.add_field("project", "bad json")
        assert (await client.post("/api/slicer/slice", data=data, headers=HEADERS)).status == 422
        assert list(app.WORK.iterdir()) == []
        job = await (await client.post("/api/slicer/slice", data=payload(), headers=HEADERS)).json()
        assert (await terminal(client, job))["status"] == "error"
        assert app.jobs[job["job_id"]]["process"].returncode is not None


@pytest.mark.asyncio
async def test_rotary_output_is_never_downloadable(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, "G01X110Y110A10E1\n")
    async with TestClient(TestServer(app.create_app())) as client:
        job = await (await client.post("/api/slicer/slice", data=payload(), headers=HEADERS)).json()
        result = await terminal(client, job)
        assert result["status"] == "error" and "Rotary" in result["error"]
        assert (await client.get("/api/slicer/gcode/" + job["job_id"] + ".gcode", headers=HEADERS)).status == 409

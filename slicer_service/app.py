"""Private, bounded HTTP worker for the display-free Fleet slicing entry point."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time
import uuid

from aiohttp import web
from adapter import VERSION, settings

BIN = os.getenv("UNLAYERED_BIN", "/opt/unlayered/bin/unlayered-slicer")
WORK = Path(os.getenv("UNLAYERED_WORK_DIR", "/work"))
PREFIX = "/api/slicer"
BASE_SOURCE = "https://github.com/Unlayered3D/Unlayered-Slicer/tree/v0.1.9"
SOURCE_REVISION = os.getenv("SOURCE_REVISION", "unpublished")
SOURCE = (os.getenv("SOURCE_REPOSITORY", "https://github.com/Modovolo/Unlayered-Slicer") + "/tree/" + SOURCE_REVISION
          if SOURCE_REVISION != "unpublished" else None)
TOKEN = os.getenv("SLICER_WORKER_TOKEN", "")
TTL = int(os.getenv("SLICER_JOB_TTL_SECONDS", "3600"))
TIMEOUT = int(os.getenv("SLICER_TIMEOUT_SECONDS", "1800"))
MAX_QUEUED = int(os.getenv("SLICER_MAX_QUEUED", "8"))
# Only the image acceptance command writes this file; an environment flag cannot
# declare a slicing feature tested.
ACCEPTANCE = Path(os.getenv("SLICER_ACCEPTANCE_FILE", "/opt/unlayered/acceptance.json"))
jobs = {}

def features():
    try:
        report = json.loads(ACCEPTANCE.read_text())
        return report["features"] if report.get("version") == VERSION else []
    except (OSError, ValueError, KeyError):
        return []

@web.middleware
async def auth(request, handler):
    if request.path.endswith("/health"):
        return await handler(request)
    if not TOKEN or not secrets.compare_digest(request.headers.get("X-Service-Token", ""), TOKEN):
        return web.json_response({"error": "Worker authentication required"}, status=401)
    try:
        return await handler(request)
    except (ValueError, TypeError, KeyError) as error:
        return web.json_response({"error": str(error)}, status=422)

async def health(request):
    ready = os.access(BIN, os.X_OK)
    return web.json_response({"status": "healthy" if ready else "unavailable", "engine": "unlayered", "version": VERSION,
                              "features": features(), "source": SOURCE, "source_revision": SOURCE_REVISION,
                              "base_source": BASE_SOURCE}, status=200 if ready else 503)

def response(job):
    result = {k: job[k] for k in ("job_id", "status", "progress", "message", "result", "error")}
    if job["status"] == "complete": result["gcode_url"] = f'{PREFIX}/gcode/{job["job_id"]}.gcode'
    return result

async def run(app, job, description):
    try:
        async with app["slot"]:
            if job["status"] == "cancelled": return
            job.update(status="processing", progress=10, message="Slicing with Unlayered")
            config = job["dir"] / "job.json"
            config.write_text(json.dumps(description))
            output = job["dir"] / "output.gcode"
            log = job["dir"] / "engine.log"
            with log.open("wb") as stream:
                process = await asyncio.create_subprocess_exec(BIN, str(config), str(output), stdout=stream, stderr=stream)
                job["process"] = process
                try:
                    await asyncio.wait_for(process.wait(), TIMEOUT)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
            if job["status"] == "cancelled": return
            if process.returncode or not output.is_file():
                raise ValueError(log.read_text(errors="replace")[-6000:] or "Unlayered produced no output")
            text = output.read_text(errors="replace")
            if re.search(r"^\s*G0?[0-3](?=\s|[XYZEFABC])[^;\n]*[ABC]\s*[-+\d.]", text, re.M | re.I):
                raise ValueError("Rotary-axis output is unsupported")
            warnings = []
            for line in log.read_text(errors="replace").splitlines():
                try:
                    packet = json.loads(line)
                    if packet.get("warning"): warnings.append(str(packet.get("message", ""))[:500])
                except ValueError:
                    if "warning" in line.lower(): warnings.append(line[:500])
            job.update(status="complete", progress=100, message="Slicing complete", result={
                "engine": "unlayered", "engine_version": VERSION, "project_revision": job["revision"],
                "layer_count": len(re.findall(r"^;\s*(?:LAYER_CHANGE|CHANGE_LAYER)\s*$", text, re.M)),
                "gcode_size": output.stat().st_size, "warnings": warnings[-20:], "source": SOURCE,
                "source_revision": SOURCE_REVISION, "base_source": BASE_SOURCE,
            })
            for pattern, key, scale in (
                (r"filament used \[mm\]\s*=\s*([0-9.]+)", "filament_used_m", .001),
                (r"filament used \[g\]\s*=\s*([0-9.]+)", "filament_weight_g", 1),
            ):
                match = re.search(pattern, text, re.I)
                if match: job["result"][key] = round(float(match[1]) * scale, 3)
            match = re.search(r"estimated printing time[^=]*=\s*([^\n]+)", text, re.I)
            if match: job["result"]["estimated_time_formatted"] = match[1].strip()
    except asyncio.CancelledError:
        job.update(status="cancelled", message="Slicing cancelled", progress=100)
        process = job.get("process")
        if process and process.returncode is None:
            process.kill()
            await process.wait()
    except Exception as error:
        job.update(status="error", progress=100, message=str(error), error=str(error))
    finally:
        job["updated"] = time.time()

async def create(request):
    async with request.app["admission"]:
        return await _create(request)

async def _create(request):
    if not os.access(BIN, os.X_OK): raise web.HTTPServiceUnavailable(text="Engine unavailable")
    if sum(j["status"] in {"queued", "processing"} for j in jobs.values()) >= MAX_QUEUED:
        return web.json_response({"error": "Slicer queue is full"}, status=429)
    directory = WORK / ("ul-" + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    try:
        payload, meshes = {}, []
        total_upload = 0
        async def read_field(field):
            nonlocal total_upload
            data = bytearray()
            while True:
                chunk = await field.read_chunk()
                if not chunk: break
                total_upload += len(chunk)
                if total_upload > 100 * 1024**2: raise web.HTTPRequestEntityTooLarge(max_size=100*1024**2, actual_size=total_upload)
                data.extend(chunk)
            return data
        async for field in await request.multipart():
            if field.name == "meshes":
                path = directory / f"mesh-{len(meshes)}.stl"
                if not (field.filename or "").lower().endswith(".stl"): raise ValueError("Only normalized STL meshes are accepted")
                path.write_bytes(await read_field(field))
                meshes.append(path)
            elif field.name in {"params", "printer_profile_config", "project"}:
                payload[field.name] = json.loads((await read_field(field)).decode())
        project = payload.get("project", {})
        objects = project.get("objects", [])
        if not meshes or len(meshes) != len(objects): raise ValueError("One mesh per project object is required")
        description = {"settings": settings(payload.get("params", {}), payload.get("printer_profile_config", {}), project, features()),
                       "objects": [{"path": str(path), "cutPlanes": obj.get("cutPlanes", []), "userOrdered": obj.get("userOrdered", False)} for obj, path in zip(objects, meshes)]}
        job = dict(job_id=directory.name, dir=directory, revision=project.get("revision", 0), status="queued", progress=0,
                   message="Queued", result=None, error=None, updated=time.time(), process=None)
        jobs[job["job_id"]] = job
        job["task"] = asyncio.create_task(run(request.app, job, description))
        return web.json_response(response(job), status=202)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

async def get_job(request):
    job = jobs.get(request.match_info["job_id"].removesuffix(".gcode"))
    if not job: raise web.HTTPNotFound()
    if request.path.endswith("/cancel"):
        if job["status"] in {"queued", "processing"}:
            job.update(status="cancelled", progress=100, message="Slicing cancelled")
            job["task"].cancel()
        return web.json_response(response(job))
    if "/gcode/" in request.path:
        if job["status"] != "complete": raise web.HTTPConflict()
        return web.FileResponse(job["dir"] / "output.gcode")
    return web.json_response(response(job))

async def lifecycle(app):
    WORK.mkdir(parents=True, exist_ok=True)
    app["slot"] = asyncio.Semaphore(1)
    app["admission"] = asyncio.Lock()
    async def clean():
        while True:
            await asyncio.sleep(60)
            for key, job in list(jobs.items()):
                if job["status"] not in {"queued", "processing"} and time.time() - job["updated"] > TTL:
                    jobs.pop(key)
                    shutil.rmtree(job["dir"], ignore_errors=True)
    task = asyncio.create_task(clean())
    yield
    task.cancel()
    for job in jobs.values(): job["task"].cancel()
    await asyncio.gather(task, *(j["task"] for j in jobs.values()), return_exceptions=True)

def create_app():
    app = web.Application(client_max_size=100 * 1024**2, middlewares=[auth])
    for prefix in ("", PREFIX):
        app.router.add_get(prefix + "/health", health)
        app.router.add_post(prefix + "/slice", create)
        app.router.add_get(prefix + "/job/{job_id}", get_job)
        app.router.add_post(prefix + "/job/{job_id}/cancel", get_job)
        app.router.add_get(prefix + "/gcode/{job_id}", get_job)
    app.cleanup_ctx.append(lifecycle)
    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8090")))

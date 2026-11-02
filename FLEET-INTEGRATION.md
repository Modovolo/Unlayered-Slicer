# Fleet headless slicing integration

Based on upstream v0.1.9 (`89e3033bf81cd892a8f2af819634aa2aa235e1cc`).
`origin` remains upstream; local integration work is on `fleet-slicer`.
Keep `main` aligned with upstream and merge reviewed upstream updates into
`fleet-slicer`. Fleet image CI runs on the integration branch, not upstream main.

The desktop CLI depends on GUI classes. `FLEET_HEADLESS=ON`, with `SLIC3R_GUI=OFF`
and `SLIC3R_CAD=OFF`, selects `src/fleet_slicer.cpp`: a small entry point over the
same `Model`/`Print` pipeline. It accepts a normalized job JSON and output path.
It does not load desktop presets, account services, scripts, or printer plugins.
Settings are mapped explicitly in `slicer_service/adapter.py`. Object meshes and
cut planes arrive in bed coordinates, with identity instance transforms.

## Build and verify

```sh
docker build --target deps -f Dockerfile.slicer -t fleet-unlayered-deps:local .
docker build --target engine -f Dockerfile.slicer \
  --build-arg DEPS_IMAGE=fleet-unlayered-deps:local -t fleet-unlayered-engine:local .
docker build -f Dockerfile.slicer \
  --build-arg ENGINE_IMAGE=fleet-unlayered-engine:local -t fleet-unlayered:local .
python3 -m pytest slicer_service/test_adapter.py
```

The engine build runs upstream curved-top and cut-plane library tests through
`FleetSlicerTests`. The runtime build runs actual planar, curved-top and tilted
cut-plane jobs. Planar failure fails the build. Advanced failures are recorded
in `/opt/unlayered/acceptance.json` and remain unavailable to callers. HTTP
discovery returns only capabilities demonstrated by this image's acceptance
tests. The adapter rejects unsupported features and missing printer limits.

Dependency installation is saved separately from source and wrapper changes.
Local BuildKit mounts preserve dependency builds across failed attempts. The CI
workflow additionally retains compiler caches and publishes dependency, engine,
and service images separately. Keep build jobs bounded; the default is two.
Use a dependency-cache key that changes with compiler/architecture/dependency
inputs, as the workflow does. The runtime image omits the compiler and GUI.

Dependency and engine compilation print a progress sample every 30 seconds:
elapsed time, compiled object count and recent compiler output. Complete logs
remain at `/src/deps/build/fleet-build.log` and `/src/build/fleet-engine.log`
inside their respective build stages. A long first build can still be expensive;
silence after CMake configuration in older images was caused by redirected logs,
not evidence that compilation had stopped. Headless dependency builds now forward
their nested Ninja output, and the expected source-archive Git probe stays quiet.
Source-archive downloads time out after two minutes without data (30 minutes in
total) and are retried, so a stalled mirror fails visibly instead of hanging the
step. Downloaded archives are kept in a BuildKit cache mount, which a reused
builder keeps; a fresh GitHub runner starts without it.

CI restores the compiler cache explicitly, then extracts and saves it with
`always()` cleanup steps after failure or cancellation when the runner survives.
A forcibly terminated runner may still lose unsaved work. This retains compiler
outputs; a failed cold build still needs dependency configuration and installation
on the next fresh runner. Published dependency images avoid that work after the
first successful dependency build. No advanced engine acceptance gate is skipped.

The `fleet-slicer.yml` workflow is restricted to the Modovolo organization and
publishes immutable SHA tags at `jmarji/unlayered-slicer` on Docker Hub. It
requires an owned fork and a `DOCKERHUB_TOKEN` secret with write access to
`jmarji`. Dependency and engine tags use 24-character prefixes of their input
hashes to stay within Docker's 128-character tag limit. It does not deploy or
print anything.

## Private worker contract

Run the service with `SLICER_WORKER_TOKEN` supplied from a Secret. Only `/health`
is unauthenticated; every other request requires `X-Service-Token`. Expose the
worker only inside the cluster. Fleet Manager owns user authorization.

The existing multipart contract is retained at `/api/slicer/slice`: `meshes`
(one normalized STL per object), JSON `params`, JSON `printer_profile_config`,
and JSON `project`. Project shape: `{schema: 1, revision, objects: [{id,
cutPlanes: [{point: [x,y,z], normal: [x,y,z], enabled}], userOrdered}]}`.
Files and objects have matching order. No automatic arrangement occurs.

Polling, cancellation and downloads use `/api/slicer/job/{id}`,
`/api/slicer/job/{id}/cancel`, and `/api/slicer/gcode/{id}.gcode`.
One process runs at a time, with eight admitted jobs by default, a 30-minute
timeout, a 100 MB upload limit and one-hour terminal-job retention. A restart
loses worker files; Fleet Manager reports the persisted public job as interrupted.

The service and entry point are covered by this repository's AGPL license.
Publish the actual modified corresponding source alongside distributed images;
the upstream tag alone does not include these integration changes.

import subprocess
import sys
import time
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/fleet_build_progress.py"


def test_reports_progress_before_build_finishes_and_keeps_full_log(tmp_path):
    log = tmp_path / "build.log"
    child = "import time; from pathlib import Path; print('compiling fixture', flush=True); Path('part.o').touch(); time.sleep(.2)"
    result = subprocess.run([sys.executable, str(SCRIPT), "--log", str(log), "--label", "fixture",
                             "--interval", ".05", "--objects", str(tmp_path), "--", sys.executable, "-c", child],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "running; elapsed" in result.stdout
    assert "compiled object files: 1" in result.stdout
    assert "compiling fixture" in result.stdout
    assert result.stdout.index("running;") < result.stdout.index("finished;")
    assert "compiling fixture" in log.read_text()


def test_propagates_native_build_failure_with_error_output(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), "--log", str(tmp_path / "build.log"), "--label", "failure",
                             "--", sys.executable, "-c", "import sys; print('native compile error', file=sys.stderr); sys.exit(7)"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 7
    assert "native compile error" in result.stdout
    assert "exit=7" in result.stdout


def test_cancellation_stops_the_build_child(tmp_path):
    stopped = tmp_path / "child-stopped"
    ready = tmp_path / "child-ready"
    child = ("import signal,time; from pathlib import Path; "
             f"signal.signal(signal.SIGTERM, lambda *_: (Path({str(stopped)!r}).touch(), exit(0))); "
             f"Path({str(ready)!r}).touch(); time.sleep(60)")
    process = subprocess.Popen([sys.executable, str(SCRIPT), "--log", str(tmp_path / "build.log"),
                                "--label", "cancel", "--", sys.executable, "-c", child],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        process.terminate()
        assert process.wait(timeout=5) == 143
        assert stopped.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)

"""Starting the app for real, as its own process: one copy at a time, a second start hands its file to
the first, and it starts without a console the way pythonw.exe starts it (SYS-1, OPEN-1, NFR-6)."""
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import make_saved

APP = Path(__file__).resolve().parents[1]


def call(args, env, timeout=60):
    return subprocess.run([sys.executable, "-m", "treeprof_app", *args], cwd=str(APP), env=env, timeout=timeout,
                          capture_output=True, text=True)


def test_one_copy_at_a_time_and_no_console_needed(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env.update(QT_QPA_PLATFORM="offscreen", TREEPROF_INSTANCE=f"treeprof-test-{os.getpid()}-{tmp_path.name}",
               PYTHONDONTWRITEBYTECODE="1")
    home = tmp_path / "home"
    project = make_saved(tmp_path / "saved", 5)
    assert call(["--check-closed", "--home", str(home)], env).returncode == 0            # nothing is open yet

    # the way the Desktop shortcut starts it: launch.pyw with pythonw.exe, which has nowhere to print to
    code = ("import sys, runpy; sys.stdout = None; sys.stderr = None; "
            f"sys.argv = ['launch.pyw', '--home', r'{home}']; runpy.run_path('launch.pyw', run_name='__main__')")
    first = subprocess.Popen([sys.executable, "-c", code], cwd=str(APP), env=env)
    try:
        deadline = time.time() + 60
        while call(["--check-closed", "--home", str(home)], env).returncode != 1:
            assert first.poll() is None, "the app stopped right after starting"
            assert time.time() < deadline, "the app did not open"
            time.sleep(0.3)
        log = home / "state" / "app.log"
        assert "Tree Profiling" in log.read_text(encoding="utf-8")

        second = call([str(project), "--home", str(home)], env)                         # a double-clicked .treeprof
        assert second.returncode == 0 and first.poll() is None
        deadline = time.time() + 20
        while f"Asked to open {project}" not in log.read_text(encoding="utf-8"):
            assert time.time() < deadline, log.read_text(encoding="utf-8")
            time.sleep(0.2)
        text = log.read_text(encoding="utf-8")
        assert "Traceback" not in text, text
    finally:
        first.kill()
        first.wait(30)
    assert call(["--check-closed", "--home", str(home)], env).returncode == 0

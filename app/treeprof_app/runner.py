"""Run one run from start to end: copy the file, call the three scripts, read their progress, and
write the .treeprof file. Handles Cancel, Try again and Resume (RUN-1 to RUN-9, SYS-4, SYS-5,
spec 6.1 to 6.8). The scripts in ../processes are called as they are, never changed.

The runner does not know about windows or buttons: it reports what happens through on_event, and
the user interface shows it.
"""
from __future__ import annotations

import collections
import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from . import __version__, filecopy, lasheader, projectfile, progress, steps, store as st, summary
from .naming import safe_name
from .winproc import hidden

SAT_IMAGE = "ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0"       # the image sat_docker.py uses
_COPY_FAILED = progress.Failure(
    "copy_failed", "Could not copy the file. Check the connection to the shared drive, then try again.",
    (progress.RETRY, progress.DETAILS))
_UNREADABLE = progress.Failure(
    "unreadable_input", "This file cannot be read as a LAS or LAZ point cloud.", (progress.DETAILS,))


def default_needs_copy(path) -> bool:
    """Spec 6.8: copy unless the file is on a local fixed disk outside OneDrive."""
    s = str(path)
    if "onedrive" in s.lower() or s.startswith("\\\\") or s.startswith("//"):
        return True
    if os.name == "nt":
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(s))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(drive) != 3        # 3 = DRIVE_FIXED
    return False


def stop_sat_containers(image: str = SAT_IMAGE, docker: str = "docker") -> int:
    """Spec 6.6: stop every running container of the SegmentAnyTree image. Returns how many."""
    try:
        r = subprocess.run([docker, "ps", "-q", "--filter", f"ancestor={image}"], capture_output=True, text=True,
                           timeout=30, **hidden())
        ids = r.stdout.split()
        if ids:
            subprocess.run([docker, "stop", *ids], capture_output=True, timeout=120, **hidden())
        return len(ids)
    except (OSError, subprocess.SubprocessError):
        return 0


@contextlib.contextmanager
def keep_awake():
    """SYS-4: Windows does not go to sleep while a run is going."""
    if os.name != "nt":
        yield
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=60, **hidden())
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


@dataclass
class RunnerConfig:
    python: Union[str, Sequence[str]]                 # interpreter for the scripts
    scripts_dir: Path                                 # the processes folder
    app_version: str = __version__
    scripts_commit: Optional[str] = None
    machine: Optional[str] = None
    sat_image: str = SAT_IMAGE
    needs_copy: Callable = default_needs_copy
    stop_containers: Optional[Callable[[], object]] = None       # default: stop_sat_containers(sat_image)
    prepare_view: Optional[Callable[[dict], dict]] = None        # builds the 3D data; returns e.g. {"area_ha": 1.2}
    extra_env: dict = field(default_factory=dict)
    copy_retry_wait: float = 10.0
    copy_chunk: int = filecopy.CHUNK
    log_tail_lines: int = 400


class _Cancelled(Exception):
    pass


class _StepFailed(Exception):
    def __init__(self, failure: progress.Failure):
        super().__init__(failure.message)
        self.failure = failure


class Runner:
    """Runs one run at a time (one graphics card). run() blocks; call it from a worker thread and
    call cancel() from the user interface."""

    def __init__(self, store: st.Store, config: RunnerConfig, on_event: Optional[Callable[[dict], None]] = None):
        self.store, self.config, self.on_event = store, config, on_event
        self._cancel = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.current_run: Optional[str] = None

    # ------------------------------------------------------------------ public
    def run_queue(self) -> list:
        """Run every waiting run, oldest first. A run that fails does not stop the next (RUN-9)."""
        done = []
        while True:
            nxt = self.store.next_queued()
            if nxt is None:
                return done
            done.append((nxt["id"], self.run(nxt["id"])))

    def cancel(self) -> None:
        """RUN-5: stop the current run. Safe to call from another thread."""
        self._cancel.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None:
            kill_process_tree(proc)

    def resume(self, run_id: str, max_points: Optional[int] = None) -> None:
        """Try again / Resume: back in the queue; finished steps are not run again."""
        self.store.set_status(run_id, st.QUEUED)
        if max_points:
            self.store.update(run_id, max_points=int(max_points))

    def retry_with_smaller_tiles(self, run_id: str) -> int:
        """RUN-8: try again with half the points per tile. Returns the new value."""
        new = max(self.store.get(run_id)["max_points"] // 2, 1_000_000)
        self.resume(run_id, max_points=new)
        return new

    def run(self, run_id: str) -> str:
        """Run or resume one run until it is finished, failed or cancelled. Returns the status."""
        store = self.store
        self._cancel.clear()
        run = store.get(run_id)
        store.set_status(run_id, st.RUNNING, started_at=run["started_at"] or st.now())
        self.current_run = run_id
        self._emit(run_id, "status", status=st.RUNNING)
        try:
            with keep_awake():
                for step in steps.STEP_ORDER:
                    if step in store.steps_done(run_id):
                        continue
                    if self._cancel.is_set():
                        raise _Cancelled()
                    self._emit(run_id, "step", step=step, state="running", label=steps.STEP_LABELS[step])
                    t0 = time.time()
                    info = self._run_step(run_id, step)
                    store.step_done(run_id, step, time.time() - t0, info)
                    self._emit(run_id, "step", step=step, state="done", seconds=time.time() - t0)
                self._finish(run_id)
            status = st.FINISHED
        except _Cancelled:
            self._stop_containers()
            store.set_status(run_id, st.CANCELLED, finished_at=st.now())
            status = st.CANCELLED
        except _StepFailed as e:
            status = self._failed(run_id, e.failure)
        except Exception as e:                                   # a bug in the app must not lose the run
            status = self._failed(run_id, progress.Failure(
                "unknown", "Something went wrong in the app.", (progress.RETRY, progress.DETAILS, progress.SEND_LOG),
                f"{type(e).__name__}: {e}"))
        finally:
            self.current_run = None
        self._emit(run_id, "status", status=status)
        return status

    # ------------------------------------------------------------------ steps
    def _run_step(self, run_id: str, step: str) -> dict:
        if step == "copy":
            return self._copy(run_id)
        if step in steps.SCRIPT_STEPS:
            return self._script(run_id, step)
        return self._prepare_view(run_id)

    def _copy(self, run_id: str) -> dict:
        run = self.store.get(run_id)
        src = Path(run["input_path"])
        info = {"copied": False}
        local = src
        if self.config.needs_copy(src):
            local = Path(run["workdir"]) / "input" / src.name
            try:
                result = filecopy.copy_with_hash(
                    src, local, cancel=self._cancel.is_set, wait_seconds=self.config.copy_retry_wait,
                    chunk_size=self.config.copy_chunk,
                    progress=lambda done, total: self._emit(run_id, "progress", step="copy", done=done, total=total,
                                                            fraction=done / total if total else 1.0))
            except filecopy.CopyCancelled:
                raise _Cancelled() from None
            except filecopy.CopyError as e:
                raise _StepFailed(_with_detail(_COPY_FAILED, str(e))) from None
            info = {"copied": True, "bytes": result.bytes, "attempts": result.attempts}
            self.store.update(run_id, sha256=result.sha256)
        try:
            points = lasheader.point_count(local)
        except lasheader.LasHeaderError as e:
            raise _StepFailed(_with_detail(_UNREADABLE, str(e))) from None
        self.store.update(run_id, local_input=str(local), input_points=points)
        return info

    def _script(self, run_id: str, step: str) -> dict:
        run = self.store.get(run_id)
        workdir = Path(run["workdir"])
        (workdir / "log").mkdir(parents=True, exist_ok=True)
        cmd = steps.command(step, python=self.config.python, workdir=workdir, input_path=run["local_input"],
                            site=run["site"], max_points=run["max_points"])
        env = steps.child_env()
        env.update(self.config.extra_env)
        parser = progress.parser_for(step)
        tail = collections.deque(maxlen=self.config.log_tail_lines)
        kwargs = hidden(subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else {"start_new_session": True}
        with open(workdir / "log" / f"{step}.log", "a", encoding="utf-8") as log:
            log.write(f"\n===== {st.now()}  $ {' '.join(map(str, cmd))}\n")
            log.flush()
            try:
                proc = subprocess.Popen(cmd, cwd=str(self.config.scripts_dir), env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                        bufsize=1, **kwargs)
            except OSError as e:
                raise _StepFailed(progress.Failure(
                    "unknown", f"Something went wrong in step {steps.STEP_LABELS[step]}.",
                    (progress.RETRY, progress.DETAILS, progress.SEND_LOG), f"Cannot start {cmd[0]}: {e}")) from None
            with self._proc_lock:
                self._proc = proc
            try:
                if self._cancel.is_set():                        # Cancel arrived while the script was starting
                    kill_process_tree(proc)
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    tail.append(line.rstrip("\r\n"))
                    self._emit(run_id, "log", step=step, line=line.rstrip("\r\n"))
                    if parser.feed(line):
                        self._emit_progress(run_id, step, parser)
                code = proc.wait()
            finally:
                with self._proc_lock:
                    self._proc = None
                if proc.poll() is None:
                    kill_process_tree(proc)
                proc.stdout.close()
        if self._cancel.is_set():
            raise _Cancelled()
        check = steps.check_step_done(step, workdir, code, self.store.get(run_id)["input_points"])
        if not check.done:
            failure = progress.classify_failure(step, tail)
            if failure.kind == "unknown":
                failure = _with_detail(failure, f"{check.reason}\n{failure.detail}".strip())
            raise _StepFailed(failure)
        return _parser_info(parser)

    def _prepare_view(self, run_id: str) -> dict:
        """Spec 6.7. A failure here does not fail the run: only the 3D view is missing."""
        if self.config.prepare_view is None:
            return {"available": False, "reason": "not built yet"}
        try:
            info = dict(self.config.prepare_view(self.store.get(run_id)) or {})
            info.setdefault("available", True)
            return info
        except Exception as e:
            return {"available": False, "reason": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------------ endings
    def _finish(self, run_id: str) -> None:
        run = self.store.get(run_id)
        workdir = Path(run["workdir"])
        done = self.store.steps_done(run_id)
        trees = summary.load_trees(workdir / steps.TREES_CSV)
        area = done.get("prepare_view", {}).get("info", {}).get("area_ha")
        sat, join, measure = (done.get(s, {}).get("info", {}) for s in steps.SCRIPT_STEPS)
        finished = st.now()
        data = projectfile.new_project(
            site_name=run["site"], run_by=run["run_by"], app_version=self.config.app_version,
            capture_date=run["capture_date"], run_id=run_id, started_at=run["started_at"], finished_at=finished,
            machine=self.config.machine,
            files={"trees_csv": steps.TREES_CSV, "crowns_shp": steps.CROWNS_SHP, "info_laz": steps.INFO_LAZ,
                   "sat_merged_laz": steps.SAT_MERGED, "sat_raw_laz": steps.SAT_RAW},
            input={"path": run["input_path"], "size_bytes": run["input_size"], "sha256": run["sha256"],
                   "points": run["input_points"], "ground_source": measure.get("ground_source"),
                   "crs": _crs_name(run, workdir)},
            pipeline={"scripts_commit": self.config.scripts_commit, "sat_image": self.config.sat_image,
                      "max_points": run["max_points"],
                      "tiles": {"count": sat.get("tiles_total"), "thinned": len(sat.get("tiles_thinned", []))},
                      "trees_before_merge": join.get("trees_before"), "trees_after_merge": join.get("trees_after"),
                      "step_seconds": {s: round(done[s]["seconds"], 1) for s in steps.STEP_ORDER if s in done}},
            summary=summary.summarise(trees, area))
        path = projectfile.write_project(workdir / (safe_name(run["site"]) + projectfile.EXTENSION), data)
        self._remove_input_copy(run)
        self.store.update(run_id, trees=len(trees), project_path=str(path))
        self.store.set_status(run_id, st.FINISHED, finished_at=finished)

    def _failed(self, run_id: str, failure: progress.Failure) -> str:
        if failure.is_empty_result:                               # RES-10: no trees is a result, not an error
            self._remove_input_copy(self.store.get(run_id))
            self.store.update(run_id, trees=0)
            self.store.set_status(run_id, st.FINISHED, finished_at=st.now())
            return st.FINISHED
        self.store.set_status(run_id, st.FAILED, finished_at=st.now(), error_kind=failure.kind,
                              error_message=failure.message, error_detail=failure.detail)
        self._emit(run_id, "failure", kind=failure.kind, message=failure.message, actions=list(failure.actions))
        return st.FAILED

    def _remove_input_copy(self, run: dict) -> None:
        """STO-1: the copy of the input is a temporary file. The original is never touched."""
        local = run.get("local_input")
        if not local:
            return
        local, inputs = Path(local), Path(run["workdir"]) / "input"
        if local.parent == inputs:
            for p in (local, inputs):
                try:
                    p.unlink() if p.is_file() else p.rmdir()
                except OSError:
                    pass

    def _stop_containers(self) -> None:
        try:
            if self.config.stop_containers is not None:
                self.config.stop_containers()
            else:
                stop_sat_containers(self.config.sat_image)
        except Exception:
            pass

    # ------------------------------------------------------------------ events
    def _emit(self, run_id: str, event_type: str, /, **data) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, "run_id": run_id, **data})
        except Exception:
            pass                                                  # a display problem must not stop a run

    def _emit_progress(self, run_id: str, step: str, parser) -> None:
        if isinstance(parser, progress.SatParser):
            s = parser.state
            self._emit(run_id, "progress", step=step, label=s.label, fraction=s.fraction, eta_minutes=s.eta_minutes,
                       tile=s.tile_current, tiles_total=s.tiles_total, tiles_done=s.tiles_done)
        else:
            self._emit(run_id, "progress", step=step, label=steps.STEP_LABELS[step], fraction=None, **parser.info)


def _with_detail(failure: progress.Failure, detail: str) -> progress.Failure:
    return progress.Failure(failure.kind, failure.message, failure.actions, detail)


def _parser_info(parser) -> dict:
    if isinstance(parser, progress.SatParser):
        s = parser.state
        return {"tiles_total": s.tiles_total, "tiles_skipped": sorted(s.tiles_skipped),
                "tiles_thinned": sorted(s.tiles_thinned), "tile_minutes": s.tile_minutes, "trees": s.trees,
                "minutes": s.minutes}
    return dict(parser.info)


def _crs_name(run: dict, workdir: Path):
    """Name of the coordinate system for Run info (RES-8): from the input copy, else from a result."""
    from . import checks
    for path in (run.get("local_input"), workdir / steps.INFO_LAZ, workdir / steps.SAT_RAW):
        if path and Path(path).is_file():
            name, _ = checks.read_crs(path)
            if name and name != "?":
                return name
    return None

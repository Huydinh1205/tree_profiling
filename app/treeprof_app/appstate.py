"""Everything the screens need, without any screen code: the queue worker, what each run is doing
right now, opening results, saving, deleting local copies. The user interface (ui.py) only shows
what this module reports and calls its methods when a button is pressed.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import checks, crowns, naming, projectfile, saving, steps, store as st, summary, viewdata
from .runner import Runner, RunnerConfig
from .settings import Settings, default_home
from .winproc import hidden


def _git_commit(folder) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(folder), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           timeout=10, **hidden())
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class LiveRun:
    """What a run is doing right now (for the Running screen)."""

    def __init__(self):
        self.steps = {s: "waiting" for s in steps.STEP_ORDER}
        self.seconds = {}
        self.started = {}
        self.label = ""
        self.fraction = None
        self.eta_minutes = None
        self.copy = None                       # (bytes done, bytes total)
        self.log = collections.deque(maxlen=300)
        self.log_total = 0                     # lines printed so far (the deque only keeps the last ones)
        self.failure = None


class Results:
    """Opened results: from a run's working folder or from a saved folder (OPEN-1 to OPEN-5)."""

    def __init__(self, project_path):
        self.project_path = Path(project_path)
        self.project = projectfile.read_project(self.project_path)
        self.missing_optional = projectfile.check_openable(self.project_path, self.project)
        self.paths = projectfile.result_paths(self.project_path, self.project)
        self.trees = summary.load_trees(self.paths["trees_csv"])
        self.by_id = {t.get("tree_id"): t for t in self.trees}
        info = self.paths.get("info_laz")
        self.info_laz = info if info and info.is_file() else None
        self.summary = self.project.get("summary") or summary.summarise(self.trees)
        if self.summary.get("trees") != len(self.trees):
            self.summary = summary.summarise(self.trees, self.summary.get("area_ha"))
        self._crowns = None

    def crown_outlines(self) -> dict:
        """tree_id -> outline [(x, y), ...] from the crown shapefile; {} when it cannot be read (the
        map then draws a circle per tree)."""
        if self._crowns is None:
            try:
                self._crowns = crowns.read_crowns(self.paths["crowns_shp"])
            except (crowns.CrownsError, KeyError, TypeError):
                self._crowns = {}
        return self._crowns

    @property
    def folder(self) -> Path:
        return self.project_path.parent


class AppState:
    def __init__(self, home=None, *, runner_config: Optional[RunnerConfig] = None, autostart: bool = True,
                 machine_runner: Optional[Callable] = None):
        self.home = Path(home) if home else default_home()
        self.settings = Settings.load(self.home)
        self.store = st.Store(self.home / "state" / "runs.sqlite")
        self.interrupted = self.store.mark_interrupted()                    # SYS-5
        cfg = runner_config or RunnerConfig(
            python=self.settings.script_python, scripts_dir=Path(self.settings.scripts_dir),
            machine=platform.node() or None, scripts_commit=_git_commit(self.settings.scripts_dir))
        if cfg.prepare_view is None:
            cfg.prepare_view = self._prepare_view
        self.runner = Runner(self.store, cfg, self._on_event)
        self.live = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = False
        self.closed = False                    # True once shutdown() was called: the screens stop asking
        self._machine_runner = machine_runner
        self._machine = None
        self._machine_at = 0.0
        self._views = {}                       # view file path -> TreePoints
        self._building = set()
        self._view_failed = {}
        self.opened = {}                       # token -> path of a .treeprof file opened from a saved folder
        self.save_progress = {}                # run id / project path -> dict shown by the Save dialog
        self.worker = threading.Thread(target=self._loop, name="treeprof-worker", daemon=True)
        if autostart:
            self.worker.start()

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while not self._stop:
            self._wake.wait(1.0)
            self._wake.clear()
            if self._stop:
                break
            try:
                self.runner.run_queue()
            except Exception:
                time.sleep(1.0)

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop = True
        self.runner.cancel()
        self._wake.set()
        if self.worker.is_alive():
            self.worker.join(30)
        self.store.close()

    def _on_event(self, e: dict) -> None:
        with self._lock:
            live = self.live.setdefault(e["run_id"], LiveRun())
            kind = e["type"]
            if kind == "status" and e["status"] == st.RUNNING:
                live.failure, live.label, live.fraction, live.eta_minutes = None, "", None, None
                done = self.store.steps_done(e["run_id"])
                for s in steps.STEP_ORDER:
                    live.steps[s] = "done" if s in done else "waiting"
                    if s in done:
                        live.seconds[s] = done[s]["seconds"]
            elif kind == "step":
                if e["state"] == "running":
                    live.steps[e["step"]] = "running"
                    live.started[e["step"]] = time.time()
                    live.label, live.fraction, live.eta_minutes, live.copy = "", None, None, None
                else:
                    live.steps[e["step"]] = "done"
                    live.seconds[e["step"]] = e.get("seconds", 0.0)
            elif kind == "progress":
                if e.get("step") == "copy":
                    live.copy = (e["done"], e["total"])
                    live.fraction = e.get("fraction")
                else:
                    live.label = e.get("label") or ""
                    live.fraction = e.get("fraction")
                    live.eta_minutes = e.get("eta_minutes")
            elif kind == "log":
                live.log.append(e["line"])
                live.log_total += 1
            elif kind == "failure":
                live.failure = {"kind": e["kind"], "message": e["message"], "actions": e["actions"]}

    # ------------------------------------------------------------------ runs
    def add_run(self, input_path, *, site: str, run_by: str, capture_date: Optional[str] = None,
                max_points: int = steps.DEFAULT_MAX_POINTS) -> str:
        """NEW-7, NEW-8: put a file in the queue. Returns the run id."""
        input_path = Path(input_path)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = naming.safe_name(site).replace(" ", "_")
        workdir = Path(self.settings.runs_root) / f"{base}_{stamp}"
        n = 2
        while workdir.exists():
            workdir = Path(self.settings.runs_root) / f"{base}_{stamp}_{n}"
            n += 1
        workdir.mkdir(parents=True)
        size = mtime = None
        try:
            stat = input_path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            pass
        run_id = self.store.add_run(site=site, run_by=run_by, input_path=input_path, workdir=workdir,
                                    capture_date=capture_date, max_points=max_points, input_size=size,
                                    input_mtime=mtime)
        self.settings.last_browse_dir = str(input_path.parent)
        self.settings.save()
        self._wake.set()
        return run_id

    def cancel(self, run_id: str) -> None:
        """RUN-5 and RUN-9: stop the run that is going, or take a waiting run out of the queue."""
        run = self.store.get(run_id)
        if run["status"] == st.RUNNING and self.runner.current_run == run_id:
            self.runner.cancel()
        elif run["status"] == st.QUEUED:
            self.store.set_status(run_id, st.CANCELLED, finished_at=st.now())

    def resume(self, run_id: str) -> None:
        self.runner.resume(run_id)
        self._wake.set()

    def retry_smaller(self, run_id: str) -> int:
        new = self.runner.retry_with_smaller_tiles(run_id)
        self._wake.set()
        return new

    def delete_run(self, run_id: str) -> None:
        """Remove a run that is not going: its record and its working folder."""
        run = self.store.get(run_id)
        self.store.delete_run(run_id)
        shutil.rmtree(run["workdir"], ignore_errors=True)
        self.live.pop(run_id, None)

    def run_view(self, run_id: str) -> dict:
        """One run with everything the screens show about it."""
        run = self.store.get(run_id)
        with self._lock:
            live = self.live.get(run_id)
        saves = self.store.saves(run_id)
        run["saves"] = saves
        run["saved"] = bool(saves)
        run["saved_folder"] = saves[-1]["folder"] if saves else None
        run["local_exists"] = Path(run["workdir"]).is_dir()
        run["live"] = live
        run["steps_done"] = self.store.steps_done(run_id)
        return run

    def runs(self) -> list:
        return [self.run_view(r["id"]) for r in self.store.list_runs()]

    def current(self) -> Optional[dict]:
        run_id = self.runner.current_run
        return self.run_view(run_id) if run_id else None

    def log_tail(self, run_id: str, lines: int = 200) -> list:
        """The last lines the scripts printed for this run (from memory, or from the log files after a restart)."""
        with self._lock:
            live = self.live.get(run_id)
            if live and live.log:
                return list(live.log)[-lines:]
        folder = Path(self.store.get(run_id)["workdir"]) / "log"
        logs = sorted((p for p in folder.glob("*.log") if p.name != "sat_docker.log"), key=lambda p: p.stat().st_mtime) \
            if folder.is_dir() else []
        if not logs:
            return []
        try:
            return logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        except OSError:
            return []

    def estimate_minutes(self, points: Optional[int]) -> Optional[float]:
        """NEW-6: time a run of this many points should take, from the runs finished on this computer."""
        if not points:
            return None
        rates = []
        for run in self.store.list_runs():
            if run["status"] != st.FINISHED or not run["input_points"]:
                continue
            done = self.store.steps_done(run["id"])
            seconds = sum(done[s]["seconds"] for s in steps.SCRIPT_STEPS if s in done)
            if seconds > 0:
                rates.append(seconds / 60 / (run["input_points"] / 1e6))
        if not rates:
            return None
        rates.sort()
        return rates[len(rates) // 2] * points / 1e6

    def register_opened(self, project_path) -> str:
        """Remember a .treeprof file the user opened; returns the token used in the page address."""
        token = hashlib.sha1(str(project_path).encode("utf-8")).hexdigest()[:12]
        self.opened[token] = str(project_path)
        return token

    # ------------------------------------------------------------------ checks
    def machine(self, refresh: bool = False) -> list:
        """HOME-3 (kept for a minute, because asking Docker takes a moment)."""
        if refresh or self._machine is None or time.time() - self._machine_at > 60:
            kw = {"run": self._machine_runner} if self._machine_runner else {}
            self._machine = checks.machine_checks(runs_root=self.settings.runs_root,
                                                  shared_root=self.settings.shared_root,
                                                  warn_free_gb=self.settings.warn_free_gb, **kw)
            self._machine_at = time.time()
        return self._machine

    def check_file(self, path) -> checks.FileReport:
        path = Path(path)
        done = False
        try:
            stat = path.stat()
            done = self.store.finished_run_for(path, stat.st_size, stat.st_mtime) is not None
        except OSError:
            pass
        return checks.file_checks(path, runs_root=self.settings.runs_root, warn_free_gb=self.settings.warn_free_gb,
                                  machine=self.machine(), already_finished=done)

    # ------------------------------------------------------------------ results
    def project_path_for(self, run_id: str) -> Optional[Path]:
        """The .treeprof file of a finished run: in its working folder, or where it was saved (STO-4)."""
        run = self.store.get(run_id)
        if run["project_path"] and Path(run["project_path"]).is_file():
            return Path(run["project_path"])
        for save in reversed(self.store.saves(run_id)):
            try:
                return projectfile.find_in_folder(save["folder"])
            except projectfile.ProjectFileError:
                continue
        return None

    def open_results(self, project_path) -> Results:
        return Results(project_path)

    # ------------------------------------------------------------------ opened lately (HOME-5)
    RECENT_KEPT = 15

    def remember_opened(self, results: Results) -> bool:
        """Put saved results that were just opened at the top of the 'Opened recently' list. Results of a
        run that is on Home anyway are not listed twice. Returns True when the list changed."""
        run_id = results.project.get("run_id")
        if run_id:
            try:
                self.store.get(run_id)
                return False
            except KeyError:
                pass
        path = str(results.project_path)
        entry = {"path": path, "site": results.project.get("site_name") or results.project_path.stem,
                 "capture_date": results.project.get("capture_date"), "trees": len(results.trees), "opened_at": st.now()}
        self.settings.recent = ([entry] + [r for r in self.settings.recent if r.get("path") != path])[:self.RECENT_KEPT]
        self.settings.save()
        return True

    def recent(self) -> list:
        """The list, each entry with "exists" (the .treeprof file is still where it was)."""
        out = []
        for r in self.settings.recent:
            try:
                exists = Path(r["path"]).is_file()
            except (OSError, KeyError, TypeError):
                exists = False
            out.append({**r, "exists": exists})
        return out

    def forget_opened(self, path: str) -> None:
        self.settings.recent = [r for r in self.settings.recent if r.get("path") != path]
        self.settings.save()

    def _view_path(self, results: Results) -> Optional[Path]:
        if results.info_laz is None:
            return None
        local = results.folder / viewdata.CACHE_NAME
        if local.is_file():
            return local
        stat = results.info_laz.stat()
        key = hashlib.sha1(f"{results.info_laz}|{stat.st_size}|{stat.st_mtime}".encode()).hexdigest()[:16]
        return self.home / "state" / "viewcache" / f"{key}.npz"

    def tree_points(self, results: Results):
        """("ready", TreePoints) | ("preparing", None) | ("unavailable", reason).  OPEN-4, OPEN-5."""
        path = self._view_path(results)
        if path is None:
            return "unavailable", "the coloured point cloud (_info.laz) is not in this folder"
        key = str(path)
        if key in self._views:
            return "ready", self._views[key]
        if path.is_file():
            view = viewdata.open_view(path)
            if view is not None:
                self._views[key] = view
                return "ready", view
        with self._lock:
            if key in self._building:
                return "preparing", None
            if key in self._view_failed:
                return "unavailable", self._view_failed[key]
            self._building.add(key)

        def work():
            try:
                viewdata.build(results.info_laz, path)
            except Exception as e:
                print(f"3D view of {results.info_laz}: {type(e).__name__}: {e}", flush=True)       # goes to app.log
                with self._lock:
                    self._view_failed[key] = "the coloured point cloud (_info.laz) could not be read"
            finally:
                with self._lock:
                    self._building.discard(key)

        threading.Thread(target=work, name="treeprof-view", daemon=True).start()
        return "preparing", None

    def _prepare_view(self, run: dict) -> dict:
        workdir = Path(run["workdir"])
        return viewdata.build(workdir / steps.INFO_LAZ, workdir / viewdata.CACHE_NAME)

    # ------------------------------------------------------------------ save
    def suggested_save(self, run: dict) -> tuple:
        """SAVE-1, SAVE-2: (folder to start in, suggested new folder name)."""
        date = None
        if run.get("capture_date"):
            try:
                date = dt.date.fromisoformat(run["capture_date"])
            except ValueError:
                pass
        name = naming.suggested_folder_name(run["site"], date)
        candidates = [run.get("saved_folder") and str(Path(run["saved_folder"]).parent),
                      naming.project_folder(run["input_path"]), str(Path(run["input_path"]).parent),
                      self.settings.last_save_dir]
        start = next((c for c in candidates if c and Path(c).is_dir()), "")
        if start:
            try:
                name = naming.next_free_name(name, [p.name for p in Path(start).iterdir()])
            except OSError:
                pass
        return start, name

    def save(self, results: Results, parent, folder_name: str, selected, *, site: str, on_conflict: str = "ask",
             run_id: Optional[str] = None, progress: Optional[Callable] = None,
             cancel: Optional[Callable] = None) -> Path:
        """SAVE-3 to SAVE-8. on_conflict: "ask" (raise SaveError code conflict), "replace" or "keep_both"."""
        dest = Path(parent) / naming.safe_name(folder_name)
        source = results.folder
        plan = saving.make_plan(source, dest, site, selected) if _is_workdir(source) else \
            _plan_from_saved(results, dest, site, selected)
        if plan.conflicts:
            if on_conflict == "ask":
                raise saving.SaveError("conflict", f"{plan.conflicts[0].name} already exists in {dest}.")
            if on_conflict == "keep_both":
                n = 2
                while plan.conflicts:
                    suffix = f" ({n})"
                    plan = saving.make_plan(source, dest, site, selected, stem_suffix=suffix) if _is_workdir(source) \
                        else _plan_from_saved(results, dest, site, selected, stem_suffix=suffix)
                    n += 1
        path = saving.execute(plan, results.project, replace=(on_conflict == "replace"), progress=progress,
                              cancel=cancel)
        view = results.folder / viewdata.CACHE_NAME
        if view.is_file():                                       # keep the 3D data with the saved copy
            try:
                shutil.copyfile(view, dest / viewdata.CACHE_NAME)
            except OSError:
                pass
        if run_id:
            self.store.add_save(run_id, dest, plan.files)
        self.settings.last_save_dir = str(Path(parent))
        self.settings.save()
        return path

    # ------------------------------------------------------------------ storage
    def storage(self) -> dict:
        """STO-3: what the app keeps on the local disk."""
        rows, total = [], 0
        for run in self.runs():
            size = _folder_size(run["workdir"]) if run["local_exists"] else 0
            tiles = sum(_folder_size(f) for f in Path(run["workdir"]).glob("sat_result_tiles_*")) if size else 0
            total += size
            rows.append({"id": run["id"], "site": run["site"], "capture_date": run["capture_date"],
                         "created_at": run["created_at"], "status": run["status"], "saved": run["saved"],
                         "size": size, "tiles": tiles, "local_exists": run["local_exists"]})
        rows.sort(key=lambda r: -r["size"])
        return {"rows": rows, "used": total, "free": checks.free_bytes(self.settings.runs_root)}

    def delete_local_copy(self, run_id: str, typed_site: Optional[str] = None) -> None:
        """STO-3, STO-4. A run that was never saved needs its site name typed, and is removed completely."""
        run = self.run_view(run_id)
        if run["status"] in (st.RUNNING, st.QUEUED):
            raise ValueError("Cancel the run first.")
        if not run["saved"]:
            if (typed_site or "").strip().lower() != run["site"].strip().lower():
                raise ValueError("Type the site name exactly to delete results that were never saved.")
            self.delete_run(run_id)
            return
        shutil.rmtree(run["workdir"], ignore_errors=True)

    def delete_tiles(self, run_id: str) -> int:
        """STO-5: remove the intermediate tiles of a finished run. Returns the bytes freed."""
        run = self.store.get(run_id)
        freed = 0
        if run["status"] == st.FINISHED:
            for folder in Path(run["workdir"]).glob("sat_result_tiles_*"):
                freed += _folder_size(folder)
                shutil.rmtree(folder, ignore_errors=True)
        return freed


def _is_workdir(folder: Path) -> bool:
    return (Path(folder) / steps.TREES_CSV).is_file()


def _plan_from_saved(results: Results, dest: Path, site: str, selected, stem_suffix: str = "") -> saving.SavePlan:
    """'Save a copy' of results that were opened from a saved folder (OPEN-8): copy the files it has."""
    stem = naming.safe_name(site) + stem_suffix
    plan = saving.SavePlan(dest, stem, files={i.key: None for i in saving.ITEMS})
    for item in saving.ITEMS:
        src = results.paths.get(item.key)
        if src is None or not src.is_file():
            if item.always and item.key in projectfile.REQUIRED_FILES:
                raise saving.SaveError("missing_source", f"{item.key} is missing, so a copy cannot be saved.")
            continue
        dst = dest / (stem + item.saved_suffix)
        pairs = [(src, dst)] + [(src.with_suffix(e), dst.with_suffix(e))
                                for e in item.parts_required + item.parts_optional if src.with_suffix(e).is_file()]
        plan.copies += pairs
        plan.files[item.key] = dst.name
    plan.project_path = dest / (stem + projectfile.EXTENSION)
    plan.total_bytes = sum(s.stat().st_size for s, _ in plan.copies)
    plan.conflicts = [d for _, d in plan.copies if d.exists()] + ([plan.project_path] if plan.project_path.exists() else [])
    return plan


def _folder_size(folder) -> int:
    total = 0
    for p in Path(folder).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total

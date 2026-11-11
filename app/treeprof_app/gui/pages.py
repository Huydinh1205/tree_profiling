"""Home, New run and the progress of a run (HOME, NEW, RUN)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QComboBox, QGridLayout, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import checks, desktop, naming, present, steps, store as st
from .common import (Page, button, check_row, clear, column, frame, label, line, pill, row, status_pill)

LAS_SUFFIXES = (".las", ".laz")


# ---------------------------------------------------------------------------------- Home
class HomePage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.machine_box = QVBoxLayout()
        self.machine_box.setSpacing(8)
        self.body.addLayout(self.machine_box)
        self.body.addLayout(row(
            column(label("Runs", "h1"), label("Start a new run, or open results you saved before.", "muted")), None,
            button("Open saved results", self.open_saved, name="open_saved"),
            button("New run", lambda: win.go_new(), "primary", name="new_run")))
        self.current_box = QVBoxLayout()
        self.runs_box = QVBoxLayout()
        self.recent_box = QVBoxLayout()
        self.body.addLayout(self.current_box)
        self.body.addLayout(self.runs_box)
        self.body.addLayout(self.recent_box)
        self.end()
        self.seen = None
        self.show_machine()
        self.tick()
        self.load_recent()
        if self.state._machine is None:
            self.recheck()

    # ---- HOME-3, HOME-6
    def recheck(self) -> None:
        self.win.bg.run(lambda: self.state.machine(True), lambda r, e: self.show_machine())

    def show_machine(self) -> None:
        clear(self.machine_box)
        found = self.state._machine
        if found is None:
            self.machine_box.addWidget(label("Checking this computer...", "muted", name="machine_wait"))
            return
        chips = []
        for c in found:
            sign, colour = present.LEVEL[c.level]
            chip, lay = frame("chip", (10, 5, 10, 5), 6, horizontal=True)
            mark = QLabel(sign)
            mark.setStyleSheet(f"color:{colour};font-weight:700;")
            lay.addWidget(mark)
            lay.addWidget(label(c.text))
            chip.setToolTip(c.help or c.text)
            chips.append(chip)
        self.machine_box.addLayout(row(*chips, button("Check again", self.recheck, "flat", name="check_again"), None))
        problem = next((c for c in found if c.level == checks.ERROR), None)
        if problem:
            banner, lay = frame("banner_error", (14, 10, 14, 10))
            lay.addWidget(label(f"{problem.text}. {problem.help}".strip(), wrap=True, name="machine_problem"))
            self.machine_box.addWidget(banner)
        if any(c.key == "disk" and c.level == checks.WARNING for c in found):
            banner, lay = frame("banner_warning", (14, 8, 14, 8), horizontal=True)
            lay.addWidget(label("The local disk is getting full. Free some space before starting a large run.", wrap=True), 1)
            lay.addWidget(button("Open Storage", self.win.go_settings, "flat"))
            self.machine_box.addWidget(banner)

    def open_saved(self) -> None:
        path = self.win.dialogs.pick_project(self.state.settings.last_save_dir or "")
        if path:
            self.win.go_saved(path)

    # ---- HOME-1, HOME-4
    def signature(self):
        cur = self.state.current()
        live = cur["live"] if cur else None
        return ([(r["id"], r["status"], r["trees"], len(self.state.store.saves(r["id"])))
                 for r in self.state.store.list_runs()],
                (cur["id"], live.label, round(live.fraction or 0, 2), dict(live.steps)) if cur and live else None)

    def tick(self) -> None:
        sig = self.signature()
        if sig == self.seen:
            return
        self.seen = sig
        self.show_current()
        self.show_runs()

    def show_current(self) -> None:
        clear(self.current_box)
        cur = self.state.current()
        if not cur:
            return
        live = cur["live"]
        card, lay = frame()
        card.setObjectName("card")
        lay.addLayout(row(
            column(label("RUNNING NOW", "caps"), label(present.run_title(cur), "h2", name="current_title"),
                   label(f"Started by {cur['run_by']} at {present.fmt_when(cur['started_at'])}", "muted")), None,
            button("View progress", lambda: self.win.go_run(cur["id"]), name="view_progress")))
        text = present.current_step_text(cur)
        if text:
            eta = f"About {present.fmt_duration(live.eta_minutes * 60)} left" if live.eta_minutes else ""
            lay.addLayout(row(label(text, "strong", name="current_step"), None, label(eta, "muted")))
            lay.addWidget(_bar(live.fraction))
        self.current_box.addWidget(card)

    def show_runs(self) -> None:
        clear(self.runs_box)
        runs = self.state.runs()
        if not runs:
            card, lay = frame(margins=(20, 40, 20, 40))
            for w in (label("No runs yet", "h2", name="no_runs"), label("Press New run to choose a point cloud.", "muted")):
                w.setAlignment(Qt.AlignCenter)
                lay.addWidget(w)
            self.runs_box.addWidget(card)
            return
        card, lay = frame(margins=(20, 10, 20, 10), spacing=0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        for col, (head, stretch) in enumerate((("Site", 4), ("Capture date", 2), ("Run by", 2), ("Run on", 3), ("Trees", 1),
                                               ("Status", 3), ("Saved", 5))):
            grid.addWidget(label(head, "muted"), 0, col)
            grid.setColumnStretch(col, stretch)
        for i, r in enumerate(runs, 1):
            site = button(r["site"], lambda r=r: self.win.go_run(r["id"]), "link", name=f"open_{r['id']}")
            cells = [site, label(present.fmt_capture(r["capture_date"])), label(r["run_by"]),
                     label(present.fmt_when(r["created_at"])),
                     label(f"{r['trees']:,}" if r["trees"] is not None else "–"), status_pill(r["status"])]
            for col, w in enumerate(cells):
                grid.addWidget(w, i, col, Qt.AlignLeft | Qt.AlignVCenter)
            grid.addLayout(self.row_actions(r), i, 6)
        lay.addLayout(grid)
        self.runs_box.addWidget(card)

    def row_actions(self, r: dict):
        rid, status, out = r["id"], r["status"], []
        if status == st.QUEUED:
            out.append(button("Remove from queue", lambda: self.act(self.state.cancel, rid), "flat"))
        elif status == st.FINISHED:
            if r["saved"]:
                folder = r["saved_folder"]
                out.append(button(Path(folder).name, lambda: desktop.open_folder(folder), "flat", tip=folder))
            elif r["trees"]:
                out.append(pill("Not saved", "!", "#FFF1C9", "#6B4700"))
                out.append(button("Save", lambda: self.win.go_run(rid, save=True)))
            if not r["local_exists"] and self.state.project_path_for(rid) is None:
                out.append(pill("Not found", "?", "#ECEFEC", "#3A453F"))
        elif status in (st.INTERRUPTED, st.CANCELLED):
            out.append(button("Resume", lambda: self.act(self.state.resume, rid)))
        elif status == st.FAILED:
            out.append(button("Try again", lambda: self.act(self.state.resume, rid)))
        return row(*out, None, spacing=8)

    # ---- HOME-5
    def load_recent(self) -> None:
        """Looking whether the files are still there can be slow on a shared drive: not on the main thread."""
        if self.state.settings.recent:
            self.win.bg.run(self.state.recent, lambda entries, error: self.show_recent(entries or []))

    def show_recent(self, entries: list) -> None:
        clear(self.recent_box)
        if not entries:
            return
        card, lay = frame(margins=(20, 12, 20, 12), spacing=6)
        lay.addWidget(label("Opened recently", "strong", name="recent_title"))
        lay.addWidget(label("Saved results you opened on this computer.", "muted"))
        for i, e in enumerate(entries):
            title, small = present.recent_line(e)
            lay.addWidget(line())
            if e["exists"]:
                head = button(title, lambda e=e: self.win.go_saved(e["path"]), "link", name=f"recent_{i}")
                extra = []
            else:                                                # moved or deleted: shown, but cannot be opened
                head = label(title, "muted", name=f"recent_{i}")
                extra = [pill("Not found", "?", "#ECEFEC", "#3A453F")]
            lay.addLayout(row(column(head, label(small, "muted", wrap=True)), *extra,
                              button("Remove from list", lambda e=e: self.forget(e), "flat", name=f"forget_{i}")))
        self.recent_box.addWidget(card)

    def forget(self, entry: dict) -> None:
        self.state.forget_opened(entry["path"])
        clear(self.recent_box)
        self.load_recent()

    def act(self, what, run_id: str) -> None:
        what(run_id)
        self.tick()

    def drop_files(self, paths: list) -> None:                                     # HOME-7
        self.win.handle_paths(paths)


def _bar(fraction: Optional[float]) -> QProgressBar:
    bar = QProgressBar()
    bar.setTextVisible(False)
    _set_bar(bar, fraction)
    return bar


def _set_bar(bar: QProgressBar, fraction: Optional[float]) -> None:
    if fraction is None:                                         # cannot tell how far: a moving bar
        if bar.maximum() != 0:
            bar.setRange(0, 0)
    else:
        if bar.maximum() != 1000:
            bar.setRange(0, 1000)
        bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))


# ---------------------------------------------------------------------------------- New run
class NewRunPage(Page):
    def __init__(self, win, paths: Optional[list] = None):
        super().__init__(win)
        self.files = []
        names = self.state.store.known_names()
        self.body.addLayout(row(button("‹  Back to runs", win.go_home, "flat", name="back"), None))
        self.body.addWidget(label("New run", "h1"))

        box, lay = frame("dashed")
        self.path = QLineEdit()
        self.path.setObjectName("path")
        self.path.setPlaceholderText("or paste the full path of a file here")
        self.path.returnPressed.connect(self.add_typed)
        lay.addLayout(row(
            column(label("1. Choose the point cloud", "strong"),
                   label("A .las or .laz file from DJI Terra. Drop files here, or browse. Several files become several runs.",
                         "muted", wrap=True)), None,
            button("Browse...", self.browse, name="browse")))
        lay.addLayout(row(self.path, button("Add", self.add_typed, "flat", name="add")))
        self.body.addWidget(box)

        self.cards = QVBoxLayout()
        self.cards.setSpacing(16)
        self.body.addLayout(self.cards)

        card, lay = frame()
        lay.addWidget(label("2. Who is running this", "strong"))
        self.run_by = QComboBox()
        self.run_by.setObjectName("run_by")
        self.run_by.setEditable(True)
        self.run_by.addItems(names)
        self.run_by.setCurrentText(names[0] if names else "")
        self.run_by.lineEdit().setPlaceholderText("Your name")
        self.run_by.setFixedWidth(320)
        lay.addLayout(row(column(label("Run by", "muted"), self.run_by), None))
        self.advanced = QWidget()
        adv = QVBoxLayout(self.advanced)
        adv.setContentsMargins(0, 0, 0, 0)
        self.max_points = QSpinBox()
        self.max_points.setObjectName("max_points")
        self.max_points.setRange(1_000_000, 500_000_000)
        self.max_points.setSingleStep(1_000_000)
        self.max_points.setGroupSeparatorShown(True)
        self.max_points.setValue(steps.DEFAULT_MAX_POINTS)
        self.max_points.setFixedWidth(320)
        adv.addLayout(row(column(label("Max points per tile", "muted"), self.max_points), None))
        adv.addWidget(label("Lower it only if a run stopped because the file was too large.", "muted"))
        self.advanced.setVisible(False)
        lay.addLayout(row(button("Advanced", lambda: self.advanced.setVisible(not self.advanced.isVisible()), "flat",
                                 name="advanced"), None))
        lay.addWidget(self.advanced)
        self.body.addWidget(card)

        self.start = button("Start", self.go, "primary", name="start")
        self.body.addLayout(row(None, button("Cancel", win.go_home, name="cancel"), self.start))
        self.end()
        self.tick()
        for p in paths or []:
            self.add_path(p)

    # ---- NEW-1
    def browse(self) -> None:
        initial = self.state.settings.last_browse_dir or self.state.settings.shared_root or ""
        for p in self.win.dialogs.pick_files(initial) or []:
            self.add_path(p)

    def add_typed(self) -> None:
        self.add_path(self.path.text())
        self.path.clear()

    def drop_files(self, paths: list) -> None:
        clouds = [p for p in paths if Path(p).suffix.lower() in LAS_SUFFIXES]
        for p in clouds:
            self.add_path(p)
        if len(clouds) < len(paths):
            self.win.handle_paths([p for p in paths if p not in clouds])

    def add_path(self, path: str) -> None:
        path = str(path).strip().strip('"')
        if path.lower().startswith("file:"):                     # a file dropped on the text box arrives like this
            path = QUrl(path).toLocalFile()
        if not path or any(f["path"] == path for f in self.files):
            return
        info = naming.site_from_path(path)
        entry = {"path": path, "report": None, "site": info.site,
                 "date": naming.format_capture_date(info.capture_date) if info.capture_date else ""}
        self.files.append(entry)
        self.show_files()

        def done(report, error):
            if entry not in self.files:
                return
            entry["report"] = report if error is None else checks.FileReport(
                path, Path(path).name, checks=[checks.Check("read", checks.ERROR, "This file could not be checked", str(error))])
            self.show_files()
        self.win.bg.run(lambda: self.state.check_file(path), done)

    def remove(self, entry: dict) -> None:
        self.files.remove(entry)
        self.show_files()

    # ---- NEW-2 to NEW-4
    def show_files(self) -> None:
        clear(self.cards)
        for f in self.files:
            rep = f["report"]
            card, lay = frame()
            name = label(Path(f["path"]).name, "h2")
            lay.addLayout(row(column(name, label(str(Path(f["path"]).parent), "muted", wrap=True)), None,
                              button("Remove", lambda f=f: self.remove(f), "flat")))
            if rep is None:
                lay.addWidget(label("Checking this file...", "muted"))
                self.cards.addWidget(card)
                continue
            facts = present.file_facts(rep, self.state.estimate_minutes(rep.header.points) if rep.header else None)
            if facts:
                grid = QGridLayout()
                grid.setHorizontalSpacing(30)
                for i, (k, v) in enumerate(facts):
                    grid.addLayout(column(label(k, "muted"), label(v, "strong")), i // 3, i % 3)
                lay.addLayout(grid)
            lay.addWidget(line())
            lay.addWidget(label("Checks before starting", "strong"))
            for c in rep.checks:
                lay.addWidget(check_row(c))
            if rep.can_start:
                site, date = QLineEdit(f["site"]), QLineEdit(f["date"])
                site.setObjectName("site")
                date.setObjectName("date")
                date.setPlaceholderText("13 Feb 25")
                site.textChanged.connect(lambda text, f=f: f.update(site=text))
                date.textChanged.connect(lambda text, f=f: f.update(date=text))
                lay.addLayout(row(column(label("Site name", "muted"), site),
                                  column(label("Capture date (optional)", "muted"), date)))
                lay.addWidget(label("Site name and capture date come from the folder name. Change them if they are wrong.",
                                    "muted"))
            self.cards.addWidget(card)
        self.tick()

    # ---- NEW-5 to NEW-8
    def tick(self) -> None:
        busy = self.state.current() is not None or self.state.store.next_queued() is not None
        self.start.setText("Add to queue" if busy else "Start")
        self.start.setEnabled(bool(self.files) and all(f["report"] is not None and f["report"].can_start
                                                       for f in self.files))

    def go(self) -> None:
        if any(f["report"] is None for f in self.files):
            return
        problem = present.start_problem(self.files, self.run_by.currentText())
        if problem:
            self.win.notify(problem, "warning")
            return
        ids = []
        for f in self.files:
            date = naming.parse_user_date(f["date"])
            ids.append(self.state.add_run(f["path"], site=f["site"].strip(), run_by=self.run_by.currentText().strip(),
                                          capture_date=date.isoformat() if date else None,
                                          max_points=int(self.max_points.value())))
        self.win.go_run(ids[0])


# ---------------------------------------------------------------------------------- a run that is not finished
class ProgressView(Page):
    """RUN-1 to RUN-9: the steps of a run, with what to do when it stopped."""
    SIGNS = {"waiting": ("○", "#8C9891"), "running": ("▶", "#1D4F91"), "done": ("✓", "#17553A")}

    def __init__(self, win, view: dict):
        super().__init__(win)
        self.run_id, status = view["id"], view["status"]
        self.body.addLayout(row(button("‹  Back to runs", win.go_home, "flat", name="back"), None))
        head = [column(label(present.run_title(view), "h1", name="title"),
                       label(f"Run by {view['run_by']} · {Path(view['input_path']).name}", "muted")), None]
        if status == st.RUNNING:
            head.append(button("Cancel run", self.cancel, "danger", name="cancel_run"))
        elif status == st.QUEUED:
            head.append(button("Remove from queue", lambda: self.state.cancel(self.run_id), name="remove_from_queue"))
        self.body.addLayout(row(*head))

        if status == st.QUEUED:
            card, lay = frame(horizontal=True)
            lay.addWidget(status_pill(status))
            lay.addWidget(label("This run starts when the run before it is finished."), 1)
            self.body.addWidget(card)
        elif status == st.FAILED:
            banner, lay = frame("banner_error")
            lay.addWidget(label(view["error_message"] or "Something went wrong.", "h2", wrap=True, name="error_message"))
            lay.addWidget(label("Nothing is lost. The parts that finished are kept."))
            actions = {"Try again with smaller tiles": lambda: self.state.retry_smaller(self.run_id),
                       "Try again": lambda: self.state.resume(self.run_id),
                       "Send log to Ryan": lambda: win.send_log(self.state.run_view(self.run_id)),
                       "Delete this run": self.delete}
            names = present.failure_buttons(view["error_kind"])
            lay.addLayout(row(*[button(n, actions[n], "primary" if i == 0 else "") for i, n in enumerate(names)], None))
            self.body.addWidget(banner)
        elif status in (st.CANCELLED, st.INTERRUPTED):
            banner, lay = frame("banner_warning")
            lay.addWidget(label("This run was stopped." if status == st.CANCELLED else
                                "This run was cut off when the app or the computer stopped.", "h2", name="stopped"))
            lay.addWidget(label("Resume continues from where it stopped. Finished parts are not done again."))
            lay.addLayout(row(button("Resume", lambda: self.state.resume(self.run_id), "primary", name="resume"),
                              button("Delete this run", self.delete), None))
            self.body.addWidget(banner)

        card, lay = frame(margins=(24, 8, 24, 8), spacing=0)
        self.rows = {}
        for i, step in enumerate(steps.STEP_ORDER):
            if i:
                lay.addWidget(line())
            sign = QLabel("○")
            sign.setFixedWidth(30)
            sign.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            right, text, eta, bar = label("Waiting", "muted"), label(""), label("", "muted"), _bar(0.0)
            detail = QWidget()
            inner = QVBoxLayout(detail)
            inner.setContentsMargins(0, 4, 0, 0)
            inner.addLayout(row(text, None, eta))
            inner.addWidget(bar)
            detail.setVisible(False)
            block = column(row(label(steps.STEP_LABELS[step], "strong"), None, right), detail, spacing=4)
            holder = QWidget()
            hl = row(sign, block, spacing=14)
            hl.setContentsMargins(0, 14, 0, 14)
            hl.setStretch(1, 1)
            holder.setLayout(hl)
            lay.addWidget(holder)
            self.rows[step] = (sign, right, text, eta, bar, detail)
        self.body.addWidget(card)
        if status == st.RUNNING:
            self.body.addWidget(label("You can close this window. The run keeps going in the background, and this "
                                      "computer stays awake until it is done.", "muted", wrap=True, name="keeps_going"))

        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMinimumHeight(240)
        self.log.setVisible(status == st.FAILED)
        self.toggle = button("Hide details" if status == st.FAILED else "Show details", self.toggle_log, "flat",
                             name="show_details")
        self.body.addLayout(row(self.toggle, None))
        self.body.addWidget(self.log)
        self.end()
        self.pushed = 0
        if status != st.RUNNING:
            lines = self.state.log_tail(self.run_id)
            if view["error_detail"]:
                lines = lines + [view["error_detail"]]
            self.log.setPlainText("\n".join(lines))
        self.tick()

    def toggle_log(self) -> None:                                                  # RUN-6
        self.log.setVisible(not self.log.isVisible())
        self.toggle.setText("Hide details" if self.log.isVisible() else "Show details")

    def cancel(self) -> None:                                                      # RUN-5
        if self.win.dialogs.ask("Stop this run?", "The parts that are finished are kept. You can resume it later.",
                                ["Stop the run", "Keep running"], danger=True) == "Stop the run":
            self.state.cancel(self.run_id)

    def delete(self) -> None:
        if self.win.dialogs.ask("Delete this run?", "Everything this run made on this computer is removed.",
                                ["Delete", "Keep it"], danger=True) == "Delete":
            self.state.delete_run(self.run_id)
            self.win.go_home()

    def tick(self) -> None:
        view = self.state.run_view(self.run_id)
        for r in present.step_rows(view):
            sign, right, text, eta, bar, detail = self.rows[r["key"]]
            glyph, colour = self.SIGNS[r["state"]]
            sign.setText(glyph)
            sign.setStyleSheet(f"color:{colour};font-size:20px;font-weight:700;")
            right.setText(r["right"])
            running = r["state"] == "running"
            if running:
                text.setText(r["label"])
                eta.setText(r["eta"])
                _set_bar(bar, r["fraction"])
            detail.setVisible(running)
        live = view["live"]
        if live and view["status"] == st.RUNNING and live.log_total > self.pushed:
            new = min(live.log_total - self.pushed, len(live.log))
            for text_line in list(live.log)[-new:]:
                self.log.appendPlainText(text_line)
            self.pushed = live.log_total


# ---------------------------------------------------------------------------------- one run, whatever its state
class RunPage(QWidget):
    """Progress while the run is going or stopped, Results once it is finished (RUN-7)."""

    def __init__(self, win, run_id: str, save: bool = False):
        super().__init__()
        self.win, self.state, self.run_id = win, win.state, run_id
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.inner, self.status = None, None
        self.build()
        if save and hasattr(self.inner, "save"):
            self.inner.save()

    def build(self) -> None:
        from .results import results_page
        clear(self.lay)
        try:
            view = self.state.run_view(self.run_id)
        except KeyError:
            self.status = None
            self.inner = MessagePage(self.win, "This run no longer exists.")
        else:
            self.status = view["status"]
            self.inner = results_page(self.win, view) if view["status"] == st.FINISHED else ProgressView(self.win, view)
        self.lay.addWidget(self.inner)

    def tick(self) -> None:
        try:
            status = self.state.store.get(self.run_id)["status"]
        except KeyError:
            if self.status is not None:
                self.build()
            return
        if status != self.status:
            self.build()
        elif hasattr(self.inner, "tick"):
            self.inner.tick()

    def drop_files(self, paths: list) -> None:
        self.win.handle_paths(paths)


class MessagePage(Page):
    """A screen that only says something (results that cannot be opened, a run that is gone)."""

    def __init__(self, win, title: str, text: str = "", heading: str = "", extra: Optional[list] = None):
        super().__init__(win)
        self.body.addLayout(row(button("‹  Back to runs", win.go_home, "flat", name="back"), None))
        if heading:
            self.body.addWidget(label(heading, "h1"))
        card, lay = frame(margins=(20, 40, 20, 40))
        t = label(title, "h2", wrap=True, name="message_title")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        if text:
            d = label(text, "muted", wrap=True, name="message_text")
            d.setAlignment(Qt.AlignCenter)
            lay.addWidget(d)
        self.body.addWidget(card)
        if extra:
            self.body.addLayout(row(*extra, None))
        self.end()

    def drop_files(self, paths: list) -> None:
        self.win.handle_paths(paths)

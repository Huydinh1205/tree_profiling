"""The Save dialog and the Settings screen with Storage (SAVE, STO, SET)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QProgressBar, QSpinBox, QVBoxLayout

from .. import __version__, desktop, naming, present, saving, store as st
from ..appstate import Results
from ..settings import runs_root_problem
from .common import Page, button, check_row, clear, column, frame, label, line, row


class SaveDialog(QDialog):
    """SAVE-1 to SAVE-8. view is the run when saving from its working folder, None for 'Save a copy'."""

    def __init__(self, win, res: Results, view: Optional[dict], on_saved: Optional[Callable] = None):
        super().__init__(win)
        self.win, self.state, self.res, self.view, self.on_saved = win, win.state, res, view, on_saved
        self.setWindowTitle("Save results")
        self.setModal(True)
        self.setMinimumWidth(720)
        self.site = (view or {}).get("site") or res.project["site_name"]
        if view:
            parent, name = self.state.suggested_save(self.state.run_view(view["id"]))
        else:
            parent, name = self.state.settings.last_save_dir or "", res.folder.name
        self.chosen = saving.default_selection()
        self.progress, self.cancelled, self.saved_path, self.step = (0, 1, ""), False, None, "choose"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label("Save results", "h1"))
        self.parent_edit = QLineEdit(parent)
        self.parent_edit.setObjectName("save_parent")
        self.parent_edit.setPlaceholderText("Choose a folder")
        lay.addLayout(column(label("Save in", "strong"),
                             row(self.parent_edit, button("Change folder", self.change_folder, name="change_folder"))))
        self.name_edit = QLineEdit(name)
        self.name_edit.setObjectName("save_name")
        lay.addLayout(column(label("New folder name", "strong"), self.name_edit))
        files = column(label("Files to save", "strong"),
                       label("Project file (.treeprof): opens these results again in Tree Profiling. Always saved.", "muted"),
                       spacing=4)
        self.boxes = {}
        for item in saving.ITEMS:
            src = self.source_of(item)
            if src is None and not item.always:
                continue
            size = f" ({present.fmt_bytes(src.stat().st_size)})" if src else ""
            box = QCheckBox(item.label + size)
            box.setObjectName("file_" + item.key)
            box.setChecked(item.key in self.chosen or item.always)
            box.setEnabled(not item.always)
            box.toggled.connect(lambda on, key=item.key: self.chosen.add(key) if on else self.chosen.discard(key))
            files.addWidget(box)
            self.boxes[item.key] = box
        lay.addLayout(files)
        self.actions = QVBoxLayout()
        lay.addLayout(self.actions)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.show_progress)
        self.show_step("choose")

    def source_of(self, item) -> Optional[Path]:
        p = (self.res.folder / item.work_name) if self.view else self.res.paths.get(item.key)
        return p if p and p.is_file() else None

    def change_folder(self) -> None:                                               # SAVE-1
        got = self.win.dialogs.pick_folder(self.parent_edit.text())
        if got:
            self.parent_edit.setText(got)

    def show_step(self, step: str, message: str = "") -> None:
        self.step = step
        clear(self.actions)
        if step == "choose":
            if message:
                note = label(message, wrap=True, name="save_message")
                note.setStyleSheet("color:#8E2118;")
                self.actions.addWidget(note)
            self.actions.addLayout(row(None, button("Cancel", self.reject, name="save_cancel"),
                                       button("Save", lambda: self.go("ask"), "primary", name="save_go")))
        elif step == "conflict":                                                   # SAVE-5
            banner, lay = frame("banner_warning", (14, 10, 14, 10))
            lay.addWidget(label("This folder already has results with the same name. What do you want to do?", "strong",
                                wrap=True, name="conflict"))
            lay.addLayout(row(button("Replace", lambda: self.go("replace"), name="replace"),
                              button("Keep both", lambda: self.go("keep_both"), "primary", name="keep_both"),
                              button("Cancel", lambda: self.show_step("choose"), "flat"), None))
            self.actions.addWidget(banner)
        elif step == "copying":                                                    # SAVE-6
            self.copy_label = label("Copying...", "strong", name="copying")
            self.bar = QProgressBar()
            self.bar.setRange(0, 1000)
            self.bar.setTextVisible(False)
            self.actions.addWidget(self.copy_label)
            self.actions.addWidget(self.bar)
            self.actions.addLayout(row(None, button("Cancel", self.cancel_copy, name="cancel_copy")))
        elif step == "saved":
            banner, lay = frame("banner_ok", (14, 10, 14, 10))
            lay.addWidget(label("Saved. Double-click the .treeprof file in that folder to open these results again.",
                                "strong", wrap=True, name="saved"))
            self.actions.addWidget(banner)
            self.actions.addLayout(row(None, button("Open saved folder", lambda: desktop.open_folder(Path(self.saved_path).parent),
                                                    name="open_saved_folder"),
                                       button("Close", self.accept, "primary", name="save_close")))
        for w in (self.parent_edit, self.name_edit, *self.boxes.values()):
            w.setEnabled(step == "choose" and not (isinstance(w, QCheckBox) and self._always(w)))

    def _always(self, box) -> bool:
        return any(i.always and self.boxes.get(i.key) is box for i in saving.ITEMS)

    def cancel_copy(self) -> None:
        self.cancelled = True

    def show_progress(self) -> None:
        if self.step != "copying":
            return
        done, total, name = self.progress
        self.copy_label.setText(f"Copying {name}" if name else "Copying...")
        self.bar.setValue(int(done / total * 1000) if total else 0)

    def go(self, mode: str) -> None:
        parent, name = self.parent_edit.text().strip(), naming.safe_name(self.name_edit.text() or "", fallback="")
        problem = present.save_problem(parent, self.name_edit.text())
        if problem:
            self.show_step("choose", problem)
            return
        self.cancelled, self.progress = False, (0, 1, "")
        self.show_step("copying")
        self.timer.start()
        chosen = set(self.chosen)

        def work():
            return self.state.save(self.res, parent, name, chosen, site=self.site, on_conflict=mode,
                                   run_id=self.view["id"] if self.view else None,
                                   progress=lambda d, t, n: setattr(self, "progress", (d, t, n)),
                                   cancel=lambda: self.cancelled)

        def done(path, error):
            self.timer.stop()
            if error is None:
                self.saved_path = str(path)
                self.show_step("saved")
                if self.on_saved:
                    self.on_saved()
            elif isinstance(error, saving.SaveCancelled):
                self.show_step("choose", "Saving was cancelled. Nothing was left in the folder.")
            elif isinstance(error, saving.SaveError) and error.code == "conflict":
                self.show_step("conflict")
            elif isinstance(error, saving.SaveError):
                self.show_step("choose", str(error))
            else:
                self.show_step("choose", f"Saving failed: {error}")
        self.win.bg.run(work, done)

    def reject(self) -> None:
        if self.step == "copying":                               # Esc or the X while copying: stop the copy first
            self.cancelled = True
            return
        super().reject()


# ---------------------------------------------------------------------------------- Settings and storage
class SettingsPage(Page):
    def __init__(self, win):
        super().__init__(win)
        s = self.state.settings
        self.body.addLayout(row(button("‹  Back to runs", win.go_home, "flat", name="back"), None))
        self.body.addWidget(label("Settings", "h1"))

        card, lay = frame()                                                        # STO-1 to STO-5
        lay.addWidget(label("Storage on this computer", "h2"))
        self.storage_note = label("", "muted", wrap=True, name="storage_note")
        lay.addWidget(self.storage_note)
        self.keep = QSpinBox()                                                     # STO-6
        self.keep.setObjectName("keep_days")
        self.keep.setRange(0, 3650)
        self.keep.setSpecialValueText("Never")
        self.keep.setValue(int(s.keep_days))
        self.keep.setFixedWidth(160)
        self.keep.valueChanged.connect(self.set_keep)
        lay.addLayout(row(column(label("Remove runs from this computer after (days)", "strong"), self.keep), None))
        self.show_storage_note()
        self.storage = QVBoxLayout()
        self.storage.setSpacing(6)
        self.storage.addWidget(label("Adding up...", "muted"))
        lay.addLayout(self.storage)
        self.body.addWidget(card)

        card, lay = frame()                                                        # SET-1 to SET-4
        lay.addWidget(label("Folders", "h2"))
        self.shown = {}
        for text, attr, kind, check in (("Where runs are kept on this computer", "runs_root", "folder", runs_root_problem),
                                        ("Shared drive folder that opens when you browse", "shared_root", "folder", None),
                                        ("CloudCompare", "cloudcompare", "program", None)):
            value = label(getattr(s, attr) or "Not set", wrap=True, name="value_" + attr)
            self.shown[attr] = value
            lay.addLayout(row(column(label(text, "strong"), value), None,
                              button("Change", lambda a=attr, k=kind, c=check: self.change(a, k, c), name="change_" + attr)))
        self.warn = QSpinBox()
        self.warn.setObjectName("warn_gb")
        self.warn.setRange(0, 100000)
        self.warn.setSingleStep(10)
        self.warn.setValue(int(s.warn_free_gb))
        self.warn.setFixedWidth(160)
        self.warn.valueChanged.connect(self.set_warn)
        lay.addLayout(row(column(label("Warn me when free space is below (GB)", "strong"), self.warn), None))
        self.body.addWidget(card)

        card, lay = frame()                                                        # HOME-3
        self.again = button("Check again", self.recheck, name="check_again")
        lay.addLayout(row(label("This computer", "h2"), None, self.again))
        self.computer = QVBoxLayout()
        lay.addLayout(self.computer)
        self.body.addWidget(card)

        card, lay = frame(horizontal=True)                                         # SET-5
        cfg = self.state.runner.config
        lay.addLayout(column(label("About", "h2"),
                             label(f"Tree Profiling {__version__} · scripts {cfg.scripts_commit or 'unknown'} · "
                                   f"model {cfg.sat_image}", "muted", wrap=True, name="about"),
                             label(f"Log: {self.state.home / 'state' / 'app.log'}", "muted", wrap=True)), 1)
        lay.addWidget(button("Send log to Ryan", lambda: win.send_log(None), name="send_log"))
        self.body.addWidget(card)
        self.end()
        self.show_computer()
        if self.state._machine is None:
            self.recheck()
        self.load_storage()

    # ---- storage
    def load_storage(self) -> None:
        self.win.bg.run(self.state.storage, lambda info, error: self.show_storage(info) if error is None else None)

    def show_storage(self, info: dict) -> None:
        clear(self.storage)
        free = info["free"]
        self.storage.addWidget(label(f"Tree Profiling results: {present.fmt_bytes(info['used']) or '0 bytes'}"
                                     + (f" · Free on this disk: {present.fmt_bytes(free)}" if free is not None else ""),
                                     "strong", name="storage_total"))
        rows = [r for r in info["rows"] if r["local_exists"]]
        if not rows:
            self.storage.addWidget(label("No runs are kept on this computer.", "muted", name="storage_empty"))
        for r in rows:
            title, text, attention = present.storage_line(r)
            small = label(text, "muted")
            if attention:
                small.setStyleSheet("color:#6B4700;")
            actions = []
            if r["status"] == st.FINISHED and r["tiles"]:
                actions.append(button(f"Delete intermediate tiles ({present.fmt_bytes(r['tiles'])})",
                                      lambda r=r: self.delete_tiles(r), "flat", name=f"tiles_{r['id']}"))
            if r["status"] not in (st.RUNNING, st.QUEUED):
                actions.append(button("Delete local copy", lambda r=r: self.delete_copy(r),
                                      "" if r["saved"] else "danger", name=f"delete_{r['id']}"))
            self.storage.addWidget(line())
            self.storage.addLayout(row(column(label(title, "strong"), small), None, *actions))

    def delete_tiles(self, r: dict) -> None:                                       # STO-5
        freed = self.state.delete_tiles(r["id"])
        self.win.notify(f"Freed {present.fmt_bytes(freed) or '0 bytes'}.", "ok")
        self.load_storage()

    def delete_copy(self, r: dict) -> None:                                        # STO-3, STO-4
        typed = None
        if r["saved"]:
            if self.win.dialogs.ask(f"Delete the local copy of {r['site']}?",
                                    "The saved copy is not touched. You can still open it from the folder you saved it in.",
                                    ["Delete local copy", "Keep it"]) != "Delete local copy":
                return
        else:
            typed = self.win.dialogs.ask_text(
                f"{r['site']} was never saved",
                "Deleting it removes the only copy of these results.\nTo delete it for good, type the site name:")
            if typed is None:
                return
        try:
            self.state.delete_local_copy(r["id"], typed)
        except ValueError as e:
            self.win.notify(str(e), "error")
            return
        self.load_storage()

    # ---- folders
    def change(self, attr: str, kind: str, check) -> None:
        s = self.state.settings
        got = getattr(self.win.dialogs, f"pick_{kind}")(getattr(s, attr) or "")
        if not got:
            return
        problem = check(got) if check else None
        if problem:
            self.win.notify(problem, "error")
            return
        setattr(s, attr, got)
        s.save()
        self.shown[attr].setText(got)

    def set_keep(self, value: int) -> None:
        self.state.settings.keep_days = int(value)
        self.state.settings.save()
        self.show_storage_note()

    def show_storage_note(self) -> None:
        self.storage_note.setText(present.storage_note(self.state.settings.keep_days))

    def set_warn(self, value: int) -> None:
        self.state.settings.warn_free_gb = int(value)
        self.state.settings.save()

    # ---- this computer
    def recheck(self) -> None:
        if not self.again.isEnabled():
            return
        self.again.setText("Checking...")
        self.again.setEnabled(False)

        def done(result, error) -> None:
            self.again.setText("Check again")
            self.again.setEnabled(True)
            self.show_computer()
        self.win.bg.run(lambda: self.state.machine(True), done)

    def show_computer(self) -> None:
        clear(self.computer)
        if self.state._machine is None:
            self.computer.addWidget(label("Checking...", "muted"))
        for c in self.state._machine or []:
            self.computer.addWidget(check_row(c))

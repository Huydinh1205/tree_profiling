"""The main window: which screen is shown, dropped files, what closing the window does, the icon
next to the clock, and making sure only one copy of the app runs (SYS-1, SYS-2, SYS-6, HOME-7, OPEN-1)."""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from .. import desktop, projectfile, store as st
from ..appstate import AppState
from .common import STYLE, Background, Dialogs, button, frame
from .dialogs import SettingsPage
from .pages import LAS_SUFFIXES, HomePage, NewRunPage, RunPage
from .results import open_page

KEEP, QUIT, STAY = "Keep running in the background", "Cancel run and quit", "Stay"
NOTICE_COLOURS = {"info": ("#E6EEFA", "#1D4F91"), "ok": ("#E2F1E8", "#17553A"), "warning": ("#FFF1C9", "#6B4700"),
                  "error": ("#FBE5E2", "#8E2118")}


def app_icon() -> QIcon:
    pix = QPixmap()
    pix.loadFromData(desktop.icon_png(64), "PNG")
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, dialogs=None, use_tray: bool = True):
        super().__init__()
        self.state = state
        self.dialogs = dialogs or Dialogs(self)
        self.bg = Background()
        self.setWindowTitle("Tree Profiling")
        self.setWindowIcon(app_icon())
        self.resize(1320, 860)
        self.setMinimumSize(1100, 700)
        self.setAcceptDrops(True)
        self.last_notice, self.quitting, self.page = "", False, None
        self.cleaned_at, self.cleaning = 0.0, False

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header, h = frame("header", (20, 8, 20, 8), horizontal=True)
        h.addWidget(button("Tree Profiling", self.go_home, "brand", name="home"))
        h.addStretch(1)
        h.addWidget(button("Settings", self.go_settings, "flat", name="settings"))
        lay.addWidget(header)
        self.notice = QLabel("")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        lay.addWidget(self.notice)
        self.holder = QVBoxLayout()
        self.holder.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.holder, 1)

        self.tray = None
        if use_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("Tree Profiling")
            menu = QMenu()
            for text, slot in (("Open Tree Profiling", self.show_again), ("Quit", self.quit_from_tray)):
                act = QAction(text, menu)
                act.triggered.connect(lambda _=False, slot=slot: slot())
                menu.addAction(act)
            self.tray.setContextMenu(menu)
            self.tray_menu = menu
            self.tray.activated.connect(lambda reason: self.show_again()
                                        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self.tray.show()

        self.statuses = {r["id"]: r["status"] for r in state.store.list_runs()}
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.go_home()

    # ------------------------------------------------------------------ screens
    def show_page(self, page: QWidget) -> None:
        old = self.page
        self.page = page
        self.holder.addWidget(page)
        if old is not None:
            self.holder.removeWidget(old)
            old.setParent(None)
            old.deleteLater()

    def go_home(self) -> None:
        self.show_page(HomePage(self))

    def go_new(self, paths: Optional[list] = None) -> None:
        self.show_page(NewRunPage(self, paths))

    def go_run(self, run_id: str, save: bool = False) -> None:
        self.show_page(RunPage(self, run_id, save))

    def go_saved(self, path) -> None:
        self.state.settings.last_save_dir = str(Path(path).parent if Path(path).is_file() else path)
        self.show_page(open_page(self, path))

    def go_settings(self) -> None:
        self.show_page(SettingsPage(self))

    def notify(self, text: str, kind: str = "info") -> None:
        """A line under the header that goes away by itself."""
        bg, fg = NOTICE_COLOURS.get(kind, NOTICE_COLOURS["info"])
        self.last_notice = text
        self.notice.setText(text)
        self.notice.setStyleSheet(f"background:{bg};color:{fg};padding:10px 24px;font-weight:600;")
        self.notice.setVisible(True)
        QTimer.singleShot(9000, lambda: self._hide_notice(text))

    def _hide_notice(self, text: str) -> None:
        try:
            if self.notice.text() == text:
                self.notice.setVisible(False)
        except RuntimeError:                                     # the window is gone
            pass

    def send_log(self, view: Optional[dict] = None) -> None:                       # SYS-6
        try:
            path = desktop.make_log_zip(self.state.home, run=view, machine=self.state._machine)
        except OSError as e:
            self.notify(f"Could not make the log file: {e}", "error")
            return
        desktop.reveal_file(path)
        self.notify(f"Log saved as {path}. Send this file to Ryan.", "ok")

    # ------------------------------------------------------------------ every second
    CLEAN_EVERY = 3600                                            # seconds (STO-6)

    def tick(self) -> None:
        if self.state.closed:
            return
        if time.time() - self.cleaned_at > self.CLEAN_EVERY and not self.cleaning:
            self.clean_up()
        for r in self.state.store.list_runs():
            before = self.statuses.get(r["id"])
            if before == st.RUNNING and r["status"] in (st.FINISHED, st.FAILED) and self.tray and not self.isVisible():
                words = "finished" if r["status"] == st.FINISHED else "stopped with a problem"      # RUN-7
                self.tray.showMessage("Tree Profiling", f"{r['site']} {words}.", app_icon(), 10000)
            self.statuses[r["id"]] = r["status"]
        if self.page is not None and hasattr(self.page, "tick"):
            try:
                self.page.tick()
            except RuntimeError:
                pass

    def clean_up(self) -> None:
        """STO-6, off the main thread (deleting a big folder takes a moment). Not the run that is on screen."""
        self.cleaning, self.cleaned_at = True, time.time()
        showing = {getattr(self.page, "run_id", None)}

        def done(result, error) -> None:
            self.cleaning = False
            if error is None and (result["local"] or result["removed"]) and hasattr(self.page, "seen"):
                self.page.seen = None                                # Home draws its list again
        self.bg.run(lambda: self.state.auto_clean(skip=showing), done)

    # ------------------------------------------------------------------ files handed to the app
    def handle_paths(self, paths: list) -> None:
        """Dropped on the window, given on the command line, or double-clicked in Explorer (HOME-7, OPEN-1)."""
        paths = [str(p) for p in paths if p]
        clouds = [p for p in paths if Path(p).suffix.lower() in LAS_SUFFIXES]
        saved = next((p for p in paths if Path(p).suffix.lower() == projectfile.EXTENSION or Path(p).is_dir()), None)
        if clouds:
            if isinstance(self.page, NewRunPage):
                for p in clouds:
                    self.page.add_path(p)
            else:
                self.go_new(clouds)
        elif saved:
            self.go_saved(saved)
        elif paths:
            self.notify("Drop a .las or .laz point cloud to start a run, or a .treeprof file to open saved results.",
                        "warning")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        event.acceptProposedAction()
        if hasattr(self.page, "drop_files"):
            self.page.drop_files(paths)
        else:
            self.handle_paths(paths)

    # ------------------------------------------------------------------ closing (SYS-2)
    def busy(self) -> bool:
        return self.state.current() is not None or self.state.store.next_queued() is not None

    def closeEvent(self, event) -> None:
        if self.state.closed:                                    # already shut down
            event.accept()
            return
        if self.quitting or not self.busy():
            self.shut_down()
            event.accept()
            return
        answer = self.dialogs.ask("A run is still going",
                                  "Closing the window does not have to stop it. What do you want to do?",
                                  [KEEP, QUIT, STAY])
        if answer == QUIT:
            self.shut_down()
            event.accept()
        elif answer == KEEP:
            event.ignore()
            self.keep_in_background()
        else:
            event.ignore()

    def keep_in_background(self) -> None:
        if self.tray:
            self.hide()
            self.tray.showMessage("Tree Profiling", "The run keeps going. Click this icon to open the window again.",
                                  app_icon(), 8000)
        else:                                                    # no place next to the clock: stay in the taskbar
            self.showMinimized()

    def show_again(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        if self.busy():
            self.show_again()
            if self.dialogs.ask("A run is still going", "Quitting stops it. You can resume it the next time you open "
                                "the app.", [QUIT, STAY], danger=True) != QUIT:
                return
        self.quitting = True
        self.close()

    def shut_down(self) -> None:
        self.timer.stop()
        if self.tray:
            self.tray.hide()
        self.state.shutdown()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)


# ---------------------------------------------------------------------------------- only one copy (SYS-1)
def server_name() -> str:
    """One app per Windows account. TREEPROF_INSTANCE gives another name (the tests use it, so that they
    never talk to an app that is really open on the same computer)."""
    return os.environ.get("TREEPROF_INSTANCE") or "treeprof-" + re.sub(r"[^A-Za-z0-9]", "_", getpass.getuser() or "user")


ACK = b"ok\n"


def send_to_running(message: dict, name: Optional[str] = None, timeout_ms: int = 800, answer_ms: int = 5000) -> bool:
    """Hand a message to the copy of the app that is already open. False when there is none.
    The connection is kept open until the app answers that it has the message: closing it right after
    writing can lose the message on Windows when the app is busy at that moment."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(message).encode("utf-8") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.waitForReadyRead(answer_ms)                             # the app is open either way; this only waits for "ok"
    sock.disconnectFromServer()
    return True


def _someone_listens(name: str, timeout_ms: int = 300) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Listens for a second start of the app: it hands over the file to open and leaves."""
    received = Signal(dict)

    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connected)
        self.listening = False
        if _someone_listens(self.name):                          # another copy has the name: leave it alone
            return
        self.listening = self.server.listen(self.name)
        if not self.listening:                                   # a stale name left by a crash
            QLocalServer.removeServer(self.name)
            self.listening = self.server.listen(self.name)

    def _connected(self) -> None:
        while self.server.hasPendingConnections():
            self._serve(self.server.nextPendingConnection())

    def _serve(self, sock) -> None:
        """One message per connection: a line of JSON. Answer "ok", then pass the message on."""
        buffer = bytearray()

        def read() -> None:
            buffer.extend(bytes(sock.readAll()))
            if b"\n" not in buffer:
                return
            text = bytes(buffer).partition(b"\n")[0]
            buffer.clear()
            try:
                message = json.loads(text.decode("utf-8") or "{}")
            except ValueError:
                message = {}
            sock.write(ACK)
            sock.flush()
            self.received.emit(message if isinstance(message, dict) else {})
        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)
        if sock.bytesAvailable():
            read()

    def close(self) -> None:
        self.server.close()


def run(home, file: Optional[str] = None, show: bool = True) -> int:
    """Start the app (or hand the file to the copy that is already open)."""
    if os.name == "nt":                                          # its own button and icon in the Windows taskbar,
        try:                                                     # not the one of Python
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TreeProfiling.App")
        except (OSError, AttributeError):
            pass
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Tree Profiling")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    if send_to_running({"open": file}):
        return 0
    server = InstanceServer()
    state = AppState(home)
    win = MainWindow(state)

    def received(message: dict) -> None:
        if message.get("ping"):                                  # only asked whether the app is open
            return
        win.show_again()
        if message.get("open"):
            print(f"Asked to open {message['open']}", flush=True)
            win.handle_paths([message["open"]])
    server.received.connect(received)
    if file:
        win.handle_paths([file])
    if show:
        win.show()
    code = app.exec()
    server.close()
    state.shutdown()
    return code

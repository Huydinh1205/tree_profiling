"""Shared by all screens: the look, small building blocks, work off the main thread, and the
standard Windows dialogs (choose a file, yes / no questions)."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import checks, present

STYLE = """
QWidget { font-size: 14px; color: #1B2420; }
QMainWindow, QWidget#page, QScrollArea, QWidget#scrollbody { background: #F4F6F3; }
QScrollArea { border: none; }
QFrame#header { background: #FFFFFF; border-bottom: 1px solid #D9DFDA; }
QFrame#card { background: #FFFFFF; border: 1px solid #D9DFDA; border-radius: 12px; }
QFrame#dashed { background: #FFFFFF; border: 2px dashed #B9C3BC; border-radius: 12px; }
QFrame#line { background: #E3E8E4; max-height: 1px; min-height: 1px; border: none; }
QFrame#banner_error { background: #FBE5E2; border: 1px solid #E0A29B; border-radius: 12px; }
QFrame#banner_error QLabel { color: #8E2118; }
QFrame#banner_warning { background: #FFF6DA; border: 1px solid #E2C46B; border-radius: 12px; }
QFrame#banner_warning QLabel { color: #6B4700; }
QFrame#banner_ok { background: #E2F1E8; border: 1px solid #9CCFB0; border-radius: 12px; }
QFrame#banner_ok QLabel { color: #17553A; }
QFrame#chip { background: #FFFFFF; border: 1px solid #D9DFDA; border-radius: 8px; }
QLabel { background: transparent; border: none; }
QLabel[role="h1"] { font-size: 26px; font-weight: 600; }
QLabel[role="h2"] { font-size: 18px; font-weight: 600; }
QLabel[role="strong"] { font-weight: 600; }
QLabel[role="muted"] { color: #4E5A54; }
QLabel[role="big"] { font-size: 20px; font-weight: 600; }
QLabel[role="caps"] { color: #1D4F91; font-weight: 600; }
QPushButton { padding: 8px 16px; border-radius: 8px; border: 1px solid #B9C3BC; background: #FFFFFF; }
QPushButton:hover { background: #EEF2EE; }
QPushButton:focus { border: 2px solid #1F6B4A; }
QPushButton[kind="primary"] { background: #1F6B4A; color: #FFFFFF; border: 1px solid #1F6B4A; font-weight: 600; }
QPushButton[kind="primary"]:hover { background: #17553A; }
QPushButton[kind="danger"] { color: #8E2118; border: 1px solid #E0A29B; }
QPushButton[kind="flat"] { border: 1px solid transparent; background: transparent; color: #1F6B4A; font-weight: 600; }
QPushButton[kind="flat"]:hover { background: #E2F1E8; }
QPushButton[kind="link"] { border: 1px solid transparent; background: transparent; font-weight: 600; text-align: left; padding: 4px 0px; }
QPushButton[kind="link"]:hover { color: #1F6B4A; }
QPushButton[kind="brand"] { border: 1px solid transparent; background: transparent; font-size: 18px; font-weight: 600; color: #1F6B4A; }
QPushButton:disabled { background: #E3E8E4; color: #8C9891; border: 1px solid #E3E8E4; }
QLineEdit { padding: 7px 10px; border: 1px solid #B9C3BC; border-radius: 8px; background: #FFFFFF; }
QLineEdit:focus { border: 2px solid #1F6B4A; }
QComboBox, QSpinBox { min-height: 32px; }
QProgressBar { border: none; background: #E3E8E4; border-radius: 4px; max-height: 8px; min-height: 8px; }
QProgressBar::chunk { background: #1D4F91; border-radius: 4px; }
QPlainTextEdit { background: #1B2420; color: #E6EDE8; border-radius: 8px; font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 13px; }
QTableView { background: #FFFFFF; border: none; gridline-color: #E3E8E4; selection-background-color: #CFE8D8; selection-color: #1B2420; }
QHeaderView::section { background: #F4F6F3; border: none; border-bottom: 1px solid #D9DFDA; padding: 8px; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 8px 18px; border: none; border-bottom: 3px solid transparent; background: transparent; font-weight: 600; color: #4E5A54; }
QTabBar::tab:selected { border-bottom: 3px solid #1F6B4A; color: #1B2420; }
QCheckBox { spacing: 8px; }
QDialog, QMessageBox, QInputDialog { background: #FFFFFF; }
QToolTip { background: #1B2420; color: #FFFFFF; border: none; padding: 6px; }
"""

LIGHT = {                                    # the app is always light, also when Windows is set to dark mode
    QPalette.Window: "#F4F6F3", QPalette.WindowText: "#1B2420", QPalette.Base: "#FFFFFF",
    QPalette.AlternateBase: "#F4F6F3", QPalette.Text: "#1B2420", QPalette.PlaceholderText: "#7A8680",
    QPalette.Button: "#FFFFFF", QPalette.ButtonText: "#1B2420", QPalette.BrightText: "#FFFFFF",
    QPalette.ToolTipBase: "#FFFFFF", QPalette.ToolTipText: "#1B2420", QPalette.Highlight: "#1F6B4A",
    QPalette.HighlightedText: "#FFFFFF", QPalette.Link: "#1F6B4A", QPalette.Light: "#FFFFFF",
    QPalette.Midlight: "#EEF2EE", QPalette.Mid: "#B9C3BC", QPalette.Dark: "#8C9891", QPalette.Shadow: "#4E5A54",
}
LIGHT_DISABLED = {QPalette.WindowText: "#8C9891", QPalette.Text: "#8C9891", QPalette.ButtonText: "#8C9891",
                  QPalette.Base: "#F4F6F3", QPalette.Button: "#E3E8E4"}


def light_palette() -> QPalette:
    palette = QPalette()
    for role, colour in LIGHT.items():
        palette.setColor(role, QColor(colour))
    for role, colour in LIGHT_DISABLED.items():
        palette.setColor(QPalette.Disabled, role, QColor(colour))
    return palette


def apply_look(app) -> None:
    """Fusion, the app's light colours and its style sheet. Without the palette, Windows in dark mode gives the
    boxes a dark background while the style sheet keeps the text dark: dark on dark."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)          # Qt 6.8 and newer
    except AttributeError:
        pass
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLE)



# ---------------------------------------------------------------------------------- building blocks
def label(text: str = "", role: str = "", wrap: bool = False, name: str = "") -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if wrap:
        w.setWordWrap(True)
    if name:
        w.setObjectName(name)
    w.setTextInteractionFlags(Qt.TextSelectableByMouse if role in ("", "muted") and wrap else Qt.NoTextInteraction)
    return w


def button(text: str, on_click: Optional[Callable] = None, kind: str = "", name: str = "", tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if name:
        b.setObjectName(name)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    b.setCursor(Qt.PointingHandCursor)
    return b


def frame(name: str = "card", margins=(20, 16, 20, 16), spacing: int = 10, horizontal: bool = False):
    """A box with a layout inside. Returns (frame, layout)."""
    f = QFrame()
    f.setObjectName(name)
    lay = QHBoxLayout(f) if horizontal else QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


def line() -> QFrame:
    f = QFrame()
    f.setObjectName("line")
    return f


def row(*widgets, spacing: int = 10, stretch_last: bool = False) -> QHBoxLayout:
    """Widgets side by side. None in the list is a stretch (pushes what follows to the right)."""
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w, 100)                                 # a column of text takes the room there is
    return lay


def column(*widgets, spacing: int = 2) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def pill(text: str, sign: str, bg: str, fg: str) -> QLabel:
    w = QLabel(f"{sign}  {text}")
    w.setStyleSheet(f"background:{bg};color:{fg};border-radius:9px;padding:3px 10px;font-weight:600;")
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return w


def status_pill(status: str) -> QLabel:
    return pill(*present.STATUS.get(status, (status, "?", "#ECEFEC", "#3A453F")))


def check_row(c: checks.Check) -> QWidget:
    """One line of a check: sign, what was found, and what to do about it."""
    sign, colour = present.LEVEL[c.level]
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    mark = QLabel(sign)
    mark.setStyleSheet(f"color:{colour};font-weight:700;")
    mark.setFixedWidth(16)
    mark.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    text = label(c.text, wrap=True)
    if c.level != checks.OK:
        text.setStyleSheet(f"color:{colour};")
    col = column(text)
    if c.help:
        col.addWidget(label(c.help, "muted", wrap=True))
    lay.addWidget(mark)
    lay.addLayout(col, 1)
    return w


def clear(layout) -> None:
    """Remove everything from a layout."""
    while layout.count():
        item = layout.takeAt(0)
        widget, inner = item.widget(), item.layout()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif inner is not None:
            clear(inner)


class Page(QWidget):
    """A screen that scrolls when it is taller than the window. Add things to self.body.
    A page may have tick() (called every second) and drop_files(paths)."""

    def __init__(self, win, max_width: int = 1240):
        super().__init__()
        self.win, self.state = win, win.state
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("scrollbody")
        centre = QHBoxLayout(inner)
        centre.setContentsMargins(24, 20, 24, 24)
        holder = QWidget()
        holder.setMaximumWidth(max_width)
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        centre.addStretch(1)
        centre.addWidget(holder, 100)
        centre.addStretch(1)
        area.setWidget(inner)
        outer.addWidget(area)

    def end(self) -> None:
        """Call after the last widget: keeps everything at the top."""
        self.body.addStretch(1)


# ---------------------------------------------------------------------------------- work off the main thread
class Background(QObject):
    """Run something slow (asking Docker, reading a file header, copying) without freezing the window.
    on_done(result, error) is called back on the main thread."""
    _finished = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self._finished.connect(self._deliver)
        self.running = 0

    def run(self, work: Callable, on_done: Optional[Callable] = None) -> None:
        self.running += 1
        threading.Thread(target=self._work, args=(work, on_done), name="treeprof-bg", daemon=True).start()

    def _work(self, work, on_done) -> None:
        try:
            result, error = work(), None
        except Exception as e:                                    # handed to on_done, which decides what to show
            result, error = None, e
        self._finished.emit(on_done, result, error)

    def _deliver(self, on_done, result, error) -> None:
        self.running -= 1
        if on_done is None:
            return
        try:
            on_done(result, error)
        except RuntimeError:                                      # the screen that asked is already closed
            pass


# ---------------------------------------------------------------------------------- Windows dialogs
class Dialogs:
    """The standard dialogs. The tests replace this object, so no test ever waits for a click."""

    def __init__(self, parent: Optional[QWidget] = None):
        self.parent = parent

    def pick_files(self, initial: str = "") -> Optional[list]:
        files, _ = QFileDialog.getOpenFileNames(self.parent, "Choose point clouds", initial or "",
                                                "Point clouds (*.las *.laz);;All files (*)")
        return files or None

    def pick_folder(self, initial: str = "") -> Optional[str]:
        return QFileDialog.getExistingDirectory(self.parent, "Choose a folder", initial or "") or None

    def pick_project(self, initial: str = "") -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(self.parent, "Open saved results", initial or "",
                                              "Tree Profiling results (*.treeprof);;All files (*)")
        return path or None

    def pick_program(self, initial: str = "") -> Optional[str]:
        path, _ = QFileDialog.getOpenFileName(self.parent, "Choose CloudCompare.exe", initial or "",
                                              "Programs (*.exe);;All files (*)")
        return path or None

    def ask(self, title: str, text: str, buttons: list, danger: bool = False) -> Optional[str]:
        """A question with named buttons. Returns the text of the button pressed; the LAST button is the
        safe one (also chosen by Esc or closing the box)."""
        box = QMessageBox(self.parent)
        box.setWindowTitle("Tree Profiling")
        box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
        box.setText(title)
        box.setInformativeText(text)
        made = [box.addButton(b, QMessageBox.RejectRole if i == len(buttons) - 1 else QMessageBox.AcceptRole)
                for i, b in enumerate(buttons)]
        box.setDefaultButton(made[-1] if danger else made[0])
        box.setEscapeButton(made[-1])
        box.exec()
        clicked = box.clickedButton()
        return clicked.text() if clicked else buttons[-1]

    def ask_text(self, title: str, text: str) -> Optional[str]:
        value, ok = QInputDialog.getText(self.parent, title, text)
        return value if ok else None

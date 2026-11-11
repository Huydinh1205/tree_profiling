"""The Results screen: overview, crown map, the selected tree in 3D, the tree table, run info
(RES-1 to RES-10, OPEN-1 to OPEN-8)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGraphicsEllipseItem, QLabel, QGraphicsPolygonItem, QGraphicsScene,
                               QGraphicsView, QGridLayout, QHeaderView, QLineEdit, QMenu, QScrollArea, QSplitter,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from .. import desktop, present, projectfile
from ..appstate import Results
from .common import button, clear, column, frame, label, line, pill, row

DARK = QColor("#1B2420")
MAX_PIXELS_PER_METRE = 200.0


# ---------------------------------------------------------------------------------- which screen to show
def results_page(win, view: dict) -> QWidget:
    """A finished run: its results, or why they cannot be shown."""
    from .pages import MessagePage
    if not view["trees"]:                                                         # RES-10
        def delete():
            if win.dialogs.ask("Delete this run?", "Everything this run made on this computer is removed.",
                               ["Delete", "Keep it"], danger=True) == "Delete":
                win.state.delete_run(view["id"])
                win.go_home()
        return MessagePage(win, "No trees were found in this file",
                           "The run finished, but the tree-finding step did not find any tree in this point cloud.",
                           heading=present.run_title(view), extra=[button("Delete this run", delete, name="delete_run")])
    path = win.state.project_path_for(view["id"])
    if path is None:                                                              # STO-4
        return MessagePage(win, "Not found", "The local copy was deleted and the saved folder was moved or deleted.",
                           heading=present.run_title(view))
    return open_page(win, path, view)


def open_page(win, path, view: Optional[dict] = None) -> QWidget:
    """Results from a .treeprof file (OPEN-1 to OPEN-7), or a plain message when it cannot be opened."""
    from .pages import MessagePage
    if not path:
        return MessagePage(win, "Nothing to open", "Choose a .treeprof file with 'Open saved results'.")
    try:
        if Path(path).is_dir():
            path = projectfile.find_in_folder(path)
        results = win.state.open_results(path)
        if view is None:
            win.state.remember_opened(results)                     # HOME-5
    except projectfile.ProjectFileError as e:
        return MessagePage(win, "These results cannot be opened", str(e))
    except (OSError, ValueError) as e:
        return MessagePage(win, "These results cannot be opened", f"{Path(path).name}: {e}")
    return ResultsView(win, results, view)


# ---------------------------------------------------------------------------------- crown map
class CrownMap(QGraphicsView):
    """RES-3: every crown seen from above, to scale. Wheel zooms, dragging moves, a click picks a tree."""
    picked = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("crown_map")
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor("#FFFFFF")))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(170)
        self.items_by_id, self.chosen, self.whole, self._press = {}, None, True, None
        self.edge = QPen(QColor("#FFFFFF"), 0)                    # width 0: always one pixel, however far zoomed
        self.mark = QPen(DARK, 3)
        self.mark.setCosmetic(True)

    def set_shapes(self, shapes: list, width: float, height: float) -> None:
        scene = self.scene()
        scene.clear()
        self.items_by_id, self.chosen = {}, None
        pen = self.edge if len(shapes) <= 5000 else QPen(Qt.NoPen)
        self.plain_pen = pen
        self.setRenderHint(QPainter.Antialiasing, len(shapes) <= 5000)
        for tree_id, kind, geo in shapes:
            if kind == "ring":
                item = QGraphicsPolygonItem(QPolygonF([QPointF(x, y) for x, y in geo]))
            else:
                x, y, r = geo
                item = QGraphicsEllipseItem(QRectF(x - r, y - r, 2 * r, 2 * r))
            item.setPen(pen)
            item.setData(0, tree_id)
            scene.addItem(item)
            self.items_by_id[tree_id] = item
        margin = max(width, height) * 0.03
        scene.setSceneRect(QRectF(-width / 2 - margin, -height / 2 - margin, width + 2 * margin, height + 2 * margin))
        self.fit()

    def set_colours(self, colours: dict) -> None:
        """colours: tree_id -> (r, g, b)."""
        for tree_id, item in self.items_by_id.items():
            item.setBrush(QBrush(QColor(*colours.get(tree_id, (0xCF, 0xE8, 0xD8)))))

    def select(self, tree_id) -> None:                                             # RES-5
        if self.chosen is not None and self.chosen in self.items_by_id:
            old = self.items_by_id[self.chosen]
            old.setPen(self.plain_pen)
            old.setZValue(0)
        self.chosen = tree_id
        item = self.items_by_id.get(tree_id)
        if item is not None:
            item.setPen(self.mark)
            item.setZValue(1)

    # ---- moving around
    def fit(self) -> None:
        self.whole = True
        if not self.scene().sceneRect().isEmpty():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def pixels_per_metre(self) -> float:
        return self.transform().m11()

    def zoom(self, factor: float) -> None:
        rect = self.scene().sceneRect()
        if rect.isEmpty() or self.viewport().width() < 2:
            return
        lowest = min(self.viewport().width() / rect.width(), self.viewport().height() / rect.height())
        now = self.pixels_per_metre()
        target = min(max(now * factor, lowest), max(MAX_PIXELS_PER_METRE, lowest))
        if target <= lowest * 1.001:
            self.fit()
            return
        self.whole = False
        self.scale(target / now, target / now)

    def centre_on(self, tree_id) -> None:
        item = self.items_by_id.get(tree_id)
        if item is not None and not self.whole:
            self.centerOn(item)

    def wheelEvent(self, event) -> None:
        self.zoom(1.25 ** (event.angleDelta().y() / 120.0))
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.whole:
            self.fit()

    def mousePressEvent(self, event) -> None:
        self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        pos = event.position().toPoint()
        if self._press is not None and (pos - self._press).manhattanLength() < 5:      # a click, not a drag
            self.pick_at(pos)
        self._press = None

    def pick_at(self, pos) -> None:
        item = self.itemAt(pos)
        if item is not None and item.data(0) is not None:
            self.picked.emit(int(item.data(0)))

    def position_of(self, tree_id):
        """Where the middle of a tree's crown is drawn, in the widget (for the tests)."""
        item = self.items_by_id[tree_id]
        return self.mapFromScene(item.sceneBoundingRect().center())


class Legend(QWidget):
    """The colour scale of the map, light to dark."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(170, 12)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, self.width(), 0)
        for i, c in enumerate(present.RAMP):
            grad.setColorAt(i / (len(present.RAMP) - 1), QColor(c))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(self.rect(), 5, 5)


# ---------------------------------------------------------------------------------- one tree in 3D
class TreeView3D(QWidget):
    """RES-7: the points of one tree. Drag to turn, wheel to zoom, double-click to reset.
    Drawn by the app itself (no graphics driver needed), so it also works through Remote Desktop."""

    def __init__(self):
        super().__init__()
        self.setObjectName("tree_3d")
        self.setMinimumSize(240, 110)
        self.setCursor(Qt.OpenHandCursor)
        self.points = self.colours = None
        self.reset()
        self._last = None

    def reset(self) -> None:
        self.turn, self.tilt, self.zoomed = math.radians(35), math.radians(12), 1.0
        self.update()

    def set_points(self, points) -> None:
        """points: (n, 3) array, the tree standing on z = 0 around x = y = 0."""
        import numpy as np
        self.points = np.asarray(points, dtype="float64")
        z = self.points[:, 2]
        span = float(z.max() - z.min()) or 1.0
        stops = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in present.RAMP], dtype="float64")
        f = (z - z.min()) / span * (len(stops) - 1)
        i = np.clip(f.astype(int), 0, len(stops) - 2)
        k = (f - i)[:, None]
        self.colours = (stops[i] + (stops[i + 1] - stops[i]) * k).astype("uint8")
        self.tree_height = float(z.max())
        self.reach = max(self.tree_height, float(np.abs(self.points[:, :2]).max()) * 2, 2.0)
        self.reset()

    def render_image(self, width: int, height: int):
        """The picture as a (height, width, 3) array of bytes."""
        import numpy as np
        img = np.empty((height, width, 3), dtype="uint8")
        img[:] = (0x1B, 0x24, 0x20)
        if self.points is None or not len(self.points):
            return img
        x, y, z = self.points[:, 0], self.points[:, 1], self.points[:, 2] - self.tree_height / 2
        ca, sa, ce, se = math.cos(self.turn), math.sin(self.turn), math.cos(self.tilt), math.sin(self.tilt)
        across = x * ca - y * sa
        away = x * sa + y * ca
        up = z * ce + away * se
        depth = away * ce - z * se
        scale = min(width, height) * 0.88 / self.reach * self.zoomed
        px = (width / 2 + across * scale).astype(int)
        py = (height / 2 - up * scale).astype(int)
        order = np.argsort(-depth)                               # far points first, near ones drawn over them
        px, py, col = px[order], py[order], self.colours[order]
        size = 2 if scale * self.reach < 900 else 3
        for dx in range(size):
            for dy in range(size):
                ok = (px + dx >= 0) & (px + dx < width) & (py + dy >= 0) & (py + dy < height)
                img[py[ok] + dy, px[ok] + dx] = col[ok]
        return img

    def paintEvent(self, event) -> None:
        w, h = max(self.width(), 2), max(self.height(), 2)
        img = self.render_image(w, h)
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format_RGB888)
        QPainter(self).drawImage(0, 0, qimg)

    def mousePressEvent(self, event) -> None:
        self._last = event.position()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._last is None:
            return
        d = event.position() - self._last
        self._last = event.position()
        self.turn += d.x() * 0.01
        self.tilt = min(max(self.tilt + d.y() * 0.01, -math.pi / 2), math.pi / 2)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._last = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()

    def wheelEvent(self, event) -> None:
        self.zoomed = min(max(self.zoomed * 1.2 ** (event.angleDelta().y() / 120.0), 0.4), 12.0)
        self.update()
        event.accept()


# ---------------------------------------------------------------------------------- tree table
class TreeTableModel(QAbstractTableModel):
    """RES-4: one row per tree. Only the rows on screen are ever asked for, so 50,000 trees are no problem."""

    def __init__(self, trees: list):
        super().__init__()
        self.rows = trees
        self.columns = [name for name, _, shown in present.COLUMNS if shown]
        self.labels = {name: text for name, text, _ in present.COLUMNS}
        self.sorted_by = None                                    # (column name, descending)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        name = self.columns[index.column()]
        if role == Qt.DisplayRole:
            value = self.rows[index.row()].get(name)
            return str(value) if name in present.TEXT_COLUMNS and value is not None else present.fmt_value(value)
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignLeft if name in present.TEXT_COLUMNS else Qt.AlignRight) | Qt.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return self.labels[self.columns[section]]
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignLeft if self.columns[section] in present.TEXT_COLUMNS else Qt.AlignRight) | Qt.AlignVCenter)
        return None

    def set_rows(self, rows: list) -> None:
        self.beginResetModel()
        self.rows = present.sort_trees(rows, *self.sorted_by) if self.sorted_by else rows
        self.endResetModel()

    def set_columns(self, names: list) -> None:
        self.beginResetModel()
        self.columns = [name for name, _, _ in present.COLUMNS if name in names]
        self.endResetModel()

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns):
            return
        self.layoutAboutToBeChanged.emit()
        self.sorted_by = (self.columns[column], order == Qt.DescendingOrder)
        self.rows = present.sort_trees(self.rows, *self.sorted_by)
        self.layoutChanged.emit()

    def row_of(self, tree_id) -> Optional[int]:
        for i, t in enumerate(self.rows):
            if t.get("tree_id") == tree_id:
                return i
        return None

    def tree_id_at(self, row_number: int):
        return self.rows[row_number].get("tree_id") if 0 <= row_number < len(self.rows) else None


# ---------------------------------------------------------------------------------- the screen
class ResultsView(QWidget):
    def __init__(self, win, res: Results, view: Optional[dict]):
        super().__init__()
        self.setObjectName("page")
        self.win, self.state, self.res, self.view = win, win.state, res, view
        self.trees = res.trees
        present.add_labels(self.trees)
        self.from_workdir = view is not None and Path(view["workdir"]) == res.folder
        self.selected = present.tallest(self.trees)
        self.colour_key = "height_m"
        self._picking = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 10, 24, 12)
        outer.setSpacing(6)
        self.head = QVBoxLayout()
        outer.addLayout(self.head)
        self.show_head()
        for _, p in res.missing_optional:
            outer.addWidget(label(f"{p.name} is not in this folder, so the 3D view is not available.", name="missing"))

        tabs = QTabWidget()
        tabs.setObjectName("tabs")
        tabs.addTab(self.trees_tab(), "Trees")
        self.info_holder = QScrollArea()
        self.info_holder.setWidgetResizable(True)
        tabs.addTab(self.info_holder, "Run info")
        outer.addWidget(tabs, 1)
        self.tabs = tabs
        self.show_info()
        self.recolour()
        self.pick(self.selected, "start")

    # ---- header (RES-9)
    def show_head(self) -> None:
        clear(self.head)
        project = self.res.project
        saved = bool(self.view and self.state.store.saves(self.view["id"])) or not self.from_workdir
        who = project.get("run_by") or ""
        src = Path(str((project.get("input") or {}).get("path") or "")).name
        right = []
        if self.from_workdir:
            right.append(pill("Saved", "✓", "#E2F1E8", "#17553A") if saved else pill("Not saved", "!", "#FFF1C9", "#6B4700"))
            right[-1].setObjectName("saved_pill")
        if self.state.settings.cloudcompare and self.res.info_laz:
            right.append(button("Open in CloudCompare", self.cloudcompare, name="cloudcompare"))
        right.append(button("Open folder", lambda: desktop.open_folder(self.res.folder), name="open_folder"))
        right.append(button("Save" if self.from_workdir else "Save a copy", self.save, "primary", name="save"))
        self.head.addLayout(row(
            button("‹  Back to runs", self.win.go_home, "flat", name="back"),
            column(label(present.title_of(project["site_name"], project.get("capture_date")), "h1", name="title"),
                   label(" · ".join(x for x in (f"Run by {who}" if who else "", src) if x), "muted")), None, *right))

    def cloudcompare(self) -> None:
        if not desktop.open_with(self.state.settings.cloudcompare, self.res.info_laz):
            self.win.notify("CloudCompare could not be started. Check its path in Settings.", "error")

    def save(self) -> None:
        from .dialogs import SaveDialog
        self.dialog = SaveDialog(self.win, self.res, self.view if self.from_workdir else None, self.after_save)
        self.dialog.open()

    def after_save(self) -> None:
        self.show_head()
        self.show_info()

    # ---- Trees tab
    def trees_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("scrollbody")
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(8)
        cards = []
        for name, value, note in present.summary_cards(self.res.summary):
            card, c = frame(margins=(14, 6, 14, 6), spacing=0)
            c.addWidget(label(name, "muted"))
            c.addWidget(label(value, "big", name="card_" + name.lower().replace(" ", "_")))
            if note:
                card.setToolTip(note)
                c.itemAt(0).widget().setText(f"{name} ({note})" if len(note) < 20 else name)
            cards.append(card)
        cards_row = row(*cards)
        for i in range(len(cards)):
            cards_row.setStretch(i, 1)
        lay.addLayout(cards_row)

        # the map
        map_card, m = frame(margins=(14, 10, 14, 10), spacing=6)
        self.colour = QComboBox()
        self.colour.setObjectName("colour_by")
        for key, text in present.COLOUR_BY.items():
            self.colour.addItem(text, key)
        self.colour.currentIndexChanged.connect(lambda _: self.set_colour(self.colour.currentData()))
        m.addLayout(row(label("Crown map (seen from above)", "strong"),
                        label("Scroll to zoom, drag to move, click a crown to see that tree.", "muted"), None,
                        label("Colour by", "muted"), self.colour))
        self.map = CrownMap()
        self.map.set_shapes(*present.map_shapes(self.trees, self.res.crown_outlines()))
        self.map.picked.connect(lambda tree_id: self.pick(tree_id, "map"))
        m.addWidget(self.map, 1)
        self.legend = row(spacing=8)
        m.addLayout(row(self.legend, None,
                        button("+", lambda: self.map.zoom(1.6), name="zoom_in", tip="Zoom in"),
                        button("−", lambda: self.map.zoom(1 / 1.6), name="zoom_out", tip="Zoom out"),
                        button("Show the whole site", self.map.fit, "flat", name="whole_site")))
        self.map.setToolTip("Each shape is the crown of one tree, drawn to scale.")

        # the selected tree
        tree_card, t = frame(margins=(14, 10, 14, 10), spacing=6)
        tree_card.setFixedWidth(380)
        self.panel = t
        top = QWidget()
        top_lay = row(map_card, tree_card, spacing=12)
        top_lay.setStretch(0, 1)
        top.setLayout(top_lay)
        top.setMinimumHeight(322)

        # the table
        table_card, tb = frame(margins=(14, 10, 14, 10), spacing=8)
        self.find, self.hmin, self.hmax = QLineEdit(), QLineEdit(), QLineEdit()
        for w, name, hint, width in ((self.find, "find", "Find a tree", 220), (self.hmin, "hmin", "Height from (m)", 150),
                                     (self.hmax, "hmax", "to (m)", 110)):
            w.setObjectName(name)
            w.setPlaceholderText(hint)
            w.setFixedWidth(width)
            w.setClearButtonEnabled(True)
            w.textChanged.connect(lambda _: self.refilter())
        self.qc = QComboBox()
        self.qc.setObjectName("qc")
        for key, text in (("all", "All trees"), ("ok", "OK only"), ("check", "To check only")):
            self.qc.addItem(text, key)
        self.qc.currentIndexChanged.connect(lambda _: self.refilter())
        self.count = label("", "muted", name="count")
        self.columns_button = button("Columns", name="columns")
        menu = QMenu(self.columns_button)
        self.column_actions = {}
        for name, text, shown in present.COLUMNS[1:]:
            act = QAction(text, menu, checkable=True)
            act.setChecked(shown)
            act.toggled.connect(lambda _: self.apply_columns())
            menu.addAction(act)
            self.column_actions[name] = act
        self.columns_button.setMenu(menu)
        tb.addLayout(row(self.find, self.hmin, self.hmax, self.qc, None, self.count, self.columns_button))
        self.model = TreeTableModel(self.trees)
        self.table = QTableView()
        self.table.setObjectName("table")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(96)
        self.table.selectionModel().currentRowChanged.connect(self.row_chosen)
        self.model.modelReset.connect(self.reselect)
        self.model.layoutChanged.connect(self.reselect)
        tb.addWidget(self.table, 1)

        split = QSplitter(Qt.Vertical)
        split.setObjectName("split")
        split.addWidget(top)
        split.addWidget(table_card)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        split.setSizes([560, 400])
        lay.addWidget(split, 1)
        self.refilter()
        tab.setMinimumHeight(590)                                # on a low screen the tab scrolls; nothing overlaps
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(tab)
        return area

    # ---- map colours
    def set_colour(self, key: str) -> None:
        self.colour_key = key
        self.recolour()

    def recolour(self) -> None:
        colours, legend = present.map_colours(self.trees, self.colour_key)
        self.map.set_colours(colours)
        clear(self.legend)
        if legend[0] == "scale":
            for w in (label(present.fmt_value(float(legend[1])), "muted", name="legend_lo"), Legend(),
                      label(present.fmt_value(float(legend[2])), "muted", name="legend_hi"),
                      label(present.COLOUR_BY[self.colour_key], "muted")):
                self.legend.addWidget(w)
        else:                                                    # quality check: one swatch per class, with words
            for i, (rgb, words, count) in enumerate(legend[1]):
                swatch = QLabel()
                swatch.setFixedSize(14, 14)
                swatch.setStyleSheet(f"background: rgb({rgb[0]},{rgb[1]},{rgb[2]}); border: 1px solid #8C9891; border-radius: 3px;")
                self.legend.addWidget(swatch)
                self.legend.addWidget(label(f"{words} ({count:,})", "muted", name=f"legend_class_{i}"))
        self.legend.addStretch(1)

    # ---- table
    @staticmethod
    def _number(text: str) -> Optional[float]:
        try:
            return float(text.replace(",", "."))
        except ValueError:
            return None

    def refilter(self) -> None:
        rows = present.filter_trees(self.trees, self.find.text(), self._number(self.hmin.text()),
                                    self._number(self.hmax.text()), self.qc.currentData())
        self.model.set_rows(rows)
        self.count.setText(f"Showing {len(rows):,} of {len(self.trees):,} trees")

    def apply_columns(self) -> None:
        self.model.set_columns(["tree_uid"] + [n for n, a in self.column_actions.items() if a.isChecked()])

    def reselect(self, *_) -> None:
        """Keep the row of the selected tree marked after sorting, filtering or changing columns."""
        self._picking = True
        try:
            i = self.model.row_of(self.selected)
            if i is None:
                self.table.clearSelection()
            else:
                self.table.selectRow(i)
        finally:
            self._picking = False

    def row_chosen(self, current, previous) -> None:
        if self._picking or not current.isValid():
            return
        self.pick(self.model.tree_id_at(current.row()), "table")

    # ---- RES-5: one tree selected everywhere
    def pick(self, tree_id, source: str) -> None:
        if tree_id is None or tree_id not in self.res.by_id:
            return
        self.selected = tree_id
        self.map.select(tree_id)
        if source == "table":
            self.map.centre_on(tree_id)
        if source != "table":
            self._picking = True
            try:
                i = self.model.row_of(tree_id)
                if i is not None:
                    self.table.selectRow(i)
                    self.table.scrollTo(self.model.index(i, 0), QAbstractItemView.PositionAtCenter)
            finally:
                self._picking = False
        self.show_tree()

    def show_tree(self) -> None:                                                   # RES-6, RES-7
        clear(self.panel)
        tree = self.res.by_id.get(self.selected)
        self.panel.addWidget(label("Selected tree", "muted"))
        if tree is None:
            self.panel.addWidget(label("No tree selected", "strong"))
            self.panel.addStretch(1)
            return
        self.panel.addWidget(label(tree.get("tree_uid") or f"Tree {self.selected}", "h2", wrap=True, name="selected_tree"))
        status, got = self.state.tree_points(self.res)
        if status == "ready":
            pts = got.tree(self.selected)
            if pts is None:
                self.panel.addWidget(label("No points are stored for this tree.", "muted", name="view_note"))
            else:
                self.view3d = TreeView3D()
                self.view3d.set_points(pts)
                self.view3d.setToolTip("Drag to turn, scroll to zoom, double-click to reset. Points are coloured by height.")
                self.panel.addWidget(self.view3d, 1)
        elif status == "preparing":
            self.panel.addWidget(label("Preparing 3D view...", "muted", name="view_note"))
            QTimer.singleShot(1500, self._retry_view)
        else:
            self.panel.addWidget(label(f"3D view not available: {got}", "muted", wrap=True, name="view_note"))
        facts = present.tree_facts(tree)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for i, (name, value) in enumerate(facts[:-1]):           # two per line: "Height  23.6 m"
            cell = row(label(name, "muted"), label(value, "strong", name="fact_" + name.lower().replace(" ", "_")), None,
                       spacing=6)
            grid.addLayout(cell, i // 2, i % 2)
        self.panel.addLayout(grid)
        self.panel.addLayout(row(label(facts[-1][0], "muted"), label(facts[-1][1], "strong", name="fact_quality_check"),
                                 None, spacing=6))
        note = present.qc_help(tree)
        if note and (tree.get("qc_flag") or "ok") != "ok":
            self.panel.addWidget(label(note, "muted", wrap=True, name="qc_help"))
        if status != "ready":
            self.panel.addStretch(1)

    def _retry_view(self) -> None:
        try:
            self.show_tree()
        except RuntimeError:                                     # the screen was closed meanwhile
            pass

    # ---- Run info (RES-8)
    def show_info(self) -> None:
        saves = self.state.store.saves(self.view["id"]) if self.view else None
        card, lay = frame(margins=(20, 8, 20, 8), spacing=0)
        for i, (name, value) in enumerate(present.run_info_rows(self.res.project, saves)):
            if i:
                lay.addWidget(line())
            left = label(name, "muted")
            left.setFixedWidth(260)
            holder = QWidget()
            hl = row(left, label(value, wrap=True, name="info_" + name.lower().replace(" ", "_")))
            hl.setContentsMargins(0, 9, 0, 9)
            hl.setStretch(1, 1)
            holder.setLayout(hl)
            lay.addWidget(holder)
        lay.addStretch(1)
        self.info_holder.setWidget(card)

    def drop_files(self, paths: list) -> None:
        self.win.handle_paths(paths)

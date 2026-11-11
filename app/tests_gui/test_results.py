"""The Results screen: crown map, 3D view of a tree, table, and speed on a big site (RES-1 to RES-9, NFR-2)."""
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtTest import QTest

from treeprof_app import present
from treeprof_app.gui.results import CrownMap, ResultsView, TreeTableModel, TreeView3D

from conftest import make_saved


def open_saved(h, n, points=False) -> ResultsView:
    h.win.go_saved(make_saved(h.tmp / f"saved_{n}", n, points))
    view = h.win.page
    assert isinstance(view, ResultsView)
    h.pump()
    return view


def wheel(widget, steps: int) -> None:
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120 * steps), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    widget.wheelEvent(event)


# ---------------------------------------------------------------------------------- overview, selection
def test_overview_and_the_tallest_tree_first(make):                             # RES-1, RES-6
    h = make()
    view = open_saved(h, 60)
    assert h.text("card_trees") == "60" and h.text("card_tallest") == "34.0 m"
    assert h.text("card_trees_to_check") == "3"                    # trees 17, 34 and 51 are at the edge
    assert view.selected == 29 and h.text("selected_tree") == "TEST_SITE_TREE00029"     # 5 + 29 = 34 m
    assert h.text("fact_height") == "34.0 m" and h.text("fact_quality_check") == "OK"
    assert view.map.chosen == 29
    assert view.model.tree_id_at(view.table.currentIndex().row()) == 29


def test_clicking_a_crown_selects_the_tree_everywhere(make):                    # RES-5
    h = make()
    view = open_saved(h, 60)
    QTest.mouseClick(view.map.viewport(), Qt.LeftButton, Qt.NoModifier, view.map.position_of(17))
    assert view.selected == 17 and h.text("selected_tree") == "TEST_SITE_TREE00017"
    assert h.text("fact_quality_check") == "Check: at the edge" and "within 2 m of the edge" in h.text("qc_help")
    assert view.model.tree_id_at(view.table.currentIndex().row()) == 17
    assert view.map.items_by_id[17].pen().widthF() == 3 and view.map.items_by_id[29].pen().widthF() != 3
    QTest.mouseClick(view.map.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))    # empty corner: nothing changes
    assert view.selected == 17


def test_dragging_the_map_does_not_select(make):
    h = make()
    view = open_saved(h, 60)
    view.map.zoom(3.0)
    start = view.map.position_of(17)
    QTest.mousePress(view.map.viewport(), Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(view.map.viewport(), start + QPoint(40, 25))
    QTest.mouseRelease(view.map.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(40, 25))
    assert view.selected == 29


def test_clicking_a_row_selects_the_tree(make):
    h = make()
    view = open_saved(h, 60)
    view.table.selectRow(4)
    assert view.selected == 5 and view.map.chosen == 5 and h.text("selected_tree") == "TEST_SITE_TREE00005"


# ---------------------------------------------------------------------------------- map
def test_map_is_to_scale_and_can_be_zoomed(make):                               # RES-3
    h = make()
    view = open_saved(h, 60)
    m = view.map
    assert len(m.items_by_id) == 60
    t = m.transform()
    assert abs(t.m11() - t.m22()) < 1e-9                           # one metre is as long across as up
    whole = m.pixels_per_metre()
    h.press("+")
    assert m.pixels_per_metre() > whole * 1.5 and not m.whole
    wheel(m, 2)
    closer = m.pixels_per_metre()
    assert closer > whole * 2
    h.press("−")
    assert m.pixels_per_metre() < closer
    for _ in range(30):
        wheel(m, -3)
    assert abs(m.pixels_per_metre() - whole) < 1e-6 and m.whole    # never smaller than the whole site
    for _ in range(40):
        wheel(m, 3)
    assert m.pixels_per_metre() <= 200.0 + 1e-6                    # and never absurdly close
    h.press("Show the whole site")
    assert abs(m.pixels_per_metre() - whole) < 1e-6


def test_colour_by_changes_the_colours_and_the_legend(make):                    # RES-3
    h = make()
    view = open_saved(h, 60)
    assert (h.text("legend_lo"), h.text("legend_hi")) == ("5.0", "34.0")
    tall, short = view.map.items_by_id[29].brush().color(), view.map.items_by_id[30].brush().color()
    assert tall == QColor(*present.ramp_rgb(34.0, 5.0, 34.0)) and short == QColor(*present.ramp_rgb(5.0, 5.0, 34.0))
    view.colour.setCurrentIndex(1)                                 # crown area
    assert view.colour_key == "crown_area_m2" and h.text("legend_hi") == "38.5"
    assert view.map.items_by_id[29].brush().color() == QColor(*present.ramp_rgb(38.48, 7.07, 38.48))
    assert view.map.chosen == 29                                   # the selection is kept


def test_map_widget_alone(qtbot):
    m = CrownMap()
    qtbot.addWidget(m)
    m.resize(400, 300)
    m.show()
    got = []
    m.picked.connect(got.append)
    m.set_shapes([(1, "ring", [(-5, -5), (5, -5), (5, 5), (-5, 5)]), (2, "circle", (20.0, 0.0, 4.0))], 34.0, 10.0)
    m.set_colours({1: (10, 20, 30)})
    assert m.items_by_id[1].brush().color() == QColor(10, 20, 30)
    m.pick_at(m.position_of(2))
    m.pick_at(QPoint(1, 1))
    assert got == [2]
    m.select(2)
    m.select(1)
    assert m.items_by_id[2].zValue() == 0 and m.items_by_id[1].zValue() == 1
    m.set_shapes([], 10.0, 10.0)                                   # a site without any tree does not break it
    m.select(1)
    m.zoom(2.0)


# ---------------------------------------------------------------------------------- table
def test_table_filter_sort_and_columns(make):                                   # RES-4
    h = make()
    view = open_saved(h, 60)
    assert h.text("count") == "Showing 60 of 60 trees"
    h.find("find").setText("00017")
    assert h.text("count") == "Showing 1 of 60 trees" and view.model.rowCount() == 1
    h.find("find").setText("")
    h.find("hmin").setText("30")
    h.find("hmax").setText("32")
    assert h.text("count") == "Showing 6 of 60 trees"              # heights 30, 31, 32: trees 25-27 and 55-57
    h.find("hmin").setText("")
    h.find("hmax").setText("abc")                                  # not a number: no limit
    assert view.model.rowCount() == 60
    h.find("hmax").setText("")
    view.qc.setCurrentIndex(2)
    assert h.text("count") == "Showing 3 of 60 trees"
    view.qc.setCurrentIndex(0)

    view.table.sortByColumn(1, Qt.DescendingOrder)                 # height, tallest first
    assert view.model.tree_id_at(0) == 29 and view.model.index(0, 1).data() == "34.0"
    assert view.model.tree_id_at(view.table.currentIndex().row()) == 29      # the selected tree stays marked
    view.table.sortByColumn(1, Qt.AscendingOrder)
    assert view.model.index(0, 1).data() == "5.0"

    headers = lambda: [view.model.headerData(i, Qt.Horizontal) for i in range(view.model.columnCount())]
    assert headers() == ["Tree ID", "Height (m)", "Crown diameter (m)", "Crown area (m²)", "Crown volume (m³)", "Quality check"]
    view.column_actions["nn_spacing_m"].setChecked(True)
    view.column_actions["crown_volume_m3"].setChecked(False)
    assert headers() == ["Tree ID", "Height (m)", "Crown diameter (m)", "Crown area (m²)", "Quality check", "Nearest tree (m)"]
    assert view.model.index(0, 5).data() == "8.0"
    assert int(view.model.index(0, 1).data(Qt.TextAlignmentRole)) & int(Qt.AlignRight)
    assert int(view.model.index(0, 0).data(Qt.TextAlignmentRole)) & int(Qt.AlignLeft)


def test_a_tree_filtered_out_stays_selected_and_comes_back(make):
    h = make()
    view = open_saved(h, 60)
    h.find("find").setText("00017")
    assert view.selected == 29 and not view.table.selectionModel().hasSelection()
    h.find("find").setText("")
    assert view.model.tree_id_at(view.table.currentIndex().row()) == 29


# ---------------------------------------------------------------------------------- 3D
def test_3d_view_of_the_selected_tree(make):                                    # RES-7, OPEN-4
    pytest.importorskip("laspy")
    h = make()
    view = open_saved(h, 12, points=True)
    h.until(lambda: h.has("tree_3d"), 30)                          # the points are prepared in the background
    v = h.find("tree_3d")
    assert len(v.points) == 300 and abs(v.tree_height - (5 + view.selected % 30)) < 1.0
    img = v.render_image(300, 260)
    lit = int((img != (0x1B, 0x24, 0x20)).any(axis=2).sum())
    assert lit > 300                                               # the tree is drawn
    before = img.copy()
    QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
    QTest.mouseMove(v, QPoint(160, 120))
    QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(160, 120))
    assert (v.render_image(300, 260) != before).any()              # dragging turns it
    turned = v.turn
    wheel(v, 3)
    assert v.zoomed > 1.5
    QTest.mouseDClick(v, Qt.LeftButton)
    assert v.zoomed == 1.0 and v.turn != turned                    # double-click: back to the first view
    view.table.selectRow(0)                                        # another tree: other points
    assert h.find("tree_3d") is not v and abs(h.find("tree_3d").tree_height - 6.0) < 1.0
    assert (h.tmp / "home" / "state" / "viewcache").is_dir()       # kept for the next time (OPEN-4)


def test_3d_widget_alone(qtbot):
    np = pytest.importorskip("numpy")
    v = TreeView3D()
    qtbot.addWidget(v)
    assert not (v.render_image(50, 40) != (0x1B, 0x24, 0x20)).any()     # no tree yet: only the background
    pts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 10.0], [1.0, 0.0, 5.0]])
    v.set_points(pts)
    assert tuple(v.colours[0]) == (0xCF, 0xE8, 0xD8) and tuple(v.colours[1]) == (0x0F, 0x4A, 0x32)
    img = v.render_image(200, 200)
    rows = (img != (0x1B, 0x24, 0x20)).any(axis=2).nonzero()[0]
    assert rows.min() < 40 and rows.max() > 160                    # the tree fills the height of the picture
    v.resize(200, 200)
    v.show()
    v.grab()                                                       # painting on screen works


# ---------------------------------------------------------------------------------- big sites (NFR-2)
def test_table_model_with_fifty_thousand_trees():
    trees = [{"tree_id": i, "tree_uid": f"T{i:05d}", "height_m": float(i % 97), "qc_flag": "ok"} for i in range(50_000)]
    present.add_labels(trees)
    model = TreeTableModel(trees)
    start = time.perf_counter()
    model.sort(1, Qt.DescendingOrder)
    model.set_rows(present.filter_trees(trees, hmin=50))
    took = time.perf_counter() - start
    assert model.index(0, 1).data() == "96.0" and model.rowCount() == 24_205
    assert took < 2.0, took


def test_fifty_thousand_trees_open_and_stay_quick(make):
    h = make()
    path = make_saved(h.tmp / "big", 50_000)
    start = time.perf_counter()
    h.win.go_saved(path)
    h.pump()
    h.win.grab()                                                   # really drawn once
    opened = time.perf_counter() - start
    view = h.win.page
    assert isinstance(view, ResultsView) and len(view.map.items_by_id) == 50_000
    start = time.perf_counter()
    view.table.selectRow(40_000)
    h.pump()
    picked = time.perf_counter() - start
    start = time.perf_counter()
    view.colour.setCurrentIndex(2)
    view.table.sortByColumn(3, Qt.DescendingOrder)
    h.find("hmin").setText("20")
    h.pump()
    h.win.grab()
    changed = time.perf_counter() - start
    print(f"\n50,000 trees: open {opened:.1f} s, select {picked:.2f} s, recolour + sort + filter {changed:.1f} s")
    assert opened < 10 and picked < 0.5 and changed < 5, (opened, picked, changed)


def test_colour_by_quality_check_shows_which_trees_to_look_at(make):            # RES-3
    h = make()
    view = open_saved(h, 60)
    view.colour.setCurrentIndex(view.colour.findData("qc_flag"))
    assert view.colour.currentText() == "Quality check"
    ok, edge = QColor(*present.QC_COLOURS["ok"]), QColor(*present.QC_COLOURS["edge"])
    assert view.map.items_by_id[17].brush().color() == edge and view.map.items_by_id[34].brush().color() == edge
    assert view.map.items_by_id[1].brush().color() == ok
    assert (h.text("legend_class_0"), h.text("legend_class_1")) == ("OK (57)", "At the edge (3)")
    assert not h.has("legend_class_2") and not h.has("legend_lo")  # only the classes that occur; no number scale
    assert view.map.chosen == view.selected                        # the selection is kept
    view.colour.setCurrentIndex(0)                                 # back to height: the scale is back
    assert (h.text("legend_lo"), h.text("legend_hi")) == ("5.0", "34.0") and not h.has("legend_class_0")

from qgis.PyQt.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QInputDialog,
    QFrame
)
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import Qt

from qgis.core import QgsWkbTypes, QgsGeometry
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsSnapIndicator

from .polygon_area_splitter import PolygonAreaSplitter
from .line_length_splitter import LineLengthSplitter


def get_snapped_point(canvas, raw_point):
    match = canvas.snappingUtils().snapToMap(raw_point)
    if match.isValid():
        return match.point(), True, match
    return raw_point, False, match


class DirectionLineMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback
        self.points = []
        self.snap_indicator = QgsSnapIndicator(canvas)

        self.rb = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.rb.setColor(QColor(255, 0, 0))
        self.rb.setWidth(3)

    def canvasMoveEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        match = self.canvas.snappingUtils().snapToMap(raw_point)
        self.snap_indicator.setMatch(match)

        if self.points:
            point = match.point() if match.isValid() else raw_point
            self.rb.reset(QgsWkbTypes.LineGeometry)
            self.rb.addPoint(self.points[0], False)
            self.rb.addPoint(point, True)
            self.rb.show()

    def canvasReleaseEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        point, snapped, match = get_snapped_point(self.canvas, raw_point)

        self.points.append(point)

        self.rb.reset(QgsWkbTypes.LineGeometry)

        for p in self.points:
            self.rb.addPoint(p, False)

        self.rb.show()

        if len(self.points) == 1:
            self.canvas.window().statusBar().showMessage(
                "First point selected. Move mouse to see preview line, then click second point.",
                8000
            )

        elif len(self.points) == 2:
            line = QgsGeometry.fromPolylineXY(self.points)
            self.rb.setToGeometry(line, None)
            self.canvas.unsetMapTool(self)
            self.callback(self.points)

    def deactivate(self):
        self.rb.reset(QgsWkbTypes.LineGeometry)
        super().deactivate()


class OnePointMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback
        self.snap_indicator = QgsSnapIndicator(canvas)

    def canvasMoveEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        match = self.canvas.snappingUtils().snapToMap(raw_point)
        self.snap_indicator.setMatch(match)

    def canvasReleaseEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        point, snapped, match = get_snapped_point(self.canvas, raw_point)
        self.canvas.unsetMapTool(self)
        self.callback(point)


class SideClickMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback
        self.snap_indicator = QgsSnapIndicator(canvas)

    def canvasMoveEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        match = self.canvas.snappingUtils().snapToMap(raw_point)
        self.snap_indicator.setMatch(match)

    def canvasReleaseEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        point, snapped, match = get_snapped_point(self.canvas, raw_point)
        self.canvas.unsetMapTool(self)
        self.callback(point)


class BoundaryPathMapTool(QgsMapToolEmitPoint):
    def __init__(self, canvas, finish_callback, polygon_geom):
        super().__init__(canvas)
        self.canvas = canvas
        self.finish_callback = finish_callback
        self.polygon_geom = polygon_geom
        self.points = []
        self.first_point_snapped = False
        self.snap_indicator = QgsSnapIndicator(canvas)
        self.rb = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.rb.setColor(QColor(0, 120, 255))
        self.rb.setWidth(2)

    def canvasMoveEvent(self, event):
        raw_point = self.toMapCoordinates(event.pos())
        match = self.canvas.snappingUtils().snapToMap(raw_point)
        self.snap_indicator.setMatch(match)

        if self.points:
            point = match.point() if match.isValid() else raw_point

            self.rb.reset(QgsWkbTypes.LineGeometry)

            for p in self.points:
                self.rb.addPoint(p, False)

            self.rb.addPoint(point, True)
            self.rb.show()

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if len(self.points) < 1:
                QMessageBox.warning(None, "Boundary path", "At least one point is required.")
                return
            if not self.first_point_snapped:
                has_inside_point = any(self._is_point_inside_polygon(p) for p in self.points[1:])
                if not has_inside_point:
                    QMessageBox.warning(None, "Boundary path rule", "When the first point is outside the polygon, at least one additional point must be inside the polygon.")
                    return
            self.canvas.unsetMapTool(self)
            self.finish_callback(self.points)
            return

        raw_point = self.toMapCoordinates(event.pos())
        point, snapped, match = get_snapped_point(self.canvas, raw_point)

        if len(self.points) == 0:
            raw_inside = self._is_point_inside_polygon(raw_point)
            snapped_inside = self._is_point_inside_polygon(point)
            if raw_inside and not snapped:
                QMessageBox.warning(None, "Invalid first point", "First point cannot be inside the selected polygon.\n\nAllowed first point:\n- snapped to polygon boundary or vertex\n- outside the selected polygon")
                return
            if raw_inside and snapped_inside:
                QMessageBox.warning(None, "Invalid first point", "The snapped first point is still inside the polygon.\n\nPlease snap to polygon boundary or vertex.")
                return
            self.first_point_snapped = snapped

        self.points.append(point)
        self._update_rubber_band()
        self.canvas.window().statusBar().showMessage(f"Boundary path points: {len(self.points)}. Left click = add point, right click = finish.", 8000)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if len(self.points) < 1:
                QMessageBox.warning(None, "Boundary path", "At least one point is required.")
                return
            if not self.first_point_snapped:
                has_inside_point = any(self._is_point_inside_polygon(p) for p in self.points[1:])
                if not has_inside_point:
                    QMessageBox.warning(None, "Boundary path rule", "When the first point is outside the polygon, at least one additional point must be inside the polygon.")
                    return
            self.canvas.unsetMapTool(self)
            self.finish_callback(self.points)
        elif event.key() == Qt.Key_Escape:
            self.rb.reset(QgsWkbTypes.LineGeometry)
            self.points = []
            self.canvas.unsetMapTool(self)

    def _is_point_inside_polygon(self, point):
        pt_geom = QgsGeometry.fromPointXY(point)
        return self.polygon_geom.contains(pt_geom)

    def _update_rubber_band(self):
        self.rb.reset(QgsWkbTypes.LineGeometry)
        for p in self.points:
            self.rb.addPoint(p, False)
        self.rb.show()

    def deactivate(self):
        self.rb.reset(QgsWkbTypes.LineGeometry)
        super().deactivate()


class SmartGeometrySplitterDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.polygon_splitter = PolygonAreaSplitter()
        self.line_splitter = LineLengthSplitter()
        self.pending = {}
        self.direction_tool = None
        self.one_point_tool = None
        self.boundary_path_tool = None
        self.side_tool = None
        self.preview_rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.preview_rb.setColor(QColor(0, 255, 0, 180))
        self.preview_rb.setFillColor(QColor(0, 255, 0, 120))
        self.preview_rb.setWidth(3)

        self.setWindowTitle("Smart Geometry Splitter")
        self.resize(520, 430)
        layout = QVBoxLayout()
        self.lbl = QLabel("<b>Smart Geometry Splitter</b><br><br>Edit active polygon or line layer directly in QGIS editing mode.")
        self.btnCheck = QPushButton("Check Active Layer")
        self.sep1 = QFrame(); self.sep1.setFrameShape(QFrame.HLine)
        self.lblPolygon = QLabel("<b>Polygon tools</b>")
        self.btnPolygonTwoParts = QPushButton("1. Split polygon into 2 parts")
        self.btnPolygonEqualParts = QPushButton("2. Divide polygon into equal parts")
        self.btnPolygonAreaList = QPushButton("3. Divide polygon by area list + remainder")
        self.sep2 = QFrame(); self.sep2.setFrameShape(QFrame.HLine)
        self.lblLine = QLabel("<b>Line tools</b>")
        self.btnLineTwoParts = QPushButton("1. Split line into 2 parts")
        self.btnLineEqualParts = QPushButton("2. Divide line into equal parts")
        self.btnLineLengthList = QPushButton("3. Divide line by length list + remainder")
        self.sep3 = QFrame(); self.sep3.setFrameShape(QFrame.HLine)
        self.btnClose = QPushButton("Close")
        self.btnPolygonTwoParts.setEnabled(False)
        self.btnPolygonEqualParts.setEnabled(False)
        self.btnPolygonAreaList.setEnabled(False)
        self.btnLineTwoParts.setEnabled(False)
        self.btnLineEqualParts.setEnabled(False)
        self.btnLineLengthList.setEnabled(False)
        layout.addWidget(self.lbl); layout.addWidget(self.btnCheck)
        layout.addWidget(self.sep1); layout.addWidget(self.lblPolygon); layout.addWidget(self.btnPolygonTwoParts); layout.addWidget(self.btnPolygonEqualParts); layout.addWidget(self.btnPolygonAreaList)
        layout.addWidget(self.sep2); layout.addWidget(self.lblLine); layout.addWidget(self.btnLineTwoParts); layout.addWidget(self.btnLineEqualParts); layout.addWidget(self.btnLineLengthList)
        layout.addWidget(self.sep3); layout.addWidget(self.btnClose)
        self.setLayout(layout)
        self.btnCheck.clicked.connect(self.check_layer)
        self.btnPolygonTwoParts.clicked.connect(self.split_polygon_two_parts)
        self.btnPolygonEqualParts.clicked.connect(self.divide_polygon_equal_parts)
        self.btnPolygonAreaList.clicked.connect(self.divide_polygon_area_list)
        self.btnLineTwoParts.clicked.connect(self.split_line_two_parts)
        self.btnLineEqualParts.clicked.connect(self.divide_line_equal_parts)
        self.btnLineLengthList.clicked.connect(self.divide_line_length_list)
        self.btnClose.clicked.connect(self.close)

    def clear_preview(self):
        self.preview_rb.reset(QgsWkbTypes.PolygonGeometry)

    def check_layer(self):
        layer = self.iface.activeLayer()
        self.btnPolygonTwoParts.setEnabled(False); self.btnPolygonEqualParts.setEnabled(False); self.btnPolygonAreaList.setEnabled(False)
        self.btnLineTwoParts.setEnabled(False); self.btnLineEqualParts.setEnabled(False); self.btnLineLengthList.setEnabled(False)
        if not layer:
            QMessageBox.warning(self, "Error", "No active layer."); return
        if not layer.isEditable():
            QMessageBox.warning(self, "Error", "Layer is not editable. Start editing mode first."); return
        geom_type = QgsWkbTypes.geometryType(layer.wkbType())
        if geom_type == QgsWkbTypes.PolygonGeometry:
            self.btnPolygonTwoParts.setEnabled(True); self.btnPolygonEqualParts.setEnabled(True); self.btnPolygonAreaList.setEnabled(True)
            QMessageBox.information(self, "OK", f"Editable polygon layer ready:\n\n{layer.name()}")
        elif geom_type == QgsWkbTypes.LineGeometry:
            self.btnLineTwoParts.setEnabled(True); self.btnLineEqualParts.setEnabled(True); self.btnLineLengthList.setEnabled(True)
            QMessageBox.information(self, "OK", f"Editable line layer ready:\n\n{layer.name()}")
        else:
            QMessageBox.warning(self, "Error", "Only polygon and line layers are supported.")

    def split_polygon_two_parts(self):
        self.clear_preview()
        layer = self.iface.activeLayer()
        if not self._validate_polygon_layer(layer): return
        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one polygon."); return
        feature = selected[0]
        total_area = feature.geometry().area()
        target_area, ok = QInputDialog.getDouble(self, "Target area", f"Total area: {total_area:.3f}\n\nEnter target area:", total_area / 2, 0.0001, total_area - 0.0001, 3)
        if not ok: return
        method, ok = QInputDialog.getItem(self, "Polygon split method", "Choose polygon split method:", ["Boundary path + target area", "Vertical", "Horizontal", "User-defined line"], 0, False)
        if not ok: return
        if method == "Boundary path + target area":
            self.pending = {"layer": layer, "feature": feature, "target_area": target_area}
            QMessageBox.information(self, "Boundary path", "Draw the known boundary path for the new polygon.\n\nLeft click = add point\nRight click or Enter = finish known points\nEscape = cancel\n\nRules:\n- If first point is snapped to polygon boundary/vertex, one point is enough.\n- If first point is outside polygon, at least one next point must be inside polygon.\n- First point cannot be inside the selected polygon.\n\nAfter right click, choose Clockwise or Counterclockwise for the boundary search direction.")
            self.hide()
            self.boundary_path_tool = BoundaryPathMapTool(self.canvas, self.receive_boundary_path, feature.geometry())
            self.canvas.setMapTool(self.boundary_path_tool); self.canvas.setFocus(); return
        mode, ok = QInputDialog.getItem(self, "Mode", "Choose split mode:", ["Strict", "Allow multipart"], 0, False)
        if not ok: return
        allow_multipart = True if "Allow" in mode else False
        if method in ["Vertical", "Horizontal"]:
            options = self._side_options_for_axis(method)
            side, ok = QInputDialog.getItem(self, "Target side", "Which side should receive the target area?", options, 0, False)
            if not ok: return
            self.run_polygon_split(layer, feature, target_area, method, allow_multipart, self._is_opposite_side(side, options), None); return
        if method == "User-defined line":
            self.pending = {"layer": layer, "feature": feature, "target_area": target_area, "direction": "User-defined", "allow": allow_multipart}
            QMessageBox.information(self, "Draw line", "After pressing OK, click TWO points on the map canvas.")
            self.hide()
            self.direction_tool = DirectionLineMapTool(self.canvas, self.run_user_defined_direction)
            self.canvas.setMapTool(self.direction_tool); self.canvas.setFocus()

    def receive_boundary_path(self, points):
        self.pending["path_points"] = points

        self.show()

        direction, ok = QInputDialog.getItem(
            self,
            "Boundary search direction",
            "Choose direction for finding the final boundary point:",
            ["Clockwise", "Counterclockwise"],
            0,
            False
        )

        if not ok:
            self.clear_preview()
            return

        self.receive_boundary_direction(direction)

    def receive_boundary_side(self, side_point):
        # Compatibility wrapper for old side-click workflow.
        # New workflow uses receive_boundary_direction().
        self.receive_boundary_direction("Clockwise")

    def receive_boundary_direction(self, direction):
        self.show()
        p = self.pending

        try:
            preview = self.polygon_splitter.preview_boundary_path_area_by_direction(
                feature=p["feature"],
                target_area=p["target_area"],
                path_points=p["path_points"],
                direction=direction,
                tolerance=0.01
            )

            g1 = preview["geom1"]
            g2 = preview["geom2"]

            self.preview_rb.setToGeometry(g1, None)

            answer = QMessageBox.question(
                self,
                "Apply split?",
                "Preview created successfully.\n\n"
                f"Direction: {direction}\n"
                f"Target area: {p['target_area']:.3f}\n"
                f"Preview area: {preview['area_1']:.3f}\n"
                f"Remaining area: {preview['area_2']:.3f}\n"
                f"Difference: {preview['difference']:.6f}\n\n"
                "Apply this split to the active editable layer?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if answer == QMessageBox.Yes:
                result = self.polygon_splitter.apply_precomputed_split(
                    layer=p["layer"],
                    feature=p["feature"],
                    g1=g1,
                    g2=g2,
                    command_name="Split polygon by boundary path and target area"
                )

                self.clear_preview()

                QMessageBox.information(
                    self,
                    "Success",
                    "Boundary-path polygon was created successfully.\n\n"
                    f"Created area: {result['area_1']:.3f}\n"
                    f"Remaining area: {result['area_2']:.3f}"
                )
            else:
                self.clear_preview()

        except Exception as e:
            self.clear_preview()
            QMessageBox.critical(self, "Boundary path failed", str(e))

    def run_user_defined_direction(self, pts):
        self.show()
        p = self.pending
        options = self._side_options_for_user_line(pts)
        side, ok = QInputDialog.getItem(self, "Target side", "Which side should receive the target area?", options, 0, False)
        if not ok: return
        self.run_polygon_split(layer=p["layer"], feature=p["feature"], target_area=p["target_area"], direction=p["direction"], allow=p["allow"], opposite=self._user_line_selected_is_opposite(pts, side), pts=pts)

    def run_polygon_split(self, layer, feature, target_area, direction, allow, opposite, pts):
        try:
            result = self.polygon_splitter.split_by_target_area(layer=layer, feature=feature, target_area=target_area, direction=direction, allow_multipart=allow, use_opposite_side=opposite, direction_points=pts, tolerance=0.01)
            QMessageBox.information(self, "Success", "Polygon was split successfully.\n\n" f"Target area: {target_area:.3f}\n" f"Area 1: {result['area_1']:.3f}\n" f"Area 2: {result['area_2']:.3f}\n" f"Difference: {result['difference']:.6f}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def divide_polygon_equal_parts(self):
        layer = self.iface.activeLayer()
        if not self._validate_polygon_layer(layer): return
        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one polygon."); return
        feature = selected[0]
        total_area = feature.geometry().area()
        parts_count, ok = QInputDialog.getInt(
            self,
            "Equal polygon parts",
            f"Total area: {total_area:.3f}\n\nEnter number of equal parts:",
            2,
            2,
            1000,
            1
        )
        if not ok: return

        direction, ok = QInputDialog.getItem(
            self,
            "Split direction",
            "Choose split direction:",
            ["Vertical", "Horizontal", "User-defined line"],
            0,
            False
        )
        if not ok: return

        mode, ok = QInputDialog.getItem(
            self,
            "Mode",
            "Choose split mode:",
            ["Strict", "Allow multipart"],
            0,
            False
        )
        if not ok: return
        allow_multipart = True if "Allow" in mode else False

        if direction == "Boundary path + perimeter continuation":
            self.pending = {
                "layer": layer,
                "feature": feature,
                "parts_count": parts_count,
                "allow_multipart": allow_multipart
            }
            QMessageBox.information(
                self,
                "Boundary path perimeter equal parts",
                "Draw the start boundary path for equal division.\n\n"
                "Left click = add point\n"
                "Right click or Enter = finish known points\n"
                "After right click, click inside the side/direction where equal parts should be created."
            )
            self.hide()
            self.boundary_path_tool = BoundaryPathMapTool(
                self.canvas,
                self.receive_polygon_equal_boundary_path,
                feature.geometry()
            )
            self.canvas.setMapTool(self.boundary_path_tool)
            self.canvas.setFocus()
            return

        if direction == "User-defined line":
            self.pending = {
                "layer": layer,
                "feature": feature,
                "parts_count": parts_count,
                "polygon_equal_direction": "User-defined",
                "allow_multipart": allow_multipart
            }
            QMessageBox.information(
                self,
                "Draw line",
                "After pressing OK, click TWO points on the map canvas to define equal-division direction."
            )
            self.hide()
            self.direction_tool = DirectionLineMapTool(self.canvas, self.run_polygon_equal_user_direction)
            self.canvas.setMapTool(self.direction_tool)
            self.canvas.setFocus()
            return

        self.run_polygon_equal_parts(layer, feature, parts_count, direction, None, allow_multipart)

    def run_polygon_equal_user_direction(self, pts):
        self.show()
        p = self.pending
        self.run_polygon_equal_parts(
            layer=p["layer"],
            feature=p["feature"],
            parts_count=p["parts_count"],
            direction="User-defined",
            direction_points=pts,
            allow_multipart=p.get("allow_multipart", False)
        )

    def run_polygon_equal_parts(self, layer, feature, parts_count, direction, direction_points, allow_multipart=False):
        try:
            result = self.polygon_splitter.split_equal_parts_axis(
                layer=layer,
                feature=feature,
                parts_count=parts_count,
                direction=direction,
                direction_points=direction_points,
                tolerance=0.01,
                allow_multipart=allow_multipart
            )
            areas_text = "\n".join([f"Part {i + 1}: {area:.3f}" for i, area in enumerate(result["areas"])])
            QMessageBox.information(
                self,
                "Success",
                "Polygon divided into equal parts successfully.\n\n"
                f"Parts: {result['count']}\n"
                f"Target area each: {result['area_each']:.3f}\n"
                f"Total area: {result['total']:.3f}\n\n"
                f"{areas_text}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def receive_polygon_equal_boundary_path(self, points):
        self.pending["path_points"] = points

        QMessageBox.information(
            None,
            "Define equal-division side",
            "Click inside the side/direction where the equal polygon parts should be created."
        )

        self.side_tool = SideClickMapTool(
            self.canvas,
            self.receive_polygon_equal_boundary_side
        )

        self.canvas.setMapTool(self.side_tool)
        self.canvas.setFocus()

    def receive_polygon_equal_boundary_side(self, side_point):
        self.show()
        p = self.pending

        try:
            result = self.polygon_splitter.split_equal_parts_boundary_path(
                layer=p["layer"],
                feature=p["feature"],
                parts_count=p["parts_count"],
                path_points=p["path_points"],
                side_point=side_point,
                tolerance=0.01
            )

            areas_text = "\n".join(
                [f"Part {i + 1}: {area:.3f}" for i, area in enumerate(result["areas"])]
            )

            QMessageBox.information(
                self,
                "Success",
                "Polygon divided into equal parts using boundary path + perimeter continuation.\n\n"
                f"Parts: {result['count']}\n"
                f"Target area each: {result['area_each']:.3f}\n"
                f"Total area: {result['total']:.3f}\n\n"
                f"{areas_text}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Boundary equal division failed", str(e))

    def divide_polygon_area_list(self):
        layer = self.iface.activeLayer()
        if not self._validate_polygon_layer(layer): return

        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one polygon."); return

        feature = selected[0]
        total_area = feature.geometry().area()

        text, ok = QInputDialog.getText(
            self,
            "Area list + remainder",
            f"Total area: {total_area:.3f}\n\n"
            "Enter areas separated by comma, semicolon, or plus sign:\n"
            "Example: 200, 400, 300"
        )
        if not ok: return

        try:
            area_list = self._parse_number_list(text)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e)); return

        if sum(area_list) >= total_area:
            QMessageBox.warning(
                self,
                "Error",
                "Sum of defined areas must be smaller than total polygon area.\n\n"
                f"Sum: {sum(area_list):.3f}\n"
                f"Total: {total_area:.3f}"
            )
            return

        direction, ok = QInputDialog.getItem(
            self,
            "Split direction",
            "Choose split direction:",
            ["Vertical", "Horizontal", "User-defined line"],
            0,
            False
        )
        if not ok: return

        mode, ok = QInputDialog.getItem(
            self,
            "Mode",
            "Choose split mode:",
            ["Strict", "Allow multipart"],
            0,
            False
        )
        if not ok: return
        allow_multipart = True if "Allow" in mode else False

        if direction == "Boundary path + perimeter continuation":
            self.pending = {
                "layer": layer,
                "feature": feature,
                "area_list": area_list,
                "allow_multipart": allow_multipart
            }
            QMessageBox.information(
                self,
                "Boundary path perimeter area-list",
                "Draw the start boundary path for area-list division.\n\n"
                "Left click = add point\n"
                "Right click or Enter = finish known points\n"
                "After right click, choose Clockwise or Counterclockwise for the boundary search direction."
            )
            self.hide()
            self.boundary_path_tool = BoundaryPathMapTool(
                self.canvas,
                self.receive_polygon_area_list_boundary_path,
                feature.geometry()
            )
            self.canvas.setMapTool(self.boundary_path_tool)
            self.canvas.setFocus()
            return

        if direction in ["Vertical", "Horizontal"]:
            options = self._side_options_for_axis(direction)
            side, ok = QInputDialog.getItem(
                self,
                "First area side",
                "Which side should receive the first defined area?",
                options,
                0,
                False
            )
            if not ok: return
            use_opposite = self._is_opposite_side(side, options)
            self.run_polygon_area_list(layer, feature, area_list, direction, None, allow_multipart, use_opposite)
            return

        if direction == "User-defined line":
            self.pending = {
                "layer": layer,
                "feature": feature,
                "area_list": area_list,
                "allow_multipart": allow_multipart
            }
            QMessageBox.information(
                self,
                "Draw line",
                "After pressing OK, click TWO points on the map canvas to define area-list division direction."
            )
            self.hide()
            self.direction_tool = DirectionLineMapTool(self.canvas, self.run_polygon_area_list_user_direction)
            self.canvas.setMapTool(self.direction_tool)
            self.canvas.setFocus()
            return

    def run_polygon_area_list_user_direction(self, pts):
        self.show()
        p = self.pending
        options = self._side_options_for_user_line(pts)
        side, ok = QInputDialog.getItem(
            self,
            "First area side",
            "Which side should receive the first defined area?",
            options,
            0,
            False
        )
        if not ok: return
        self.run_polygon_area_list(
            layer=p["layer"],
            feature=p["feature"],
            area_list=p["area_list"],
            direction="User-defined",
            direction_points=pts,
            allow_multipart=p.get("allow_multipart", False),
            use_opposite_side=self._user_line_selected_is_opposite(pts, side)
        )

    def run_polygon_area_list(self, layer, feature, area_list, direction, direction_points, allow_multipart=False, use_opposite_side=False):
        try:
            result = self.polygon_splitter.split_by_area_list_axis(
                layer=layer,
                feature=feature,
                area_list=area_list,
                direction=direction,
                direction_points=direction_points,
                tolerance=0.01,
                allow_multipart=allow_multipart,
                use_opposite_side=use_opposite_side
            )
            areas_text = "\n".join([f"Part {i + 1}: {area:.3f}" for i, area in enumerate(result["areas"])])
            QMessageBox.information(
                self,
                "Success",
                "Polygon divided by area list successfully.\n\n"
                f"Parts: {result['count']}\n"
                f"Defined sum: {result['defined_sum']:.3f}\n"
                f"Remainder: {result['remainder']:.3f}\n"
                f"Total area: {result['total']:.3f}\n\n"
                f"{areas_text}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Area-list division failed", str(e))

    def receive_polygon_area_list_boundary_path(self, points):
        self.pending["path_points"] = points
        QMessageBox.information(
            None,
            "Define area-list side",
            "Click inside the side/direction where the polygon parts should be created."
        )
        self.side_tool = SideClickMapTool(
            self.canvas,
            self.receive_polygon_area_list_boundary_side
        )
        self.canvas.setMapTool(self.side_tool)
        self.canvas.setFocus()

    def receive_polygon_area_list_boundary_side(self, side_point):
        self.show()
        p = self.pending

        try:
            result = self.polygon_splitter.split_by_area_list_boundary_path(
                layer=p["layer"],
                feature=p["feature"],
                area_list=p["area_list"],
                path_points=p["path_points"],
                side_point=side_point,
                tolerance=0.01
            )

            areas_text = "\n".join(
                [f"Part {i + 1}: {area:.3f}" for i, area in enumerate(result["areas"])]
            )

            QMessageBox.information(
                self,
                "Success",
                "Polygon divided by area list + remainder using boundary path + perimeter continuation.\n\n"
                f"Defined sum: {result['defined_sum']:.3f}\n"
                f"Remainder: {result['remainder']:.3f}\n"
                f"Total area: {result['total']:.3f}\n\n"
                f"{areas_text}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Boundary area-list division failed", str(e))

    def split_line_two_parts(self):
        layer = self.iface.activeLayer()
        if not self._validate_line_layer(layer): return
        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one line."); return
        feature = selected[0]
        total_length = feature.geometry().length()
        target_length, ok = QInputDialog.getDouble(self, "Target length", f"Total length: {total_length:.3f}\n\nEnter target length:", total_length / 2, 0.0001, total_length - 0.0001, 3)
        if not ok: return
        mode, ok = QInputDialog.getItem(self, "Start definition", "How should the start side be defined?", ["Click near start side on map", "Use line start point", "Use line end point"], 0, False)
        if not ok: return
        if mode == "Click near start side on map":
            self.pending = {"layer": layer, "feature": feature, "target_length": target_length}
            QMessageBox.information(self, "Click start side", "After pressing OK, click near the side of the line where the target length should start.")
            self.hide()
            self.one_point_tool = OnePointMapTool(self.canvas, self.run_line_from_clicked_side)
            self.canvas.setMapTool(self.one_point_tool); self.canvas.setFocus(); return
        points = self._line_points(feature)
        start_point = points[-1] if mode == "Use line end point" else points[0]
        self.run_line_split(layer, feature, target_length, start_point)

    def run_line_from_clicked_side(self, point):
        self.show()
        p = self.pending
        self.run_line_split(layer=p["layer"], feature=p["feature"], target_length=p["target_length"], start_point=point)

    def run_line_split(self, layer, feature, target_length, start_point):
        try:
            result = self.line_splitter.split_by_target_length(layer=layer, feature=feature, target_length=target_length, start_point=start_point, tolerance=0.001)
            QMessageBox.information(self, "Success", "Line was split successfully.\n\n" f"Target length: {target_length:.3f}\n" f"Segments: {result['count']}\n" f"Total length: {result['total']:.3f}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def divide_line_equal_parts(self):
        layer = self.iface.activeLayer()
        if not self._validate_line_layer(layer): return
        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one line."); return
        feature = selected[0]
        total_length = feature.geometry().length()
        parts_count, ok = QInputDialog.getInt(self, "Equal line parts", f"Total length: {total_length:.3f}\n\nEnter number of equal parts:", 2, 2, 1000, 1)
        if not ok: return
        try:
            result = self.line_splitter.divide_into_equal_parts(layer=layer, feature=feature, parts_count=parts_count, start_point=None)
            lengths_text = "\n".join([f"Part {i + 1}: {length:.3f}" for i, length in enumerate(result["lengths"])])
            QMessageBox.information(self, "Success", "Line was divided into equal parts successfully.\n\n" f"Number of parts: {result['count']}\n" f"Total length: {result['total']:.3f}\n\n" f"{lengths_text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def divide_line_length_list(self):
        layer = self.iface.activeLayer()
        if not self._validate_line_layer(layer): return
        selected = layer.selectedFeatures()
        if len(selected) != 1:
            QMessageBox.warning(self, "Error", "Select exactly one line."); return
        feature = selected[0]
        total_length = feature.geometry().length()
        text, ok = QInputDialog.getText(self, "Length list + remainder", f"Total length: {total_length:.3f}\n\nEnter lengths separated by comma, semicolon, or plus sign:\nExample: 50, 75, 120")
        if not ok: return
        try:
            length_list = self._parse_number_list(text)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e)); return
        if sum(length_list) >= total_length:
            QMessageBox.warning(self, "Error", "Sum of defined lengths must be smaller than total line length.\n\n" f"Sum: {sum(length_list):.3f}\n" f"Total: {total_length:.3f}"); return
        mode, ok = QInputDialog.getItem(self, "Start definition", "How should the start side be defined?", ["Click near start side on map", "Use line start point", "Use line end point"], 0, False)
        if not ok: return
        if mode == "Click near start side on map":
            self.pending = {"layer": layer, "feature": feature, "length_list": length_list, "line_operation": "list"}
            QMessageBox.information(self, "Click start side", "After pressing OK, click near the side of the line where division should start.")
            self.hide()
            self.one_point_tool = OnePointMapTool(self.canvas, self.run_line_multi_from_clicked_side)
            self.canvas.setMapTool(self.one_point_tool); self.canvas.setFocus(); return
        points = self._line_points(feature)
        start_point = points[-1] if mode == "Use line end point" else points[0]
        self.run_line_length_list(layer, feature, length_list, start_point)

    def run_line_length_list(self, layer, feature, length_list, start_point):
        try:
            result = self.line_splitter.divide_by_length_list(layer=layer, feature=feature, length_list=length_list, start_point=start_point)
            lengths_text = "\n".join([f"Part {i + 1}: {length:.3f}" for i, length in enumerate(result["lengths"])])
            QMessageBox.information(self, "Success", "Line was divided by length list successfully.\n\n" f"Number of segments: {result['count']}\n" f"Total length: {result['total']:.3f}\n\n" f"{lengths_text}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def run_line_multi_from_clicked_side(self, point):
        self.show()
        p = self.pending
        if p["line_operation"] == "list":
            self.run_line_length_list(layer=p["layer"], feature=p["feature"], length_list=p["length_list"], start_point=point)

    def _validate_polygon_layer(self, layer):
        if not layer:
            QMessageBox.warning(self, "Error", "No active layer."); return False
        if not layer.isEditable():
            QMessageBox.warning(self, "Error", "Layer is not editable."); return False
        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
            QMessageBox.warning(self, "Error", "Active layer must be polygon layer."); return False
        return True

    def _validate_line_layer(self, layer):
        if not layer:
            QMessageBox.warning(self, "Error", "No active layer."); return False
        if not layer.isEditable():
            QMessageBox.warning(self, "Error", "Layer is not editable."); return False
        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.LineGeometry:
            QMessageBox.warning(self, "Error", "Active layer must be line layer."); return False
        return True

    def _line_points(self, feature):
        geom = feature.geometry()
        line = geom.asPolyline()
        if line: return line
        multi = geom.asMultiPolyline()
        if multi and len(multi) == 1: return multi[0]
        raise Exception("Only single-part lines are supported.")

    def _parse_number_list(self, text):
        if not text or not text.strip():
            raise Exception("No values entered.")
        normalized = text.replace("+", ",").replace(";", ",")
        parts = normalized.split(",")
        values = []
        for part in parts:
            s = part.strip().replace(" ", "")
            if not s: continue
            try:
                value = float(s)
            except Exception:
                raise Exception(f"Invalid number: {part}")
            if value <= 0:
                raise Exception("All values must be greater than zero.")
            values.append(value)
        if not values:
            raise Exception("No valid values found.")
        return values

    def _side_options_for_axis(self, direction):
        if direction == "Vertical": return ["West side", "East side"]
        if direction == "Horizontal": return ["South side", "North side"]
        return ["First side", "Opposite side"]

    def _side_options_for_user_line(self, pts):
        import math

        p1 = pts[0]
        p2 = pts[1]

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()

        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            QMessageBox.warning(
                self,
                "Invalid direction line",
                "The two clicked points are identical. Please draw a real direction line."
            )
            return ["North-West side", "South-East side"]

        length = math.sqrt(dx * dx + dy * dy)
        ux = dx / length
        uy = dy / length

        # Normal used by the polygon splitter.
        nx = -uy
        ny = ux

        positive_side = self._compass_label_for_vector(nx, ny) + " side"
        negative_side = self._compass_label_for_vector(-nx, -ny) + " side"

        if positive_side == negative_side:
            return ["First side", "Opposite side"]

        # Show the two real map sides. The order is not used for calculation;
        # calculation uses _user_line_selected_is_opposite().
        return [positive_side, negative_side]

    def _compass_label_for_vector(self, dx, dy):
        import math

        angle = math.degrees(math.atan2(dy, dx))

        if -22.5 <= angle < 22.5:
            return "East"
        if 22.5 <= angle < 67.5:
            return "North-East"
        if 67.5 <= angle < 112.5:
            return "North"
        if 112.5 <= angle < 157.5:
            return "North-West"
        if angle >= 157.5 or angle < -157.5:
            return "West"
        if -157.5 <= angle < -112.5:
            return "South-West"
        if -112.5 <= angle < -67.5:
            return "South"
        return "South-East"

    def _user_line_selected_is_opposite(self, pts, selected_side):
        import math

        p1 = pts[0]
        p2 = pts[1]

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.sqrt(dx * dx + dy * dy)

        if length == 0:
            return False

        ux = dx / length
        uy = dy / length
        nx = -uy
        ny = ux

        # The splitter's default target side is the negative-normal side.
        default_side = self._compass_label_for_vector(-nx, -ny) + " side"

        return selected_side != default_side

    def _is_opposite_side(self, selected, options):
        return selected == options[1]

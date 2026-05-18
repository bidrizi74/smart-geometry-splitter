import math

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsWkbTypes


class LineLengthSplitter:
    def split_by_target_length(
        self,
        layer,
        feature,
        target_length,
        start_point=None,
        tolerance=0.001
    ):
        points = self._prepared_points(feature.geometry(), start_point)
        total_length = self._polyline_length(points)

        if target_length <= 0:
            raise Exception("Target length must be greater than zero.")

        if target_length >= total_length:
            raise Exception("Target length must be smaller than selected line length.")

        segments = self._split_points_by_distances(points, [target_length])
        return self._apply_segments(layer, feature, segments, "Split line by target length", target_length)

    def divide_into_equal_parts(self, layer, feature, parts_count, start_point=None):
        if parts_count < 2:
            raise Exception("Number of parts must be at least 2.")

        points = self._prepared_points(feature.geometry(), start_point)
        total_length = self._polyline_length(points)
        step = total_length / parts_count

        distances = [step * i for i in range(1, parts_count)]
        segments = self._split_points_by_distances(points, distances)

        return self._apply_segments(layer, feature, segments, "Divide line into equal parts", step)

    def divide_by_length_list(self, layer, feature, length_list, start_point=None):
        if not length_list:
            raise Exception("Length list is empty.")

        points = self._prepared_points(feature.geometry(), start_point)
        total_length = self._polyline_length(points)

        cumulative = []
        acc = 0.0

        for length in length_list:
            if length <= 0:
                raise Exception("All lengths must be greater than zero.")

            acc += length

            if acc >= total_length:
                raise Exception(
                    "Sum of defined lengths must be smaller than total line length.\n\n"
                    f"Sum: {acc:.3f}\n"
                    f"Total: {total_length:.3f}"
                )

            cumulative.append(acc)

        segments = self._split_points_by_distances(points, cumulative)

        return self._apply_segments(layer, feature, segments, "Divide line by length list", None)

    def _prepared_points(self, geom, start_point):
        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        points = self._extract_single_polyline(geom)

        if len(points) < 2:
            raise Exception("Line must have at least two vertices.")

        if start_point is not None:
            start_xy = QgsPointXY(start_point)
            dist_start = self._distance(start_xy, points[0])
            dist_end = self._distance(start_xy, points[-1])

            if dist_end < dist_start:
                points = list(reversed(points))

        return points

    def _extract_single_polyline(self, geom):
        if QgsWkbTypes.isMultiType(geom.wkbType()):
            multi = geom.asMultiPolyline()

            if not multi:
                raise Exception("Could not read MultiLineString geometry.")

            if len(multi) != 1:
                raise Exception(
                    "This feature is multipart MultiLineString.\n\n"
                    "Current version supports only one connected line part.\n"
                    "Please use Multipart to Singleparts first."
                )

            return [QgsPointXY(p) for p in multi[0]]

        line = geom.asPolyline()

        if not line:
            raise Exception("Could not read line geometry.")

        return [QgsPointXY(p) for p in line]

    def _split_points_by_distances(self, points, distances):
        distances = sorted(distances)
        total_length = self._polyline_length(points)

        for d in distances:
            if d <= 0 or d >= total_length:
                raise Exception("Split distances must be inside line length.")

        result_segments = []
        current_segment = [points[0]]

        distance_index = 0
        accumulated = 0.0

        for i in range(1, len(points)):
            p_prev = points[i - 1]
            p_curr = points[i]

            seg_len = self._distance(p_prev, p_curr)

            if seg_len == 0:
                continue

            segment_start = p_prev

            while distance_index < len(distances) and accumulated + seg_len >= distances[distance_index]:
                target = distances[distance_index]
                remain = target - accumulated
                ratio = remain / seg_len

                split_x = p_prev.x() + ratio * (p_curr.x() - p_prev.x())
                split_y = p_prev.y() + ratio * (p_curr.y() - p_prev.y())
                split_point = QgsPointXY(split_x, split_y)

                current_segment.append(split_point)
                result_segments.append(current_segment)

                current_segment = [split_point]
                distance_index += 1

            current_segment.append(p_curr)
            accumulated += seg_len

        result_segments.append(current_segment)

        clean_segments = []

        for seg in result_segments:
            if len(seg) >= 2:
                clean_segments.append(seg)

        if len(clean_segments) < 2:
            raise Exception("Line division failed.")

        return clean_segments

    def _apply_segments(self, layer, feature, segments, command_name, expected_first_length=None):
        layer.beginEditCommand(command_name)

        try:
            attrs = feature.attributes()

            if not layer.deleteFeature(feature.id()):
                raise Exception("Could not delete original line.")

            new_lengths = []

            for seg in segments:
                geom = QgsGeometry.fromPolylineXY(seg)

                if geom is None or geom.isEmpty():
                    raise Exception("Created empty line segment.")

                f = QgsFeature(layer.fields())
                f.setGeometry(geom)
                f.setAttributes(attrs)

                if not layer.addFeature(f):
                    raise Exception("Could not add line segment.")

                new_lengths.append(geom.length())

            layer.endEditCommand()
            layer.triggerRepaint()

        except Exception as e:
            layer.destroyEditCommand()
            raise e

        return {
            "count": len(new_lengths),
            "lengths": new_lengths,
            "total": sum(new_lengths)
        }

    def _polyline_length(self, points):
        total = 0.0

        for i in range(1, len(points)):
            total += self._distance(points[i - 1], points[i])

        return total

    def _distance(self, p1, p2):
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        return math.sqrt(dx * dx + dy * dy)
import math

from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsRectangle,
    QgsWkbTypes,
    QgsPointXY
)


class PolygonAreaSplitter:
    def split_by_target_area(
        self,
        layer,
        feature,
        target_area,
        direction,
        allow_multipart=False,
        use_opposite_side=False,
        direction_points=None,
        tolerance=0.01,
        input_geometry=None
    ):
        geom = input_geometry if input_geometry is not None else feature.geometry()

        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        total_area = geom.area()

        if target_area <= 0:
            raise Exception("Target area must be greater than zero.")

        if target_area >= total_area:
            raise Exception("Target area must be smaller than selected polygon area.")

        # IMPORTANT SIDE LOGIC:
        # The internal solvers always create geom1 on the default side:
        # - Vertical: West side
        # - Horizontal: South side
        # - User-defined: negative-normal side of the clicked line
        # If the user selects the opposite side, we solve the default side as
        # the remaining area, then return the opposite side as geom1/target.
        solver_target_area = total_area - target_area if use_opposite_side else target_area

        if direction == "User-defined":
            if not direction_points or len(direction_points) != 2:
                raise Exception("User-defined direction requires two clicked points.")

            result = self._split_by_parallel_user_line(
                geom=geom,
                target_area=solver_target_area,
                direction_points=direction_points,
                allow_multipart=allow_multipart,
                tolerance=tolerance
            )
        else:
            result = self._split_by_axis(
                geom=geom,
                target_area=solver_target_area,
                direction=direction,
                allow_multipart=allow_multipart,
                tolerance=tolerance
            )

        if use_opposite_side:
            g1 = result["geom2"]
            g2 = result["geom1"]
        else:
            g1 = result["geom1"]
            g2 = result["geom2"]

        area_1 = g1.area()
        area_2 = g2.area()
        difference = abs(area_1 - target_area)

        if difference > tolerance:
            raise Exception(
                "Area tolerance not satisfied.\n\n"
                f"Target area: {target_area:.3f}\n"
                f"Calculated area: {area_1:.3f}\n"
                f"Difference: {difference:.6f}"
            )

        if layer is not None and feature is not None:
            self._apply_split(layer, feature, g1, g2, "Split polygon by target area")

        return {
            "area_1": area_1,
            "area_2": area_2,
            "difference": difference,
            "geom1": g1,
            "geom2": g2
        }

    def split_equal_parts_axis(self, layer, feature, parts_count, direction, direction_points=None, tolerance=0.01, allow_multipart=False):
        if parts_count < 2:
            raise Exception("Number of parts must be at least 2.")

        geom = feature.geometry()

        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        # Many shapefiles store polygons as MultiPolygon even when there is only one part.
        # For this tool we use the largest polygon part as the working geometry.
        if QgsWkbTypes.isMultiType(geom.wkbType()):
            geom = self._largest_polygon_part(geom)
            if geom is None or geom.isEmpty():
                raise Exception("Could not extract polygon part from multipart geometry.")

        total_area = geom.area()
        target_area = total_area / parts_count

        if parts_count == 2:
            # Existing algorithm is correct for 2 equal parts.
            result = self.split_by_target_area(
                layer=None,
                feature=feature,
                target_area=target_area,
                direction=direction,
                allow_multipart=allow_multipart,
                use_opposite_side=False,
                direction_points=direction_points,
                tolerance=tolerance,
                input_geometry=geom
            )
            parts = [result["geom1"], result["geom2"]]
        else:
            # For 3+ parts use cumulative offset lines, so all split lines are parallel.
            parts = self._split_equal_parts_by_offset_lines(
                geom=geom,
                parts_count=parts_count,
                direction=direction,
                direction_points=direction_points,
                tolerance=tolerance,
                reverse_direction=False
            )

        if not allow_multipart:
            for part in parts:
                if not self._is_single(part):
                    raise Exception(
                        "Equal division produced multipart output.\n\n"
                        "Choose Allow multipart, or try another direction."
                    )

        self._apply_multiple_parts(
            layer=layer,
            feature=feature,
            geometries=parts,
            command_name="Divide polygon into equal parts"
        )

        areas = [g.area() for g in parts]

        return {
            "count": len(parts),
            "area_each": target_area,
            "areas": areas,
            "total": sum(areas)
        }

    def _split_equal_parts_by_offset_lines(self, geom, parts_count, direction, direction_points=None, tolerance=0.01, reverse_direction=False):
        total_area = geom.area()
        target_area = total_area / parts_count

        params = self._offset_coordinate_system(
            geom=geom,
            direction=direction,
            direction_points=direction_points,
            reverse_direction=reverse_direction
        )

        ux = params["ux"]
        uy = params["uy"]
        nx = params["nx"]
        ny = params["ny"]
        min_u = params["min_u"]
        max_u = params["max_u"]
        min_n = params["min_n"]
        max_n = params["max_n"]

        cut_offsets = []

        for i in range(1, parts_count):
            cumulative_target = target_area * i
            cut_n = self._solve_offset_for_area(
                geom=geom,
                cumulative_target=cumulative_target,
                ux=ux,
                uy=uy,
                nx=nx,
                ny=ny,
                min_u=min_u,
                max_u=max_u,
                min_n=min_n,
                max_n=max_n,
                tolerance=tolerance
            )
            cut_offsets.append(cut_n)

        boundaries = [min_n] + cut_offsets + [max_n]
        parts = []

        for i in range(parts_count):
            clip = self._half_plane_polygon(
                ux, uy, nx, ny,
                min_u,
                max_u,
                boundaries[i],
                boundaries[i + 1]
            )

            part = geom.intersection(clip)

            if part is None or part.isEmpty():
                raise Exception("Offset equal division created an empty polygon part.")

            part = self._largest_polygon_part(part)

            if part is None or part.isEmpty():
                raise Exception("Offset equal division could not create a valid polygon part.")

            parts.append(QgsGeometry(part))

        return parts

    def _solve_offset_for_area(self, geom, cumulative_target, ux, uy, nx, ny, min_u, max_u, min_n, max_n, tolerance):
        low = min_n
        high = max_n
        best_n = None
        best_diff = None

        for _ in range(120):
            mid = (low + high) / 2.0

            clip = self._half_plane_polygon(
                ux, uy, nx, ny,
                min_u,
                max_u,
                min_n,
                mid
            )

            clipped = geom.intersection(clip)
            area = 0.0 if clipped is None or clipped.isEmpty() else clipped.area()
            diff = area - cumulative_target

            if best_diff is None or abs(diff) < best_diff:
                best_diff = abs(diff)
                best_n = mid

            if abs(diff) <= tolerance:
                return mid

            if area < cumulative_target:
                low = mid
            else:
                high = mid

        if best_n is None:
            raise Exception("Could not calculate offset position for equal division.")

        return best_n

    def _offset_coordinate_system(self, geom, direction, direction_points=None, reverse_direction=False):
        if direction == "Vertical":
            # split lines vertical; cumulative area grows west → east
            ux, uy = 0.0, 1.0
            nx, ny = 1.0, 0.0

        elif direction == "Horizontal":
            # split lines horizontal; cumulative area grows south → north
            ux, uy = 1.0, 0.0
            nx, ny = 0.0, 1.0

        elif direction == "User-defined":
            if not direction_points or len(direction_points) != 2:
                raise Exception("User-defined equal division requires two clicked points.")

            p1 = direction_points[0]
            p2 = direction_points[1]
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            length = math.sqrt(dx * dx + dy * dy)

            if length == 0:
                raise Exception("Direction line has zero length.")

            ux = dx / length
            uy = dy / length
            nx = -uy
            ny = ux

        else:
            raise Exception("This equal-division direction is not yet supported.")

        if reverse_direction:
            nx = -nx
            ny = -ny

        bbox = geom.boundingBox()
        corners = [
            QgsPointXY(bbox.xMinimum(), bbox.yMinimum()),
            QgsPointXY(bbox.xMinimum(), bbox.yMaximum()),
            QgsPointXY(bbox.xMaximum(), bbox.yMinimum()),
            QgsPointXY(bbox.xMaximum(), bbox.yMaximum())
        ]

        u_values = []
        n_values = []

        for p in corners:
            u_values.append(p.x() * ux + p.y() * uy)
            n_values.append(p.x() * nx + p.y() * ny)

        min_u = min(u_values)
        max_u = max(u_values)
        min_n = min(n_values)
        max_n = max(n_values)

        margin_u = (max_u - min_u) * 1.0
        margin_n = (max_n - min_n) * 0.20

        return {
            "ux": ux,
            "uy": uy,
            "nx": nx,
            "ny": ny,
            "min_u": min_u - margin_u,
            "max_u": max_u + margin_u,
            "min_n": min_n - margin_n,
            "max_n": max_n + margin_n
        }

    def preview_boundary_path_area(
        self,
        feature,
        target_area,
        path_points,
        side_point=None,
        route=None,
        tolerance=0.01
    ):
        geom = feature.geometry()

        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        total_area = geom.area()

        if target_area <= 0:
            raise Exception("Target area must be greater than zero.")

        if target_area >= total_area:
            raise Exception("Target area must be smaller than selected polygon area.")

        if not path_points or len(path_points) < 1:
            raise Exception("Boundary path must contain at least one point.")

        ring = self._extract_outer_ring(geom)
        cum_lengths, ring_length = self._ring_cumulative_lengths(ring)

        if ring_length <= 0:
            raise Exception("Invalid polygon boundary.")

        start_info = self._nearest_point_on_ring(
            path_points[0],
            ring,
            cum_lengths,
            ring_length
        )

        start_point = start_info["point"]
        start_dist = start_info["distance"]

        fixed_path = [start_point]
        for p in path_points[1:]:
            fixed_path.append(QgsPointXY(p))

        if route is None:
            if side_point is None:
                raise Exception("Boundary direction was not defined.")
            route = self._choose_boundary_route_from_side(
                source_geom=geom,
                fixed_path=fixed_path,
                ring=ring,
                cum_lengths=cum_lengths,
                ring_length=ring_length,
                start_dist=start_dist,
                side_point=side_point
            )

        best = self._find_boundary_path_solution_v4_temp_polygon(
            source_geom=geom,
            fixed_path=fixed_path,
            ring=ring,
            cum_lengths=cum_lengths,
            ring_length=ring_length,
            start_dist=start_dist,
            target_area=target_area,
            route=route,
            tolerance=tolerance
        )

        if best is None:
            raise Exception(
                "Could not calculate target polygon in the selected direction.\n\n"
                "Try clicking the other side or drawing another boundary path."
            )

        g1 = best["geom1"]
        g2 = best["geom2"]

        area_1 = g1.area()
        area_2 = g2.area()
        difference = abs(area_1 - target_area)

        if difference > tolerance:
            raise Exception(
                "Area tolerance not satisfied.\n\n"
                f"Target area: {target_area:.3f}\n"
                f"Calculated area: {area_1:.3f}\n"
                f"Difference: {difference:.6f}\n\n"
                "Try clicking the other side or drawing another boundary path."
            )

        return {
            "geom1": g1,
            "geom2": g2,
            "area_1": area_1,
            "area_2": area_2,
            "difference": difference
        }

    def preview_boundary_path_area_by_direction(
        self,
        feature,
        target_area,
        path_points,
        direction,
        tolerance=0.01
    ):
        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        ring = self._extract_outer_ring(geom)
        route = self._route_from_direction(ring, direction)

        return self.preview_boundary_path_area(
            feature=feature,
            target_area=target_area,
            path_points=path_points,
            route=route,
            tolerance=tolerance
        )

    def split_by_boundary_path_area(self, layer, feature, target_area, path_points, side_point=None, route=None, tolerance=0.01):
        preview = self.preview_boundary_path_area(
            feature=feature,
            target_area=target_area,
            path_points=path_points,
            side_point=side_point,
            route=route,
            tolerance=tolerance
        )
        g1 = preview["geom1"]
        g2 = preview["geom2"]
        self._apply_split(layer, feature, g1, g2, "Split polygon by boundary path and target area")
        return {"area_1": g1.area(), "area_2": g2.area(), "difference": preview["difference"]}

    def apply_precomputed_split(self, layer, feature, g1, g2, command_name):
        self._apply_split(layer, feature, g1, g2, command_name)
        return {"area_1": g1.area(), "area_2": g2.area()}

    def _route_from_direction(self, ring, direction):
        """
        Convert operator choice Clockwise/Counterclockwise to internal route.

        Internal route:
        - forward  = polygon ring coordinate order
        - backward = opposite of polygon ring coordinate order
        """
        clockwise_ring = self._ring_is_clockwise(ring)

        if direction == "Clockwise":
            return "forward" if clockwise_ring else "backward"

        return "backward" if clockwise_ring else "forward"

    def _ring_is_clockwise(self, ring):
        area2 = 0.0

        for i in range(len(ring)):
            p1 = ring[i]
            p2 = ring[(i + 1) % len(ring)]
            area2 += (p2.x() - p1.x()) * (p2.y() + p1.y())

        return area2 > 0.0

    def _choose_boundary_route_from_side(self, source_geom, fixed_path, ring, cum_lengths, ring_length, start_dist, side_point):
        """
        Decide only the boundary-walking orientation from the side click.

        Important:
        - The clicked point defines the STARTING SIDE / ORIENTATION.
        - The final target polygon is allowed to extend farther along the polygon boundary.
        - The clicked point is NOT used as a hard containment limit for the final polygon.
        """

        side_geom = QgsGeometry.fromPointXY(side_point)

        test_delta = ring_length * 0.03
        if test_delta <= 0:
            return "forward"

        forward_candidate = self._candidate_from_boundary_delta(
            source_geom,
            fixed_path,
            ring,
            cum_lengths,
            ring_length,
            start_dist,
            test_delta,
            "forward"
        )

        backward_candidate = self._candidate_from_boundary_delta(
            source_geom,
            fixed_path,
            ring,
            cum_lengths,
            ring_length,
            start_dist,
            test_delta,
            "backward"
        )

        forward_contains = (
            forward_candidate is not None and
            forward_candidate["geom1"].contains(side_geom)
        )

        backward_contains = (
            backward_candidate is not None and
            backward_candidate["geom1"].contains(side_geom)
        )

        if forward_contains and not backward_contains:
            return "forward"

        if backward_contains and not forward_contains:
            return "backward"

        # Fallback if the small test polygons do not contain the clicked point.
        # Use vector orientation from the start point to select the closer walking direction.
        start_point = self._point_at_ring_distance(
            ring,
            cum_lengths,
            ring_length,
            start_dist
        )

        fwd_point = self._point_at_ring_distance(
            ring,
            cum_lengths,
            ring_length,
            start_dist + test_delta
        )

        bwd_point = self._point_at_ring_distance(
            ring,
            cum_lengths,
            ring_length,
            start_dist - test_delta
        )

        sx = side_point.x() - start_point.x()
        sy = side_point.y() - start_point.y()

        fdx = fwd_point.x() - start_point.x()
        fdy = fwd_point.y() - start_point.y()

        bdx = bwd_point.x() - start_point.x()
        bdy = bwd_point.y() - start_point.y()

        if (sx * bdx + sy * bdy) > (sx * fdx + sy * fdy):
            return "backward"

        return "forward"

    def _find_boundary_path_solution_v4_temp_polygon(self, source_geom, fixed_path, ring, cum_lengths, ring_length, start_dist, target_area, route, tolerance):
        """
        V4 temporary-polygon engine:
        build complete temporary polygon for every boundary segment before area calculation.
        """
        if ring_length <= 0:
            return None

        ordered_boundary = self._ordered_boundary_vertices_v4(ring, cum_lengths, ring_length, start_dist, route)
        if len(ordered_boundary) < 2:
            return None

        best = None
        previous_area = 0.0

        for i in range(1, len(ordered_boundary)):
            vi = QgsPointXY(ordered_boundary[i - 1])
            vj = QgsPointXY(ordered_boundary[i])

            temp_target = self._target_polygon_points_v4(fixed_path, ordered_boundary, i, vj)
            temp_area = abs(self._shoelace_area_v4(temp_target))

            temp_candidate = self._candidate_from_v4_points(source_geom, fixed_path, ordered_boundary, i, vj)
            if temp_candidate is not None:
                diff = abs(temp_candidate["area"] - target_area)
                if best is None or diff < best["difference"]:
                    best = {"geom1": temp_candidate["geom1"], "geom2": temp_candidate["geom2"], "difference": diff, "area": temp_candidate["area"]}
                if diff <= tolerance:
                    return best

            if previous_area <= target_area <= temp_area and abs(temp_area - previous_area) > 1e-12:
                t = (target_area - previous_area) / (temp_area - previous_area)
                t = max(0.0, min(1.0, t))
                final_point = self._interpolate_point_v4(vi, vj, t)

                final_candidate = self._candidate_from_v4_points(source_geom, fixed_path, ordered_boundary, i, final_point)
                if final_candidate is None:
                    return best

                diff = abs(final_candidate["area"] - target_area)
                return {"geom1": final_candidate["geom1"], "geom2": final_candidate["geom2"], "difference": diff, "area": final_candidate["area"]}

            previous_area = temp_area

        return best

    def _ordered_boundary_vertices_v4(self, ring, cum_lengths, ring_length, start_dist, route):
        start_point = self._point_at_ring_distance(ring, cum_lengths, ring_length, start_dist)
        vertices = []

        for idx, vd in enumerate(cum_lengths[:-1]):
            if route == "forward":
                delta = (vd - start_dist) % ring_length
            else:
                delta = (start_dist - vd) % ring_length
            if 1e-9 < delta < ring_length - 1e-9:
                vertices.append((delta, QgsPointXY(ring[idx])))

        vertices.sort(key=lambda x: x[0])
        ordered = [QgsPointXY(start_point)]
        for _delta, pt in vertices:
            if self._distance(ordered[-1], pt) > 1e-9:
                ordered.append(QgsPointXY(pt))

        if self._distance(ordered[-1], start_point) > 1e-9:
            ordered.append(QgsPointXY(start_point))
        return ordered

    def _target_polygon_points_v4(self, fixed_path, ordered_boundary, final_index, final_point):
        fp = [QgsPointXY(p) for p in fixed_path]
        final_point = QgsPointXY(final_point)

        if len(fp) <= 1:
            points = [QgsPointXY(p) for p in ordered_boundary[:final_index]]
            if not points or self._distance(points[-1], final_point) > 1e-9:
                points.append(final_point)
            return self._clean_polygon_points(points)

        # user draws a -> ... -> b, target must be b -> ... -> a
        points = list(reversed(fp))

        for p in ordered_boundary[1:final_index]:
            if self._distance(points[-1], p) > 1e-9:
                points.append(QgsPointXY(p))

        if self._distance(points[-1], final_point) > 1e-9:
            points.append(final_point)

        return self._clean_polygon_points(points)

    def _remaining_polygon_points_v4(self, fixed_path, ordered_boundary, final_index, final_point):
        fp = [QgsPointXY(p) for p in fixed_path]
        final_point = QgsPointXY(final_point)

        points = [final_point]

        for p in ordered_boundary[final_index:]:
            if self._distance(points[-1], p) > 1e-9:
                points.append(QgsPointXY(p))

        if len(fp) > 1:
            # remaining uses original guide order: a -> ... -> b
            for p in fp[1:]:
                if self._distance(points[-1], p) > 1e-9:
                    points.append(QgsPointXY(p))

        if self._distance(points[-1], final_point) > 1e-9:
            points.append(QgsPointXY(final_point))

        return self._clean_polygon_points(points)

    def _candidate_from_v4_points(self, source_geom, fixed_path, ordered_boundary, final_index, final_point):
        target_points = self._target_polygon_points_v4(fixed_path, ordered_boundary, final_index, final_point)
        remaining_points = self._remaining_polygon_points_v4(fixed_path, ordered_boundary, final_index, final_point)

        if len(target_points) < 4 or len(remaining_points) < 4:
            return None

        math_area = abs(self._shoelace_area_v4(target_points))

        target_geom = QgsGeometry.fromPolygonXY([target_points])
        remaining_geom = QgsGeometry.fromPolygonXY([remaining_points])

        if target_geom is None or target_geom.isEmpty():
            return None
        if remaining_geom is None or remaining_geom.isEmpty():
            return None
        if not target_geom.isGeosValid():
            return None
        if not remaining_geom.isGeosValid():
            return None
        if target_geom.area() <= 0 or remaining_geom.area() <= 0:
            return None

        return {"geom1": QgsGeometry(target_geom), "geom2": QgsGeometry(remaining_geom), "area": math_area}

    def _interpolate_point_v4(self, p1, p2, t):
        return QgsPointXY(p1.x() + t * (p2.x() - p1.x()), p1.y() + t * (p2.y() - p1.y()))

    def _shoelace_area_v4(self, points):
        if points is None or len(points) < 3:
            return 0.0

        pts = [QgsPointXY(p) for p in points]
        if len(pts) > 1 and self._distance(pts[0], pts[-1]) <= 1e-9:
            pts = pts[:-1]
        if len(pts) < 3:
            return 0.0

        area2 = 0.0
        n = len(pts)
        for i in range(n):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            area2 += p1.x() * p2.y() - p2.x() * p1.y()
        return area2 / 2.0

    def _find_boundary_path_solution(
        self,
        source_geom,
        fixed_path,
        ring,
        cum_lengths,
        ring_length,
        start_dist,
        target_area,
        route,
        tolerance
    ):
        """
        Search the complete polygon boundary in the selected orientation.

        Side click chooses only the search orientation. The solver is allowed to
        continue along the polygon boundary until the target area is found.
        """

        best = None
        previous = None
        samples = 1800

        for i in range(1, samples):
            delta = ring_length * i / samples

            candidate = self._candidate_from_boundary_delta(
                source_geom,
                fixed_path,
                ring,
                cum_lengths,
                ring_length,
                start_dist,
                delta,
                route
            )

            if candidate is None:
                continue

            diff = candidate["area"] - target_area

            if best is None or abs(diff) < best["difference"]:
                best = {
                    "geom1": candidate["geom1"],
                    "geom2": candidate["geom2"],
                    "difference": abs(diff),
                    "delta": delta
                }

            if previous is not None and previous["diff"] * diff <= 0:
                refined = self._refine_boundary_candidate(
                    source_geom,
                    fixed_path,
                    ring,
                    cum_lengths,
                    ring_length,
                    start_dist,
                    previous["delta"],
                    delta,
                    route,
                    target_area,
                    tolerance
                )

                if refined is not None:
                    if best is None or refined["difference"] < best["difference"]:
                        best = refined

                    if refined["difference"] <= tolerance:
                        return refined

            previous = {
                "delta": delta,
                "diff": diff
            }

        if best is not None and best["difference"] <= tolerance:
            return best

        return best

    def _refine_boundary_candidate(
        self,
        source_geom,
        fixed_path,
        ring,
        cum_lengths,
        ring_length,
        start_dist,
        delta1,
        delta2,
        route,
        target_area,
        tolerance
    ):
        low = delta1
        high = delta2

        low_candidate = self._candidate_from_boundary_delta(
            source_geom,
            fixed_path,
            ring,
            cum_lengths,
            ring_length,
            start_dist,
            low,
            route
        )

        high_candidate = self._candidate_from_boundary_delta(
            source_geom,
            fixed_path,
            ring,
            cum_lengths,
            ring_length,
            start_dist,
            high,
            route
        )

        if low_candidate is None or high_candidate is None:
            return None

        low_diff = low_candidate["area"] - target_area
        high_diff = high_candidate["area"] - target_area

        best = None

        for cand, diff, delta in [
            (low_candidate, low_diff, low),
            (high_candidate, high_diff, high)
        ]:
            current = {
                "geom1": cand["geom1"],
                "geom2": cand["geom2"],
                "difference": abs(diff),
                "delta": delta
            }

            if best is None or current["difference"] < best["difference"]:
                best = current

        if low_diff * high_diff > 0:
            return best

        for _ in range(100):
            mid = (low + high) / 2.0

            candidate = self._candidate_from_boundary_delta(
                source_geom,
                fixed_path,
                ring,
                cum_lengths,
                ring_length,
                start_dist,
                mid,
                route
            )

            if candidate is None:
                break

            diff = candidate["area"] - target_area

            current = {
                "geom1": candidate["geom1"],
                "geom2": candidate["geom2"],
                "difference": abs(diff),
                "delta": mid
            }

            if best is None or current["difference"] < best["difference"]:
                best = current

            if abs(diff) <= tolerance:
                return current

            if low_diff * diff <= 0:
                high = mid
                high_diff = diff
            else:
                low = mid
                low_diff = diff

        return best

    def _candidate_from_boundary_delta(self, source_geom, fixed_path, ring, cum_lengths, ring_length, start_dist, delta, route):
        """
        Cadastral-safe candidate builder.

        The calculated boundary point is inserted as a real vertex for BOTH
        output polygons.

        This avoids the wrong topology where the remaining polygon follows the
        old original boundary vertex and then returns to the new calculated point.
        """

        if route == "forward":
            candidate_dist = (start_dist + delta) % ring_length

            target_boundary = self._ring_segment_forward(
                ring,
                cum_lengths,
                ring_length,
                start_dist,
                candidate_dist
            )

            remaining_boundary = self._ring_segment_forward(
                ring,
                cum_lengths,
                ring_length,
                candidate_dist,
                start_dist
            )

        else:
            candidate_dist = (start_dist - delta) % ring_length

            fwd_candidate_to_start = self._ring_segment_forward(
                ring,
                cum_lengths,
                ring_length,
                candidate_dist,
                start_dist
            )
            target_boundary = list(reversed(fwd_candidate_to_start))

            fwd_start_to_candidate = self._ring_segment_forward(
                ring,
                cum_lengths,
                ring_length,
                start_dist,
                candidate_dist
            )
            remaining_boundary = list(reversed(fwd_start_to_candidate))

        if not target_boundary or len(target_boundary) < 2:
            return None

        if not remaining_boundary or len(remaining_boundary) < 2:
            return None

        candidate_point = QgsPointXY(target_boundary[-1])

        # Target polygon
        if len(fixed_path) <= 1:
            target_points = [QgsPointXY(p) for p in target_boundary]

        else:
            target_points = [QgsPointXY(p) for p in fixed_path]

            if self._distance(target_points[-1], candidate_point) > 1e-9:
                target_points.append(QgsPointXY(candidate_point))

            back_to_start = list(reversed(target_boundary))

            for p in back_to_start[1:]:
                if self._distance(target_points[-1], p) > 1e-9:
                    target_points.append(QgsPointXY(p))

        # Remaining polygon
        if len(fixed_path) <= 1:
            remaining_points = [QgsPointXY(p) for p in remaining_boundary]

        else:
            remaining_points = [QgsPointXY(p) for p in remaining_boundary]

            for p in fixed_path[1:]:
                if self._distance(remaining_points[-1], p) > 1e-9:
                    remaining_points.append(QgsPointXY(p))

            if self._distance(remaining_points[-1], candidate_point) > 1e-9:
                remaining_points.append(QgsPointXY(candidate_point))

        target_points = self._clean_polygon_points(target_points)
        remaining_points = self._clean_polygon_points(remaining_points)

        if len(target_points) < 4 or len(remaining_points) < 4:
            return None

        target_geom = QgsGeometry.fromPolygonXY([target_points])
        remaining_geom = QgsGeometry.fromPolygonXY([remaining_points])

        if target_geom is None or target_geom.isEmpty():
            return None

        if remaining_geom is None or remaining_geom.isEmpty():
            return None

        if not target_geom.isGeosValid():
            return None

        if not remaining_geom.isGeosValid():
            return None

        if target_geom.area() <= 0 or remaining_geom.area() <= 0:
            return None

        outside_target = target_geom.difference(source_geom)

        if outside_target is not None and not outside_target.isEmpty():
            if outside_target.area() > max(0.001, source_geom.area() * 0.000000001):
                return None

        outside_remaining = remaining_geom.difference(source_geom)

        if outside_remaining is not None and not outside_remaining.isEmpty():
            if outside_remaining.area() > max(0.001, source_geom.area() * 0.000000001):
                return None

        return {
            "geom1": QgsGeometry(target_geom),
            "geom2": QgsGeometry(remaining_geom),
            "area": target_geom.area()
        }

    def _clean_polygon_points(self, points):
        cleaned = []

        for p in points:
            pt = QgsPointXY(p)

            if not cleaned or self._distance(cleaned[-1], pt) > 1e-9:
                cleaned.append(pt)

        if len(cleaned) < 3:
            return cleaned

        if self._distance(cleaned[0], cleaned[-1]) > 1e-9:
            cleaned.append(QgsPointXY(cleaned[0]))

        final = [cleaned[0]]

        for p in cleaned[1:]:
            if self._distance(final[-1], p) > 1e-9:
                final.append(p)

        if self._distance(final[0], final[-1]) > 1e-9:
            final.append(QgsPointXY(final[0]))

        return final

    def _largest_polygon_part(self, geom):
        if geom is None or geom.isEmpty():
            return None
        if not QgsWkbTypes.isMultiType(geom.wkbType()):
            return QgsGeometry(geom)
        parts = geom.asGeometryCollection()
        polygon_parts = [part for part in parts if part is not None and not part.isEmpty() and QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.PolygonGeometry]
        if not polygon_parts:
            return None
        return QgsGeometry(max(polygon_parts, key=lambda g: g.area()))

    def _extract_outer_ring(self, geom):
        if geom is None or geom.isEmpty():
            raise Exception("Empty polygon geometry.")
        if QgsWkbTypes.isMultiType(geom.wkbType()):
            multi = geom.asMultiPolygon()
            if not multi:
                raise Exception("Could not read MultiPolygon geometry.")
            if len(multi) != 1:
                raise Exception("This feature is multipart MultiPolygon. Please use Multipart to Singleparts first.")
            if len(multi[0]) == 0:
                raise Exception("Invalid MultiPolygon geometry.")
            return self._clean_ring(multi[0][0])
        polygon = geom.asPolygon()
        if polygon and len(polygon) > 0:
            return self._clean_ring(polygon[0])
        raise Exception("Could not read polygon boundary.")

    def _clean_ring(self, ring):
        cleaned = [QgsPointXY(p) for p in ring]
        if len(cleaned) < 4:
            raise Exception("Invalid polygon ring.")
        if cleaned[0] == cleaned[-1]:
            cleaned = cleaned[:-1]
        if len(cleaned) < 3:
            raise Exception("Invalid polygon ring.")
        return cleaned

    def _ring_cumulative_lengths(self, ring):
        cumulative = [0.0]
        total = 0.0
        for i in range(len(ring)):
            p1 = ring[i]
            p2 = ring[(i + 1) % len(ring)]
            total += self._distance(p1, p2)
            cumulative.append(total)
        return cumulative, total

    def _nearest_point_on_ring(self, point, ring, cum_lengths, ring_length):
        best_point = None
        best_distance = None
        best_ring_distance = 0.0
        p = QgsPointXY(point)
        for i in range(len(ring)):
            a = ring[i]
            b = ring[(i + 1) % len(ring)]
            projection = self._project_point_to_segment(p, a, b)
            projected_point = projection["point"]
            t = projection["t"]
            segment_length = self._distance(a, b)
            ring_distance = cum_lengths[i] + t * segment_length
            d = self._distance(p, projected_point)
            if best_distance is None or d < best_distance:
                best_distance = d
                best_point = projected_point
                best_ring_distance = ring_distance
        return {"point": best_point, "distance": best_ring_distance % ring_length, "offset": best_distance}

    def _project_point_to_segment(self, p, a, b):
        ax = a.x(); ay = a.y(); bx = b.x(); by = b.y(); px = p.x(); py = p.y()
        dx = bx - ax; dy = by - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return {"point": QgsPointXY(a), "t": 0.0}
        t = ((px - ax) * dx + (py - ay) * dy) / length_sq
        t = max(0.0, min(1.0, t))
        return {"point": QgsPointXY(ax + t * dx, ay + t * dy), "t": t}

    def _point_at_ring_distance(self, ring, cum_lengths, ring_length, distance):
        d = distance % ring_length
        for i in range(len(ring)):
            seg_start = cum_lengths[i]
            seg_end = cum_lengths[i + 1]
            if seg_start <= d <= seg_end:
                a = ring[i]
                b = ring[(i + 1) % len(ring)]
                seg_len = seg_end - seg_start
                if seg_len == 0:
                    return QgsPointXY(a)
                t = (d - seg_start) / seg_len
                return QgsPointXY(a.x() + t * (b.x() - a.x()), a.y() + t * (b.y() - a.y()))
        return QgsPointXY(ring[0])

    def _ring_segment_forward(self, ring, cum_lengths, ring_length, d_from, d_to):
        d_from = d_from % ring_length
        d_to = d_to % ring_length
        end_unwrapped = d_to
        if end_unwrapped <= d_from:
            end_unwrapped += ring_length
        start_point = self._point_at_ring_distance(ring, cum_lengths, ring_length, d_from)
        end_point = self._point_at_ring_distance(ring, cum_lengths, ring_length, d_to)
        points = [start_point]
        for i in range(len(ring)):
            vd = cum_lengths[i]
            for candidate_vd in [vd, vd + ring_length]:
                if d_from < candidate_vd < end_unwrapped:
                    points.append(QgsPointXY(ring[i]))
        points.append(end_point)
        return points

    def _split_by_axis(self, geom, target_area, direction, allow_multipart, tolerance):
        bbox = geom.boundingBox()
        min_x = bbox.xMinimum(); max_x = bbox.xMaximum(); min_y = bbox.yMinimum(); max_y = bbox.yMaximum()
        margin_x = (max_x - min_x) * 0.20
        margin_y = (max_y - min_y) * 0.20
        if direction == "Vertical":
            low = min_x; high = max_x; mode = "vertical"
        else:
            low = min_y; high = max_y; mode = "horizontal"
        for _ in range(150):
            mid = (low + high) / 2.0
            if mode == "vertical":
                r1 = QgsRectangle(min_x - margin_x, min_y - margin_y, mid, max_y + margin_y)
                r2 = QgsRectangle(mid, min_y - margin_y, max_x + margin_x, max_y + margin_y)
            else:
                r1 = QgsRectangle(min_x - margin_x, min_y - margin_y, max_x + margin_x, mid)
                r2 = QgsRectangle(min_x - margin_x, mid, max_x + margin_x, max_y + margin_y)
            g1 = geom.intersection(QgsGeometry.fromRect(r1))
            g2 = geom.intersection(QgsGeometry.fromRect(r2))
            if g1 is None or g2 is None or g1.isEmpty() or g2.isEmpty():
                if g1 is None or g1.isEmpty():
                    low = mid
                else:
                    high = mid
                continue
            a1 = g1.area()
            if a1 < target_area:
                low = mid
            else:
                high = mid
            if abs(a1 - target_area) <= tolerance:
                if allow_multipart or (self._is_single(g1) and self._is_single(g2)):
                    return {"geom1": QgsGeometry(g1), "geom2": QgsGeometry(g2)}
        raise Exception("Could not create valid split with this direction. Try another direction or enable multipart mode.")

    def _split_by_parallel_user_line(self, geom, target_area, direction_points, allow_multipart, tolerance):
        p1 = direction_points[0]
        p2 = direction_points[1]
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0:
            raise Exception("Direction line has zero length.")
        ux = dx / length; uy = dy / length; nx = -uy; ny = ux
        bbox = geom.boundingBox()
        corners = [QgsPointXY(bbox.xMinimum(), bbox.yMinimum()), QgsPointXY(bbox.xMinimum(), bbox.yMaximum()), QgsPointXY(bbox.xMaximum(), bbox.yMinimum()), QgsPointXY(bbox.xMaximum(), bbox.yMaximum())]
        u_values = [p.x() * ux + p.y() * uy for p in corners]
        n_values = [p.x() * nx + p.y() * ny for p in corners]
        min_u = min(u_values); max_u = max(u_values); min_n = min(n_values); max_n = max(n_values)
        margin_u = (max_u - min_u) * 0.50
        margin_n = (max_n - min_n) * 0.50
        low = min_n - margin_n
        high = max_n + margin_n
        for _ in range(180):
            mid_n = (low + high) / 2.0
            g1_clip = self._half_plane_polygon(ux, uy, nx, ny, min_u - margin_u, max_u + margin_u, min_n - margin_n, mid_n)
            g2_clip = self._half_plane_polygon(ux, uy, nx, ny, min_u - margin_u, max_u + margin_u, mid_n, max_n + margin_n)
            g1 = geom.intersection(g1_clip)
            g2 = geom.intersection(g2_clip)
            if g1 is None or g2 is None or g1.isEmpty() or g2.isEmpty():
                if g1 is None or g1.isEmpty():
                    low = mid_n
                else:
                    high = mid_n
                continue
            a1 = g1.area()
            if a1 < target_area:
                low = mid_n
            else:
                high = mid_n
            if abs(a1 - target_area) <= tolerance:
                if allow_multipart or (self._is_single(g1) and self._is_single(g2)):
                    return {"geom1": QgsGeometry(g1), "geom2": QgsGeometry(g2)}
        raise Exception("Could not create valid split with the user-defined direction.")

    def _half_plane_polygon(self, ux, uy, nx, ny, min_u, max_u, min_n, max_n):
        local_points = [(min_u, min_n), (max_u, min_n), (max_u, max_n), (min_u, max_n), (min_u, min_n)]
        points = []
        for u, n in local_points:
            x = u * ux + n * nx
            y = u * uy + n * ny
            points.append(QgsPointXY(x, y))
        return QgsGeometry.fromPolygonXY([points])



    def split_by_area_list_axis(self, layer, feature, area_list, direction, direction_points=None, tolerance=0.01, allow_multipart=False, use_opposite_side=False):
        if not area_list:
            raise Exception("Area list is empty.")

        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        if QgsWkbTypes.isMultiType(geom.wkbType()):
            geom = self._largest_polygon_part(geom)
            if geom is None or geom.isEmpty():
                raise Exception("Could not extract polygon part from multipart geometry.")

        total_area = geom.area()

        cumulative_targets = []
        acc = 0.0
        for area in area_list:
            if area <= 0:
                raise Exception("All area values must be greater than zero.")
            acc += area
            if acc >= total_area:
                raise Exception(
                    "Sum of defined areas must be smaller than total polygon area.\n\n"
                    f"Sum: {acc:.3f}\n"
                    f"Total: {total_area:.3f}"
                )
            cumulative_targets.append(acc)

        if len(area_list) == 1:
            result = self.split_by_target_area(
                layer=None,
                feature=feature,
                target_area=area_list[0],
                direction=direction,
                allow_multipart=allow_multipart,
                use_opposite_side=use_opposite_side,
                direction_points=direction_points,
                tolerance=tolerance,
                input_geometry=geom
            )
            parts = [result["geom1"], result["geom2"]]
        else:
            parts = self._split_by_cumulative_offset_areas(
                geom=geom,
                cumulative_targets=cumulative_targets,
                direction=direction,
                direction_points=direction_points,
                tolerance=tolerance,
                reverse_direction=use_opposite_side
            )

        if not allow_multipart:
            for part in parts:
                if not self._is_single(part):
                    raise Exception(
                        "Area-list division produced multipart output.\n\n"
                        "Choose Allow multipart, or try another direction."
                    )

        self._apply_multiple_parts(
            layer=layer,
            feature=feature,
            geometries=parts,
            command_name="Divide polygon by area list + remainder"
        )

        areas = [g.area() for g in parts]
        return {
            "count": len(parts),
            "areas": areas,
            "total": sum(areas),
            "defined_sum": sum(area_list),
            "remainder": areas[-1]
        }

    def _split_by_cumulative_offset_areas(self, geom, cumulative_targets, direction, direction_points=None, tolerance=0.01, reverse_direction=False):
        params = self._offset_coordinate_system(
            geom=geom,
            direction=direction,
            direction_points=direction_points,
            reverse_direction=reverse_direction
        )

        ux = params["ux"]
        uy = params["uy"]
        nx = params["nx"]
        ny = params["ny"]
        min_u = params["min_u"]
        max_u = params["max_u"]
        min_n = params["min_n"]
        max_n = params["max_n"]

        cut_offsets = []
        for cumulative_target in cumulative_targets:
            cut_n = self._solve_offset_for_area(
                geom=geom,
                cumulative_target=cumulative_target,
                ux=ux,
                uy=uy,
                nx=nx,
                ny=ny,
                min_u=min_u,
                max_u=max_u,
                min_n=min_n,
                max_n=max_n,
                tolerance=tolerance
            )
            cut_offsets.append(cut_n)

        boundaries = [min_n] + cut_offsets + [max_n]
        parts = []

        for i in range(len(boundaries) - 1):
            clip = self._half_plane_polygon(
                ux, uy, nx, ny,
                min_u,
                max_u,
                boundaries[i],
                boundaries[i + 1]
            )

            part = geom.intersection(clip)
            if part is None or part.isEmpty():
                raise Exception("Area-list division created an empty polygon part.")

            part = self._largest_polygon_part(part)
            if part is None or part.isEmpty():
                raise Exception("Area-list division could not create a valid polygon part.")

            parts.append(QgsGeometry(part))

        return parts

    def split_equal_parts_boundary_path(self, layer, feature, parts_count, path_points, side_point, tolerance=0.01):
        """
        Sequential Boundary path + perimeter continuation for equal parts.

        This uses the proven two-part boundary splitter repeatedly:
        current polygon -> create target area -> continue with remaining polygon.
        """
        if parts_count < 2:
            raise Exception("Number of parts must be at least 2.")

        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        if QgsWkbTypes.isMultiType(geom.wkbType()):
            geom = self._largest_polygon_part(geom)
            if geom is None or geom.isEmpty():
                raise Exception("Could not extract polygon part from multipart geometry.")

        total_area = geom.area()
        target_area = total_area / parts_count
        solve_tolerance = max(tolerance, total_area * 0.000001)

        parts, remaining = self._sequential_boundary_parts(
            initial_geom=geom,
            target_areas=[target_area] * (parts_count - 1),
            path_points=path_points,
            side_point=side_point,
            tolerance=solve_tolerance
        )

        parts.append(remaining)

        self._apply_multiple_parts(
            layer=layer,
            feature=feature,
            geometries=parts,
            command_name="Divide polygon into equal parts by boundary path perimeter continuation"
        )

        areas = [g.area() for g in parts]

        return {
            "count": len(parts),
            "area_each": target_area,
            "areas": areas,
            "total": sum(areas)
        }

    def split_by_area_list_boundary_path(self, layer, feature, area_list, path_points, side_point, tolerance=0.01):
        """
        Sequential Boundary path + perimeter continuation for area list + remainder.

        Example: 200, 400, 300
        current polygon -> 200 -> remaining -> 400 -> remaining -> 300 -> remainder.
        """
        if not area_list:
            raise Exception("Area list is empty.")

        geom = feature.geometry()
        if geom is None or geom.isEmpty():
            raise Exception("Selected feature has empty geometry.")

        if QgsWkbTypes.isMultiType(geom.wkbType()):
            geom = self._largest_polygon_part(geom)
            if geom is None or geom.isEmpty():
                raise Exception("Could not extract polygon part from multipart geometry.")

        total_area = geom.area()
        acc = 0.0

        for area in area_list:
            if area <= 0:
                raise Exception("All area values must be greater than zero.")
            acc += area
            if acc >= total_area:
                raise Exception(
                    "Sum of defined areas must be smaller than total polygon area.\n\n"
                    f"Sum: {acc:.3f}\n"
                    f"Total: {total_area:.3f}"
                )

        solve_tolerance = max(tolerance, total_area * 0.000001)

        parts, remaining = self._sequential_boundary_parts(
            initial_geom=geom,
            target_areas=area_list,
            path_points=path_points,
            side_point=side_point,
            tolerance=solve_tolerance
        )

        parts.append(remaining)

        self._apply_multiple_parts(
            layer=layer,
            feature=feature,
            geometries=parts,
            command_name="Divide polygon by area list + remainder using boundary path perimeter continuation"
        )

        areas = [g.area() for g in parts]

        return {
            "count": len(parts),
            "areas": areas,
            "total": sum(areas),
            "defined_sum": sum(area_list),
            "remainder": areas[-1]
        }

    def _sequential_boundary_parts(self, initial_geom, target_areas, path_points, side_point, tolerance):
        class _FeatureWrapper:
            def __init__(self, geom):
                self._geom = geom

            def geometry(self):
                return self._geom

        current_geom = QgsGeometry(initial_geom)
        current_path = [QgsPointXY(p) for p in path_points]
        current_side_point = QgsPointXY(side_point)

        parts = []

        for target_area in target_areas:
            if current_geom is None or current_geom.isEmpty():
                raise Exception("Remaining polygon is empty before all parts were created.")

            if target_area <= 0:
                raise Exception("Target area must be greater than zero.")

            if target_area >= current_geom.area():
                raise Exception(
                    "A requested part is larger than the remaining polygon.\n\n"
                    f"Requested: {target_area:.3f}\n"
                    f"Remaining: {current_geom.area():.3f}"
                )

            wrapper = _FeatureWrapper(current_geom)

            preview = self.preview_boundary_path_area(
                feature=wrapper,
                target_area=target_area,
                path_points=current_path,
                side_point=current_side_point,
                tolerance=tolerance
            )

            part_geom = preview["geom1"]
            remaining_geom = preview["geom2"]

            if part_geom is None or part_geom.isEmpty() or part_geom.area() <= 0:
                raise Exception("Created empty polygon part during perimeter continuation.")

            if remaining_geom is None or remaining_geom.isEmpty() or remaining_geom.area() <= 0:
                raise Exception("Created empty remaining polygon during perimeter continuation.")

            parts.append(QgsGeometry(part_geom))

            shared_path = self._extract_shared_boundary_path(part_geom, remaining_geom)

            if not shared_path or len(shared_path) < 2:
                raise Exception(
                    "Could not extract the next internal boundary path for perimeter continuation.\n\n"
                    "Try a simpler first boundary path or another side click."
                )

            current_geom = QgsGeometry(remaining_geom)
            current_path = shared_path
            current_side_point = self._safe_point_inside(current_geom)

        return parts, current_geom

    def _safe_point_inside(self, geom):
        try:
            p = geom.pointOnSurface()
            if p is not None and not p.isEmpty():
                return QgsPointXY(p.asPoint())
        except Exception:
            pass

        c = geom.centroid()
        if c is not None and not c.isEmpty():
            return QgsPointXY(c.asPoint())

        bbox = geom.boundingBox()
        return QgsPointXY(
            (bbox.xMinimum() + bbox.xMaximum()) / 2.0,
            (bbox.yMinimum() + bbox.yMaximum()) / 2.0
        )

    def _extract_shared_boundary_path(self, geom1, geom2):
        try:
            b1 = geom1.boundary()
            b2 = geom2.boundary()
            shared = b1.intersection(b2)

            lines = self._extract_lines_from_geometry(shared)

            if lines:
                best = max(lines, key=lambda pts: self._polyline_length(pts))
                return [QgsPointXY(p) for p in best]
        except Exception:
            pass

        # Fallback: use polygon-boundary intersection directly.
        try:
            shared = geom1.intersection(geom2.boundary())
            lines = self._extract_lines_from_geometry(shared)
            if lines:
                best = max(lines, key=lambda pts: self._polyline_length(pts))
                return [QgsPointXY(p) for p in best]
        except Exception:
            pass

        return None

    def _extract_lines_from_geometry(self, geom):
        if geom is None or geom.isEmpty():
            return []

        lines = []

        try:
            line = geom.asPolyline()
            if line and len(line) >= 2:
                lines.append([QgsPointXY(p) for p in line])
        except Exception:
            pass

        try:
            multi = geom.asMultiPolyline()
            if multi:
                for line in multi:
                    if line and len(line) >= 2:
                        lines.append([QgsPointXY(p) for p in line])
        except Exception:
            pass

        try:
            collection = geom.asGeometryCollection()
            for part in collection:
                if part is None or part.isEmpty():
                    continue
                try:
                    line = part.asPolyline()
                    if line and len(line) >= 2:
                        lines.append([QgsPointXY(p) for p in line])
                except Exception:
                    pass
                try:
                    multi = part.asMultiPolyline()
                    if multi:
                        for line in multi:
                            if line and len(line) >= 2:
                                lines.append([QgsPointXY(p) for p in line])
                except Exception:
                    pass
        except Exception:
            pass

        return lines

    def _polyline_length(self, points):
        total = 0.0
        for i in range(1, len(points)):
            total += self._distance(points[i - 1], points[i])
        return total

    def _apply_split(self, layer, feature, g1, g2, command_name):
        self._apply_multiple_parts(layer, feature, [g1, g2], command_name)

    def _validate_polygon_output_topology(self, original_geom, geometries, command_name="Polygon split"):
        """
        Final cadastral-style topology validation before writing output features.

        Checks:
        - valid polygon geometries
        - no empty geometries
        - no polygon overlaps
        - no meaningful gaps against original polygon
        - output area sum equals original area within tolerance
        - union of outputs equals original polygon within tolerance
        """

        if original_geom is None or original_geom.isEmpty():
            raise Exception("Original polygon is empty.")

        if not geometries or len(geometries) < 2:
            raise Exception("Polygon split must create at least two polygons.")

        original_area = original_geom.area()

        if original_area <= 0:
            raise Exception("Original polygon has zero or negative area.")

        area_tolerance = max(0.01, original_area * 0.00000001)
        overlap_tolerance = max(0.001, original_area * 0.000000001)
        gap_tolerance = max(0.001, original_area * 0.000000001)

        clean_geometries = []

        for i, geom in enumerate(geometries):
            if geom is None or geom.isEmpty():
                raise Exception(f"Output polygon {i + 1} is empty.")

            if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PolygonGeometry:
                raise Exception(f"Output geometry {i + 1} is not polygon.")

            if geom.area() <= 0:
                raise Exception(f"Output polygon {i + 1} has zero area.")

            if not geom.isGeosValid():
                raise Exception(
                    f"Output polygon {i + 1} is not topologically valid.\n\n"
                    "Split was cancelled before writing to layer."
                )

            clean_geometries.append(QgsGeometry(geom))

        # Area sum control
        output_area = sum(g.area() for g in clean_geometries)
        area_difference = abs(output_area - original_area)

        if area_difference > area_tolerance:
            raise Exception(
                "Area balance validation failed.\n\n"
                f"Original area: {original_area:.6f}\n"
                f"Output area sum: {output_area:.6f}\n"
                f"Difference: {area_difference:.6f}\n"
                f"Tolerance: {area_tolerance:.6f}\n\n"
                "Split was cancelled before writing to layer."
            )

        # Overlap control
        for i in range(len(clean_geometries)):
            for j in range(i + 1, len(clean_geometries)):
                inter = clean_geometries[i].intersection(clean_geometries[j])

                if inter is not None and not inter.isEmpty():
                    inter_area = inter.area()

                    if inter_area > overlap_tolerance:
                        raise Exception(
                            "Overlap validation failed.\n\n"
                            f"Output polygons {i + 1} and {j + 1} overlap.\n"
                            f"Overlap area: {inter_area:.6f}\n"
                            f"Tolerance: {overlap_tolerance:.6f}\n\n"
                            "Split was cancelled before writing to layer."
                        )

        # Union control
        try:
            union_geom = QgsGeometry.unaryUnion(clean_geometries)
        except Exception:
            union_geom = QgsGeometry(clean_geometries[0])
            for g in clean_geometries[1:]:
                union_geom = union_geom.combine(g)

        if union_geom is None or union_geom.isEmpty():
            raise Exception("Union of output polygons is empty.")

        if not union_geom.isGeosValid():
            raise Exception(
                "Union of output polygons is not topologically valid.\n\n"
                "Split was cancelled before writing to layer."
            )

        # Gap control: original minus output union
        gap_geom = original_geom.difference(union_geom)

        if gap_geom is not None and not gap_geom.isEmpty():
            gap_area = gap_geom.area()

            if gap_area > gap_tolerance:
                raise Exception(
                    "Gap validation failed.\n\n"
                    f"Gap area: {gap_area:.6f}\n"
                    f"Tolerance: {gap_tolerance:.6f}\n\n"
                    "Split was cancelled before writing to layer."
                )

        # Extra control: output union outside original
        outside_geom = union_geom.difference(original_geom)

        if outside_geom is not None and not outside_geom.isEmpty():
            outside_area = outside_geom.area()

            if outside_area > gap_tolerance:
                raise Exception(
                    "Outside-original validation failed.\n\n"
                    f"Outside area: {outside_area:.6f}\n"
                    f"Tolerance: {gap_tolerance:.6f}\n\n"
                    "Split was cancelled before writing to layer."
                )

        return True

    def _apply_multiple_parts(self, layer, feature, geometries, command_name):
        original_geom = QgsGeometry(feature.geometry())

        self._validate_polygon_output_topology(
            original_geom=original_geom,
            geometries=geometries,
            command_name=command_name
        )

        layer.beginEditCommand(command_name)

        try:
            attrs = feature.attributes()

            if not layer.deleteFeature(feature.id()):
                raise Exception("Could not delete original polygon.")

            for geom in geometries:
                if geom is None or geom.isEmpty():
                    raise Exception("Created empty polygon.")

                f = QgsFeature(layer.fields())
                f.setGeometry(geom)
                f.setAttributes(attrs)

                if not layer.addFeature(f):
                    raise Exception("Could not add polygon part.")

            layer.endEditCommand()
            layer.triggerRepaint()

        except Exception as e:
            layer.destroyEditCommand()
            raise e

    def _is_single(self, geometry):
        if geometry is None or geometry.isEmpty():
            return False
        if QgsWkbTypes.isMultiType(geometry.wkbType()):
            return False
        if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry:
            return False
        return geometry.area() > 0

    def _distance(self, p1, p2):
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        return math.sqrt(dx * dx + dy * dy)

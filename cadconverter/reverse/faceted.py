from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path

import numpy as np
import trimesh
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
)
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeSphere
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Interface import Interface_Static
from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Controller, STEPControl_Writer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import Shell as TopoDS_Shell
from OCC.Core.TopoDS import TopoDS_Compound
from OCC.Core.gp import gp_Ax2, gp_Ax3, gp_Cylinder, gp_Dir, gp_Pnt


@dataclass(frozen=True)
class ReverseReport:
    source: Path
    output: Path
    mode: str
    mesh_count: int
    input_triangle_count: int
    exported_face_count: int
    skipped_face_count: int
    unit_scale: float
    planar_region_count: int = 0
    fallback_face_count: int = 0
    merged_triangle_count: int = 0
    holed_planar_region_count: int = 0
    hole_candidate_count: int = 0
    clean_hole_candidate_count: int = 0
    hole_loop_count: int = 0
    sewing_enabled: bool = False
    sewing_tolerance: float = 0.0
    sewn_free_edge_count: int = 0
    sewn_contiguous_edge_count: int = 0
    cylinder_candidate_count: int = 0
    cylinder_triangle_count: int = 0
    cylinder_average_radius: float = 0.0
    analytic_cylinder_count: int = 0
    analytic_cylinder_triangle_count: int = 0
    boundary_trimmed_cylinder_count: int = 0
    skipped_cylinder_candidate_count: int = 0
    analytic_cylinder_fallback: bool = False
    sphere_candidate_count: int = 0
    sphere_triangle_count: int = 0
    sphere_average_radius: float = 0.0
    analytic_sphere_count: int = 0
    analytic_sphere_triangle_count: int = 0
    skipped_sphere_candidate_count: int = 0
    cone_candidate_count: int = 0
    cone_triangle_count: int = 0
    cone_average_angle_degrees: float = 0.0
    analytic_cone_count: int = 0
    analytic_cone_triangle_count: int = 0
    skipped_cone_candidate_count: int = 0
    topology_valid: bool = False
    topology_face_count: int = 0
    topology_shell_count: int = 0
    topology_closed_shell_count: int = 0
    topology_solid_count: int = 0
    solid_promotion_enabled: bool = False
    promoted_solid_count: int = 0
    solid_promotion_fallback: bool = False

    @property
    def ok(self) -> bool:
        return self.exported_face_count > 0


def _load_scene(glb_path: Path) -> trimesh.Scene:
    loaded = trimesh.load(str(glb_path), force="scene", process=False)
    if isinstance(loaded, trimesh.Trimesh):
        scene = trimesh.Scene()
        scene.add_geometry(loaded, node_name=glb_path.stem)
        return scene
    if not isinstance(loaded, trimesh.Scene):
        raise TypeError(f"Unsupported GLB content: {type(loaded)!r}")
    return loaded


def _iter_scene_meshes(scene: trimesh.Scene):
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        mesh = scene.geometry.get(geometry_name)
        if mesh is None:
            continue
        if not isinstance(mesh, trimesh.Trimesh):
            continue
        yield node_name, mesh, np.asarray(transform, dtype=np.float64)


def _transformed_vertices(mesh: trimesh.Trimesh, transform: np.ndarray, unit_scale: float):
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if len(vertices) == 0:
        return vertices
    homogeneous = np.column_stack([vertices, np.ones(len(vertices), dtype=np.float64)])
    return (transform @ homogeneous.T).T[:, :3] * unit_scale


def _triangle_face(points: np.ndarray, tolerance: float):
    a, b, c = points
    if np.linalg.norm(np.cross(b - a, c - a)) <= tolerance:
        return None

    polygon = BRepBuilderAPI_MakePolygon(
        gp_Pnt(float(a[0]), float(a[1]), float(a[2])),
        gp_Pnt(float(b[0]), float(b[1]), float(b[2])),
        gp_Pnt(float(c[0]), float(c[1]), float(c[2])),
        True,
    )
    if not polygon.IsDone():
        return None

    face = BRepBuilderAPI_MakeFace(polygon.Wire())
    if not face.IsDone():
        return None
    return face.Face()


def _wire_from_points(points: np.ndarray):
    polygon = BRepBuilderAPI_MakePolygon()
    for point in points:
        polygon.Add(gp_Pnt(float(point[0]), float(point[1]), float(point[2])))
    polygon.Close()
    if not polygon.IsDone():
        return None
    return polygon.Wire()


def _add_mesh_faces(builder: BRep_Builder, compound: TopoDS_Compound, mesh, vertices, tolerance):
    exported = 0
    skipped = 0

    for face_indices in np.asarray(mesh.faces, dtype=np.int64):
        if len(face_indices) != 3:
            skipped += 1
            continue

        face = _triangle_face(vertices[face_indices], tolerance)
        if face is None:
            skipped += 1
            continue

        builder.Add(compound, face)
        exported += 1

    return exported, skipped


def _canonical_plane(normal: np.ndarray, point: np.ndarray):
    offset = float(np.dot(normal, point))
    axis = int(np.argmax(np.abs(normal)))
    if normal[axis] < 0:
        normal = -normal
        offset = -offset
    return normal, offset


def _face_planes(vertices: np.ndarray, faces: np.ndarray, tolerance: float):
    normals = np.zeros((len(faces), 3), dtype=np.float64)
    offsets = np.zeros(len(faces), dtype=np.float64)
    valid = np.zeros(len(faces), dtype=bool)

    for index, face in enumerate(faces):
        a, b, c = vertices[face]
        cross = np.cross(b - a, c - a)
        length = np.linalg.norm(cross)
        if length <= tolerance:
            continue
        normal, offset = _canonical_plane(cross / length, a)
        normals[index] = normal
        offsets[index] = offset
        valid[index] = True

    return normals, offsets, valid


def _raw_face_normals(vertices: np.ndarray, faces: np.ndarray, tolerance: float):
    normals = np.zeros((len(faces), 3), dtype=np.float64)
    valid = np.zeros(len(faces), dtype=bool)

    for index, face in enumerate(faces):
        a, b, c = vertices[face]
        cross = np.cross(b - a, c - a)
        length = np.linalg.norm(cross)
        if length <= tolerance:
            continue
        normals[index] = cross / length
        valid[index] = True

    return normals, valid


def _coplanar_regions(mesh, vertices, angle_tolerance_degrees, plane_tolerance, tolerance):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return [], set()

    normals, offsets, valid = _face_planes(vertices, faces, tolerance)
    normal_threshold = float(np.cos(np.deg2rad(angle_tolerance_degrees)))
    adjacency = [[] for _ in range(len(faces))]

    for left, right in np.asarray(mesh.face_adjacency, dtype=np.int64):
        if not valid[left] or not valid[right]:
            continue
        if float(np.dot(normals[left], normals[right])) < normal_threshold:
            continue
        if abs(float(offsets[left] - offsets[right])) > plane_tolerance:
            continue
        adjacency[left].append(int(right))
        adjacency[right].append(int(left))

    visited = np.zeros(len(faces), dtype=bool)
    regions = []
    invalid = set(np.flatnonzero(~valid).tolist())

    for start in range(len(faces)):
        if visited[start] or not valid[start]:
            continue
        stack = [start]
        visited[start] = True
        region = []
        while stack:
            current = stack.pop()
            region.append(current)
            for neighbor in adjacency[current]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)
        regions.append(region)

    return regions, invalid


def _smooth_regions(mesh, vertices, smooth_angle_degrees, tolerance):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return []

    normals, valid = _raw_face_normals(vertices, faces, tolerance)
    normal_threshold = float(np.cos(np.deg2rad(smooth_angle_degrees)))
    adjacency = [[] for _ in range(len(faces))]

    for left, right in np.asarray(mesh.face_adjacency, dtype=np.int64):
        if not valid[left] or not valid[right]:
            continue
        if float(np.dot(normals[left], normals[right])) < normal_threshold:
            continue
        adjacency[left].append(int(right))
        adjacency[right].append(int(left))

    visited = np.zeros(len(faces), dtype=bool)
    regions = []
    for start in range(len(faces)):
        if visited[start] or not valid[start]:
            continue
        stack = [start]
        visited[start] = True
        region = []
        while stack:
            current = stack.pop()
            region.append(current)
            for neighbor in adjacency[current]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)
        regions.append(region)

    return regions


def _fit_circle_2d(points_2d: np.ndarray):
    x = points_2d[:, 0]
    y = points_2d[:, 1]
    system = np.column_stack([2.0 * x, 2.0 * y, np.ones(len(points_2d))])
    values = x * x + y * y
    solution, *_ = np.linalg.lstsq(system, values, rcond=None)
    center = solution[:2]
    radius_sq = float(solution[2] + np.dot(center, center))
    if radius_sq <= 0.0:
        return None
    return center, float(np.sqrt(radius_sq))


def _angular_bounds(points_2d: np.ndarray, center_2d: np.ndarray):
    angles = np.mod(np.arctan2(points_2d[:, 1] - center_2d[1], points_2d[:, 0] - center_2d[0]), 2.0 * np.pi)
    if len(angles) == 0:
        return 0.0, 0.0, 0.0

    sorted_angles = np.sort(angles)
    wrapped = np.append(sorted_angles, sorted_angles[0] + 2.0 * np.pi)
    gaps = np.diff(wrapped)
    gap_index = int(np.argmax(gaps))
    largest_gap = float(gaps[gap_index])
    span = float(2.0 * np.pi - largest_gap)

    if span >= np.deg2rad(330.0):
        return 0.0, 2.0 * np.pi, 2.0 * np.pi

    start = float(wrapped[gap_index + 1] % (2.0 * np.pi))
    end = start + span
    return start, end, span


def _orthonormal_basis(axis: np.ndarray):
    helper = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(helper, axis))) > 0.9:
        helper = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    v /= np.linalg.norm(v)
    return u, v


def _fit_cylinder_region(vertices, faces, normals, region, *, tolerance, max_radius_error_ratio):
    region_faces = faces[np.asarray(region, dtype=np.int64)]
    region_normals = normals[np.asarray(region, dtype=np.int64)]
    if len(region_normals) < 3:
        return None

    _, singular_values, vh = np.linalg.svd(region_normals, full_matrices=False)
    if singular_values[-1] / max(singular_values[0], tolerance) > 0.18:
        return None

    axis = vh[-1]
    axis /= np.linalg.norm(axis)

    unique_vertex_indices = np.unique(region_faces.reshape(-1))
    points = vertices[unique_vertex_indices]
    if len(points) < 8:
        return None

    origin = points.mean(axis=0)
    u, v = _orthonormal_basis(axis)
    relative = points - origin
    points_2d = np.column_stack([relative @ u, relative @ v])
    circle = _fit_circle_2d(points_2d)
    if circle is None:
        return None

    center_2d, radius = circle
    if radius <= tolerance:
        return None

    distances = np.linalg.norm(points_2d - center_2d, axis=1)
    radius_error = float(np.sqrt(np.mean((distances - radius) ** 2)))
    if radius_error / radius > max_radius_error_ratio:
        return None

    radial_2d = points_2d - center_2d
    u_min, u_max, angular_span_radians = _angular_bounds(points_2d, center_2d)
    angular_span = float(np.rad2deg(angular_span_radians))
    if angular_span < 20.0:
        return None

    center = origin + center_2d[0] * u + center_2d[1] * v
    heights = (points - center) @ axis
    v_min = float(np.min(heights))
    v_max = float(np.max(heights))
    if abs(v_max - v_min) <= tolerance:
        return None

    return {
        "region": tuple(int(index) for index in region),
        "face_count": len(region),
        "axis": axis,
        "center": center,
        "u_direction": u,
        "radius": radius,
        "radius_error": radius_error,
        "u_min": u_min,
        "u_max": u_max,
        "v_min": v_min,
        "v_max": v_max,
        "angular_span": min(360.0, angular_span),
    }


def _detect_cylinder_candidates(
    mesh,
    vertices,
    *,
    tolerance,
    smooth_angle_degrees=35.0,
    min_faces=12,
    max_radius_error_ratio=0.03,
):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return []

    normals, valid = _raw_face_normals(vertices, faces, tolerance)
    candidates = []
    for region in _smooth_regions(mesh, vertices, smooth_angle_degrees, tolerance):
        if len(region) < min_faces:
            continue
        if not all(valid[index] for index in region):
            continue
        candidate = _fit_cylinder_region(
            vertices,
            faces,
            normals,
            region,
            tolerance=tolerance,
            max_radius_error_ratio=max_radius_error_ratio,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _fit_sphere_region(vertices, faces, region, *, tolerance, max_radius_error_ratio):
    region_faces = faces[np.asarray(region, dtype=np.int64)]
    unique_vertex_indices = np.unique(region_faces.reshape(-1))
    points = vertices[unique_vertex_indices]
    if len(points) < 12:
        return None

    system = np.column_stack([2.0 * points, np.ones(len(points))])
    values = np.sum(points * points, axis=1)
    solution, *_ = np.linalg.lstsq(system, values, rcond=None)
    center = solution[:3]
    radius_sq = float(solution[3] + np.dot(center, center))
    if radius_sq <= tolerance:
        return None

    radius = float(np.sqrt(radius_sq))
    distances = np.linalg.norm(points - center, axis=1)
    radius_error = float(np.sqrt(np.mean((distances - radius) ** 2)))
    if radius_error / radius > max_radius_error_ratio:
        return None

    directions = (points - center) / radius
    axis_spans = np.ptp(directions, axis=0)
    coverage_ratio = float(np.min(axis_spans) / 2.0)
    full_coverage = bool(np.all(axis_spans >= 1.5))

    return {
        "region": tuple(int(index) for index in region),
        "face_count": len(region),
        "center": center,
        "radius": radius,
        "radius_error": radius_error,
        "coverage_ratio": max(0.0, min(1.0, coverage_ratio)),
        "full_coverage": full_coverage,
    }


def _detect_sphere_candidates(
    mesh,
    vertices,
    *,
    tolerance,
    smooth_angle_degrees=35.0,
    min_faces=24,
    max_radius_error_ratio=0.02,
):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return []

    candidates = []
    for region in _smooth_regions(mesh, vertices, smooth_angle_degrees, tolerance):
        if len(region) < min_faces:
            continue
        candidate = _fit_sphere_region(
            vertices,
            faces,
            region,
            tolerance=tolerance,
            max_radius_error_ratio=max_radius_error_ratio,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _fit_cone_region(vertices, faces, region, *, tolerance, max_radius_error_ratio):
    region_faces = faces[np.asarray(region, dtype=np.int64)]
    unique_vertex_indices = np.unique(region_faces.reshape(-1))
    points = vertices[unique_vertex_indices]
    if len(points) < 12:
        return None

    reference = points.mean(axis=0)
    centered = points - reference
    _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    if singular_values[0] <= tolerance:
        return None

    axis = vh[-1]
    axis /= np.linalg.norm(axis)
    heights = centered @ axis
    radial_vectors = centered - np.outer(heights, axis)
    radii = np.linalg.norm(radial_vectors, axis=1)

    system = np.column_stack([heights, np.ones(len(heights))])
    slope_intercept, *_ = np.linalg.lstsq(system, radii, rcond=None)
    slope, intercept = (float(value) for value in slope_intercept)
    predicted = slope * heights + intercept
    if np.max(predicted) <= tolerance:
        return None

    mean_radius = float(np.mean(radii))
    if mean_radius <= tolerance:
        return None

    radius_error = float(np.sqrt(np.mean((radii - predicted) ** 2)))
    if radius_error / mean_radius > max_radius_error_ratio:
        return None

    if abs(slope) < 0.04:
        return None

    height_span = float(np.max(heights) - np.min(heights))
    radius_span = float(abs(slope) * height_span)
    if height_span <= tolerance or radius_span / mean_radius < 0.18:
        return None

    h_min = float(np.min(heights))
    h_max = float(np.max(heights))
    radius_min = max(0.0, float(slope * h_min + intercept))
    radius_max = max(0.0, float(slope * h_max + intercept))
    if max(radius_min, radius_max) <= tolerance:
        return None

    u, v = _orthonormal_basis(axis)
    radial = centered - np.outer(heights, axis)
    radial_lengths = np.linalg.norm(radial, axis=1)
    angular_points = radial[radial_lengths > max(tolerance, max(radius_min, radius_max) * 0.2)]
    if len(angular_points) < 6:
        return None
    points_2d = np.column_stack([angular_points @ u, angular_points @ v])
    _, _, angular_span_radians = _angular_bounds(points_2d, np.array([0.0, 0.0]))
    angular_span = float(np.rad2deg(angular_span_radians))

    semi_angle = float(np.rad2deg(np.arctan(abs(slope))))
    return {
        "region": tuple(int(index) for index in region),
        "face_count": len(region),
        "axis": axis,
        "reference": reference,
        "h_min": h_min,
        "h_max": h_max,
        "height": height_span,
        "radius_min": radius_min,
        "radius_max": radius_max,
        "mean_radius": mean_radius,
        "radius_error": radius_error,
        "angular_span": min(360.0, angular_span),
        "full_coverage": angular_span >= 330.0,
        "semi_angle_degrees": semi_angle,
    }


def _detect_cone_candidates(
    mesh,
    vertices,
    *,
    tolerance,
    smooth_angle_degrees=35.0,
    min_faces=24,
    max_radius_error_ratio=0.035,
):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return []

    candidates = []
    for region in _smooth_regions(mesh, vertices, smooth_angle_degrees, tolerance):
        if len(region) < min_faces:
            continue
        candidate = _fit_cone_region(
            vertices,
            faces,
            region,
            tolerance=tolerance,
            max_radius_error_ratio=max_radius_error_ratio,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _sphere_solid(candidate):
    center = candidate["center"]
    maker = BRepPrimAPI_MakeSphere(
        gp_Pnt(float(center[0]), float(center[1]), float(center[2])),
        float(candidate["radius"]),
    )
    solid = maker.Solid()
    if not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def _cone_solid(candidate):
    reference = candidate["reference"]
    axis = candidate["axis"]
    h_min = float(candidate["h_min"])
    height = float(candidate["height"])
    base = reference + h_min * axis
    maker = BRepPrimAPI_MakeCone(
        gp_Ax2(
            gp_Pnt(float(base[0]), float(base[1]), float(base[2])),
            gp_Dir(float(axis[0]), float(axis[1]), float(axis[2])),
        ),
        float(candidate["radius_min"]),
        float(candidate["radius_max"]),
        height,
    )
    solid = maker.Solid()
    if not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def _cone_consumed_faces(candidate, vertices, faces, tolerance):
    consumed = set(candidate["region"])
    reference = candidate["reference"]
    axis = candidate["axis"]
    h_min = float(candidate["h_min"])
    h_max = float(candidate["h_max"])
    height_tolerance = max(float(tolerance) * 1000.0, float(candidate["height"]) * 0.002)

    for face_index, face in enumerate(faces):
        if face_index in consumed:
            continue
        heights = (vertices[face] - reference) @ axis
        if np.all(np.abs(heights - h_min) <= height_tolerance) or np.all(
            np.abs(heights - h_max) <= height_tolerance
        ):
            consumed.add(int(face_index))

    return consumed


def _cylinder_surface(candidate):
    center = candidate["center"]
    axis = candidate["axis"]
    x_direction = candidate["u_direction"]
    return gp_Cylinder(
        gp_Ax3(
            gp_Pnt(float(center[0]), float(center[1]), float(center[2])),
            gp_Dir(float(axis[0]), float(axis[1]), float(axis[2])),
            gp_Dir(float(x_direction[0]), float(x_direction[1]), float(x_direction[2])),
        ),
        float(candidate["radius"]),
    )


def _project_points_to_cylinder(points: np.ndarray, candidate, tolerance: float):
    center = candidate["center"]
    axis = candidate["axis"]
    radius = float(candidate["radius"])
    projected = []

    for point in points:
        relative = point - center
        height = float(np.dot(relative, axis))
        radial = relative - height * axis
        radial_length = float(np.linalg.norm(radial))
        if radial_length <= tolerance:
            return None
        projected.append(center + height * axis + radius * radial / radial_length)

    return np.asarray(projected, dtype=np.float64)


def _cylinder_boundary_face(candidate, vertices, faces, tolerance: float):
    loops = _region_boundary_loops(faces, candidate["region"])
    if len(loops) != 1:
        return None

    points = vertices[np.asarray(loops[0], dtype=np.int64)]
    points = _remove_collinear_points(points, tolerance)
    if len(points) < 4:
        return None

    projected = _project_points_to_cylinder(points, candidate, tolerance)
    if projected is None:
        return None

    wire = _wire_from_points(projected)
    if wire is None:
        return None

    face = BRepBuilderAPI_MakeFace(_cylinder_surface(candidate), wire, True)
    if not face.IsDone():
        return None
    return face.Face()


def _cylinder_rectangular_face(candidate):
    cylinder = _cylinder_surface(candidate)
    face = BRepBuilderAPI_MakeFace(
        cylinder,
        float(candidate["u_min"]),
        float(candidate["u_max"]),
        float(candidate["v_min"]),
        float(candidate["v_max"]),
    )
    if not face.IsDone():
        return None
    return face.Face()


def _cylinder_face(candidate, vertices, faces, tolerance: float):
    boundary_face = _cylinder_boundary_face(candidate, vertices, faces, tolerance)
    if boundary_face is not None:
        return boundary_face, True

    rectangular_face = _cylinder_rectangular_face(candidate)
    if rectangular_face is None:
        return None, False
    return rectangular_face, False


def _region_boundary_loops(faces: np.ndarray, region):
    edge_count = defaultdict(int)
    for face_index in region:
        a, b, c = (int(value) for value in faces[face_index])
        for edge in ((a, b), (b, c), (c, a)):
            edge_count[tuple(sorted(edge))] += 1

    boundary_edges = [edge for edge, count in edge_count.items() if count == 1]
    neighbors = defaultdict(list)
    for a, b in boundary_edges:
        neighbors[a].append(b)
        neighbors[b].append(a)

    if any(len(vertex_neighbors) != 2 for vertex_neighbors in neighbors.values()):
        return []

    remaining = {tuple(sorted(edge)) for edge in boundary_edges}
    loops = []

    while remaining:
        start, next_vertex = next(iter(remaining))
        remaining.remove(tuple(sorted((start, next_vertex))))
        loop = [start, next_vertex]
        previous = start
        current = next_vertex

        while current != start:
            candidates = [
                neighbor
                for neighbor in neighbors[current]
                if neighbor != previous and tuple(sorted((current, neighbor))) in remaining
            ]
            if not candidates:
                loops = []
                remaining.clear()
                break
            following = candidates[0]
            remaining.remove(tuple(sorted((current, following))))
            previous = current
            current = following
            if current != start:
                loop.append(current)

        if len(loop) >= 3:
            loops.append(loop)

    return loops


def _remove_collinear_points(points: np.ndarray, tolerance: float):
    cleaned = []
    for point in points:
        if not cleaned or np.linalg.norm(point - cleaned[-1]) > tolerance:
            cleaned.append(point)

    if len(cleaned) > 1 and np.linalg.norm(cleaned[0] - cleaned[-1]) <= tolerance:
        cleaned.pop()

    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        next_points = []
        count = len(cleaned)
        for index, point in enumerate(cleaned):
            previous = cleaned[(index - 1) % count]
            following = cleaned[(index + 1) % count]
            if np.linalg.norm(np.cross(point - previous, following - point)) <= tolerance:
                changed = True
                continue
            next_points.append(point)
        cleaned = next_points

    return np.asarray(cleaned, dtype=np.float64)


def _orientation(a, b, c):
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _segments_intersect(a, b, c, d, tolerance: float):
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    return (o1 * o2 < -tolerance) and (o3 * o4 < -tolerance)


def _has_self_intersection(points_2d: np.ndarray, tolerance: float):
    count = len(points_2d)
    for left in range(count):
        a = points_2d[left]
        b = points_2d[(left + 1) % count]
        for right in range(left + 1, count):
            if abs(left - right) <= 1:
                continue
            if left == 0 and right == count - 1:
                continue
            c = points_2d[right]
            d = points_2d[(right + 1) % count]
            if _segments_intersect(a, b, c, d, tolerance):
                return True
    return False


def _signed_area_2d(points_2d: np.ndarray):
    x = points_2d[:, 0]
    y = points_2d[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _projected_area(points: np.ndarray, normal: np.ndarray):
    axis = int(np.argmax(np.abs(normal)))
    projected = np.delete(points, axis, axis=1)
    return _signed_area_2d(projected)


def _point_in_polygon(point: np.ndarray, polygon: np.ndarray):
    inside = False
    previous = polygon[-1]
    for current in polygon:
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            slope_x = (previous[0] - current[0]) * (point[1] - current[1])
            slope_x /= previous[1] - current[1]
            if point[0] < slope_x + current[0]:
                inside = not inside
        previous = current
    return inside


def _loops_intersect(left: np.ndarray, right: np.ndarray, tolerance: float):
    for left_index in range(len(left)):
        a = left[left_index]
        b = left[(left_index + 1) % len(left)]
        for right_index in range(len(right)):
            c = right[right_index]
            d = right[(right_index + 1) % len(right)]
            if _segments_intersect(a, b, c, d, tolerance):
                return True
    return False


def _clean_projected_loop(vertices: np.ndarray, loop, normal: np.ndarray, tolerance: float):
    axis = int(np.argmax(np.abs(normal)))
    points = vertices[np.asarray(loop, dtype=np.int64)]
    points = _remove_collinear_points(points, tolerance)
    if len(points) < 3:
        return None

    projected = np.delete(points, axis, axis=1)
    area = _signed_area_2d(projected)
    if abs(area) <= tolerance:
        return None
    if _has_self_intersection(projected, tolerance):
        return None

    return {
        "points": points,
        "projected": projected,
        "area": area,
    }


def _orient_loop(points: np.ndarray, area: float, reverse: bool):
    should_reverse = (area > 0) == reverse
    if should_reverse:
        return points[::-1]
    return points


def _clean_planar_region_loops(vertices: np.ndarray, faces: np.ndarray, region, tolerance: float):
    loops = _region_boundary_loops(faces, region)
    if not loops:
        return None

    normal, _, valid = _face_planes(vertices, faces[[region[0]]], tolerance)
    if not valid[0]:
        return None

    cleaned = [
        loop
        for loop in (_clean_projected_loop(vertices, loop, normal[0], tolerance) for loop in loops)
        if loop is not None
    ]
    if len(cleaned) != len(loops):
        return None
    cleaned.sort(key=lambda loop: abs(loop["area"]), reverse=True)
    outer = cleaned[0]
    holes = cleaned[1:]

    for hole in holes:
        if _loops_intersect(outer["projected"], hole["projected"], tolerance):
            return None
        if not _point_in_polygon(hole["projected"][0], outer["projected"]):
            return None

    for index, left in enumerate(holes):
        for right in holes[index + 1 :]:
            if _loops_intersect(left["projected"], right["projected"], tolerance):
                return None
            if _point_in_polygon(left["projected"][0], right["projected"]):
                return None
            if _point_in_polygon(right["projected"][0], left["projected"]):
                return None

    return outer, holes


def _planar_region_face(
    vertices: np.ndarray,
    faces: np.ndarray,
    region,
    tolerance: float,
    max_hole_loop_points: int | None = 48,
):
    cleaned = _clean_planar_region_loops(vertices, faces, region, tolerance)
    if cleaned is None:
        return None

    outer, holes = cleaned
    if holes and max_hole_loop_points is not None:
        for hole in holes:
            if len(hole["points"]) > max_hole_loop_points:
                return None

    outer_points = _orient_loop(outer["points"], outer["area"], reverse=False)
    outer_wire = _wire_from_points(outer_points)
    if outer_wire is None:
        return None

    maker = BRepBuilderAPI_MakeFace(outer_wire)
    for hole in holes:
        hole_points = _orient_loop(hole["points"], hole["area"], reverse=True)
        hole_wire = _wire_from_points(hole_points)
        if hole_wire is None:
            return None
        maker.Add(hole_wire)

    if not maker.IsDone():
        return None

    face = maker.Face()
    try:
        if not BRepCheck_Analyzer(face).IsValid():
            return None
    except Exception:
        return None

    return face, len(holes)


def _planar_hole_candidate(vertices: np.ndarray, faces: np.ndarray, region, tolerance: float):
    loops = _region_boundary_loops(faces, region)
    if len(loops) <= 1:
        return 0, False

    cleaned = _clean_planar_region_loops(vertices, faces, region, tolerance)
    if cleaned is None:
        return len(loops) - 1, False

    _, holes = cleaned
    return len(holes), bool(holes)

def _fallback_region_triangles(builder, compound, vertices, faces, region, tolerance):
    exported = 0
    skipped = 0
    for face_index in region:
        face = _triangle_face(vertices[faces[face_index]], tolerance)
        if face is None:
            skipped += 1
            continue
        builder.Add(compound, face)
        exported += 1
    return exported, skipped


def _add_reconstructed_mesh_faces(
    builder: BRep_Builder,
    compound: TopoDS_Compound,
    mesh,
    vertices,
    *,
    tolerance,
    angle_tolerance_degrees,
    plane_tolerance,
    min_region_faces,
    reconstruct_holes,
    max_hole_loop_points,
    consumed_faces=None,
):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    consumed_faces = set(consumed_faces or ())
    regions, invalid = _coplanar_regions(
        mesh,
        vertices,
        angle_tolerance_degrees,
        plane_tolerance,
        tolerance,
    )

    exported = 0
    skipped = 0
    planar_regions = 0
    fallback_faces = 0
    merged_triangles = 0
    holed_planar_regions = 0
    hole_candidates = 0
    clean_hole_candidates = 0
    hole_loops = 0

    for region in regions:
        region = [face_index for face_index in region if face_index not in consumed_faces]
        if not region:
            continue

        if len(region) >= min_region_faces:
            loops = _region_boundary_loops(faces, region)
            if len(loops) > 1:
                candidate_holes, clean_candidate = _planar_hole_candidate(vertices, faces, region, tolerance)
                hole_candidates += 1
                hole_loops += candidate_holes
                if clean_candidate:
                    clean_hole_candidates += 1
            result = None
            if len(loops) == 1 or (reconstruct_holes and len(loops) > 1):
                result = _planar_region_face(
                    vertices,
                    faces,
                    region,
                    tolerance,
                    max_hole_loop_points=max_hole_loop_points,
                )
            if result is not None:
                face, hole_count = result
                builder.Add(compound, face)
                exported += 1
                planar_regions += 1
                if hole_count:
                    holed_planar_regions += 1
                merged_triangles += len(region)
                continue

        fallback_exported, fallback_skipped = _fallback_region_triangles(
            builder, compound, vertices, faces, region, tolerance
        )
        exported += fallback_exported
        skipped += fallback_skipped
        fallback_faces += fallback_exported

    for face_index in invalid:
        if face_index in consumed_faces:
            continue
        face = _triangle_face(vertices[faces[face_index]], tolerance)
        if face is None:
            skipped += 1
            continue
        builder.Add(compound, face)
        exported += 1
        fallback_faces += 1

    return (
        exported,
        skipped,
        planar_regions,
        fallback_faces,
        merged_triangles,
        holed_planar_regions,
        hole_candidates,
        clean_hole_candidates,
        hole_loops,
    )


def _configure_step_export(output: Path) -> None:
    STEPControl_Controller.Init()
    product_name = output.stem.strip() or "CadConverter faceted mesh"
    Interface_Static.SetCVal("write.step.product.name", product_name[:128])
    Interface_Static.SetCVal("write.step.assembly", "Off")


def _sew_shape(shape, sewing_tolerance: float):
    sewing = BRepBuilderAPI_Sewing()
    sewing.SetTolerance(float(sewing_tolerance))
    sewing.SetFaceMode(True)
    sewing.SetFloatingEdgesMode(True)
    sewing.SetSameParameterMode(True)
    sewing.SetNonManifoldMode(False)
    sewing.Add(shape)
    sewing.Perform()
    return sewing.SewedShape(), sewing.NbFreeEdges(), sewing.NbContigousEdges()


def _count_subshapes(shape, shape_type):
    explorer = TopExp_Explorer()
    explorer.Init(shape, shape_type)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _count_closed_shells(shape):
    explorer = TopExp_Explorer()
    explorer.Init(shape, TopAbs_SHELL)
    count = 0
    while explorer.More():
        try:
            if BRep_Tool.IsClosed(explorer.Current()):
                count += 1
        except Exception:
            pass
        explorer.Next()
    return count


def _topology_diagnostics(shape):
    try:
        is_valid = bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:
        is_valid = False
    return {
        "valid": is_valid,
        "faces": _count_subshapes(shape, TopAbs_FACE),
        "shells": _count_subshapes(shape, TopAbs_SHELL),
        "closed_shells": _count_closed_shells(shape),
        "solids": _count_subshapes(shape, TopAbs_SOLID),
    }


def _combine_shapes(*shapes):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        if shape is not None:
            builder.Add(compound, shape)
    return compound


def _promote_closed_shells_to_solids(shape):
    topology = _topology_diagnostics(shape)
    if topology["shells"] == 0:
        return shape, 0, True

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)

    explorer = TopExp_Explorer()
    explorer.Init(shape, TopAbs_SHELL)
    promoted = 0
    while explorer.More():
        shell = TopoDS_Shell(explorer.Current())
        if BRep_Tool.IsClosed(shell):
            maker = BRepBuilderAPI_MakeSolid(shell)
            if maker.IsDone() and BRepCheck_Analyzer(maker.Solid()).IsValid():
                builder.Add(compound, maker.Solid())
                promoted += 1
            else:
                builder.Add(compound, shell)
        else:
            builder.Add(compound, shell)
        explorer.Next()

    if promoted == 0:
        return shape, 0, True

    try:
        if not BRepCheck_Analyzer(compound).IsValid():
            return shape, 0, True
    except Exception:
        return shape, 0, True

    return compound, promoted, False


def _write_step(root, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _configure_step_export(output)
    writer = STEPControl_Writer()
    writer.Transfer(root, STEPControl_AsIs)
    status = writer.Write(str(output))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to write STEP file: {output}")


def glb_to_faceted_step(
    glb_path,
    step_path,
    *,
    unit_scale: float = 1000.0,
    tolerance: float = 1.0e-12,
) -> ReverseReport:
    """Convert a GLB mesh scene into a faceted STEP file.

    This is a mesh-preserving reverse conversion. It creates planar STEP faces
    from triangles, not recovered analytic CAD surfaces.
    """
    source = Path(glb_path)
    output = Path(step_path)
    if not source.is_file():
        raise FileNotFoundError(f"GLB file not found: {source}")

    scene = _load_scene(source)

    builder = BRep_Builder()
    root = TopoDS_Compound()
    builder.MakeCompound(root)

    mesh_count = 0
    input_triangles = 0
    exported_faces = 0
    skipped_faces = 0

    for _node_name, mesh, transform in _iter_scene_meshes(scene):
        mesh_count += 1
        input_triangles += len(mesh.faces)
        vertices = _transformed_vertices(mesh, transform, unit_scale)
        exported, skipped = _add_mesh_faces(builder, root, mesh, vertices, tolerance)
        exported_faces += exported
        skipped_faces += skipped

    if exported_faces == 0:
        raise RuntimeError("No faces were exported from the GLB scene.")

    output.parent.mkdir(parents=True, exist_ok=True)
    _write_step(root, output)

    return ReverseReport(
        source=source,
        output=output,
        mode="faceted",
        mesh_count=mesh_count,
        input_triangle_count=input_triangles,
        exported_face_count=exported_faces,
        skipped_face_count=skipped_faces,
        unit_scale=unit_scale,
        fallback_face_count=exported_faces,
    )


def glb_to_reconstructed_step(
    glb_path,
    step_path,
    *,
    unit_scale: float = 1000.0,
    tolerance: float = 1.0e-12,
    angle_tolerance_degrees: float = 1.0,
    plane_tolerance: float = 0.05,
    min_region_faces: int = 2,
    reconstruct_holes: bool = False,
    max_hole_loop_points: int | None = 48,
    reconstruct_cylinders: bool = False,
    reconstruct_spheres: bool = False,
    reconstruct_cones: bool = False,
    trim_cylinder_boundaries: bool = False,
    min_cylinder_span_degrees: float = 330.0,
    max_cylinder_free_edges: int | None = 20,
    make_solids: bool = False,
    mode_name: str = "reconstructed",
    sew: bool = True,
    sewing_tolerance: float | None = None,
) -> ReverseReport:
    """Convert a GLB mesh scene into a lighter STEP using planar reconstruction.

    Connected coplanar triangle patches are merged into single planar STEP faces.
    Non-planar or unsafe patches are still emitted as triangle faces.
    """
    source = Path(glb_path)
    output = Path(step_path)
    if not source.is_file():
        raise FileNotFoundError(f"GLB file not found: {source}")

    scene = _load_scene(source)

    builder = BRep_Builder()
    root = TopoDS_Compound()
    builder.MakeCompound(root)
    analytic_root = TopoDS_Compound()
    builder.MakeCompound(analytic_root)

    mesh_count = 0
    input_triangles = 0
    exported_faces = 0
    skipped_faces = 0
    planar_regions = 0
    fallback_faces = 0
    merged_triangles = 0
    holed_planar_regions = 0
    hole_candidates = 0
    clean_hole_candidates = 0
    hole_loops = 0
    cylinder_candidates = []
    analytic_cylinders = 0
    analytic_cylinder_triangles = 0
    boundary_trimmed_cylinders = 0
    skipped_cylinder_candidates = 0
    sphere_candidates = []
    analytic_spheres = 0
    analytic_sphere_triangles = 0
    skipped_sphere_candidates = 0
    cone_candidates = []
    analytic_cones = 0
    analytic_cone_triangles = 0
    skipped_cone_candidates = 0

    for _node_name, mesh, transform in _iter_scene_meshes(scene):
        mesh_count += 1
        input_triangles += len(mesh.faces)
        vertices = _transformed_vertices(mesh, transform, unit_scale)
        mesh_cylinder_candidates = _detect_cylinder_candidates(
            mesh,
            vertices,
            tolerance=tolerance,
        )
        cylinder_candidates.extend(mesh_cylinder_candidates)
        mesh_sphere_candidates = _detect_sphere_candidates(
            mesh,
            vertices,
            tolerance=tolerance,
        )
        sphere_candidates.extend(mesh_sphere_candidates)
        mesh_cone_candidates = _detect_cone_candidates(
            mesh,
            vertices,
            tolerance=tolerance,
        )
        cone_candidates.extend(mesh_cone_candidates)
        consumed_faces = set()
        mesh_faces = np.asarray(mesh.faces, dtype=np.int64)

        if reconstruct_cones:
            for candidate in mesh_cone_candidates:
                if not candidate["full_coverage"]:
                    skipped_cone_candidates += 1
                    continue
                cone = _cone_solid(candidate)
                if cone is None:
                    skipped_cone_candidates += 1
                    continue
                builder.Add(analytic_root, cone)
                consumed_faces.update(_cone_consumed_faces(candidate, vertices, mesh_faces, tolerance))
                analytic_cones += 1
                analytic_cone_triangles += candidate["face_count"]

        if reconstruct_spheres:
            for candidate in mesh_sphere_candidates:
                if consumed_faces.intersection(candidate["region"]):
                    skipped_sphere_candidates += 1
                    continue
                if not candidate["full_coverage"]:
                    skipped_sphere_candidates += 1
                    continue
                sphere = _sphere_solid(candidate)
                if sphere is None:
                    skipped_sphere_candidates += 1
                    continue
                builder.Add(analytic_root, sphere)
                consumed_faces.update(candidate["region"])
                analytic_spheres += 1
                analytic_sphere_triangles += candidate["face_count"]

        if reconstruct_cylinders:
            for candidate in mesh_cylinder_candidates:
                if consumed_faces.intersection(candidate["region"]):
                    skipped_cylinder_candidates += 1
                    continue
                if candidate["angular_span"] < min_cylinder_span_degrees:
                    skipped_cylinder_candidates += 1
                    continue
                if trim_cylinder_boundaries:
                    cylinder_face, used_boundary = _cylinder_face(
                        candidate,
                        vertices,
                        mesh_faces,
                        tolerance,
                    )
                else:
                    cylinder_face = _cylinder_rectangular_face(candidate)
                    used_boundary = False
                if cylinder_face is None:
                    continue
                builder.Add(root, cylinder_face)
                consumed_faces.update(candidate["region"])
                analytic_cylinders += 1
                analytic_cylinder_triangles += candidate["face_count"]
                if used_boundary:
                    boundary_trimmed_cylinders += 1

        (
            exported,
            skipped,
            regions,
            fallback,
            merged,
            holed_regions,
            mesh_hole_candidates,
            mesh_clean_hole_candidates,
            mesh_hole_loops,
        ) = _add_reconstructed_mesh_faces(
            builder,
            root,
            mesh,
            vertices,
            tolerance=tolerance,
            angle_tolerance_degrees=angle_tolerance_degrees,
            plane_tolerance=plane_tolerance,
            min_region_faces=min_region_faces,
            reconstruct_holes=reconstruct_holes,
            max_hole_loop_points=max_hole_loop_points,
            consumed_faces=consumed_faces,
        )
        exported_faces += exported
        skipped_faces += skipped
        planar_regions += regions
        fallback_faces += fallback
        merged_triangles += merged
        holed_planar_regions += holed_regions
        hole_candidates += mesh_hole_candidates
        clean_hole_candidates += mesh_clean_hole_candidates
        hole_loops += mesh_hole_loops

    reconstructed_face_count = exported_faces + analytic_cylinders
    exported_faces = reconstructed_face_count + analytic_spheres + analytic_cones

    if exported_faces == 0:
        raise RuntimeError("No faces were exported from the GLB scene.")

    if sewing_tolerance is None:
        sewing_tolerance = 0.2 if reconstruct_cylinders else 0.01

    output_shape = root
    sewn_free_edges = 0
    sewn_contiguous_edges = 0
    if sew and reconstructed_face_count > 0:
        output_shape, sewn_free_edges, sewn_contiguous_edges = _sew_shape(root, sewing_tolerance)
    if analytic_spheres or analytic_cones:
        output_shape = (
            _combine_shapes(output_shape, analytic_root)
            if reconstructed_face_count > 0
            else analytic_root
        )

    topology = _topology_diagnostics(output_shape)

    if (
        reconstruct_cylinders
        and max_cylinder_free_edges is not None
        and sewn_free_edges > max_cylinder_free_edges
    ):
        fallback_report = glb_to_reconstructed_step(
            glb_path,
            step_path,
            unit_scale=unit_scale,
            tolerance=tolerance,
            angle_tolerance_degrees=angle_tolerance_degrees,
            plane_tolerance=plane_tolerance,
            min_region_faces=min_region_faces,
            reconstruct_holes=reconstruct_holes,
            max_hole_loop_points=max_hole_loop_points,
            reconstruct_cylinders=False,
            reconstruct_spheres=reconstruct_spheres,
            reconstruct_cones=reconstruct_cones,
            make_solids=make_solids,
            mode_name=mode_name,
            sew=sew,
            sewing_tolerance=None,
        )
        return ReverseReport(
            source=fallback_report.source,
            output=fallback_report.output,
            mode=fallback_report.mode,
            mesh_count=fallback_report.mesh_count,
            input_triangle_count=fallback_report.input_triangle_count,
            exported_face_count=fallback_report.exported_face_count,
            skipped_face_count=fallback_report.skipped_face_count,
            unit_scale=fallback_report.unit_scale,
            planar_region_count=fallback_report.planar_region_count,
            fallback_face_count=fallback_report.fallback_face_count,
            merged_triangle_count=fallback_report.merged_triangle_count,
            holed_planar_region_count=fallback_report.holed_planar_region_count,
            hole_candidate_count=fallback_report.hole_candidate_count,
            clean_hole_candidate_count=fallback_report.clean_hole_candidate_count,
            hole_loop_count=fallback_report.hole_loop_count,
            sewing_enabled=fallback_report.sewing_enabled,
            sewing_tolerance=fallback_report.sewing_tolerance,
            sewn_free_edge_count=fallback_report.sewn_free_edge_count,
            sewn_contiguous_edge_count=fallback_report.sewn_contiguous_edge_count,
            cylinder_candidate_count=fallback_report.cylinder_candidate_count,
            cylinder_triangle_count=fallback_report.cylinder_triangle_count,
            cylinder_average_radius=fallback_report.cylinder_average_radius,
            skipped_cylinder_candidate_count=fallback_report.cylinder_candidate_count,
            analytic_cylinder_fallback=True,
            sphere_candidate_count=fallback_report.sphere_candidate_count,
            sphere_triangle_count=fallback_report.sphere_triangle_count,
            sphere_average_radius=fallback_report.sphere_average_radius,
            analytic_sphere_count=fallback_report.analytic_sphere_count,
            analytic_sphere_triangle_count=fallback_report.analytic_sphere_triangle_count,
            skipped_sphere_candidate_count=fallback_report.skipped_sphere_candidate_count,
            cone_candidate_count=fallback_report.cone_candidate_count,
            cone_triangle_count=fallback_report.cone_triangle_count,
            cone_average_angle_degrees=fallback_report.cone_average_angle_degrees,
            analytic_cone_count=fallback_report.analytic_cone_count,
            analytic_cone_triangle_count=fallback_report.analytic_cone_triangle_count,
            skipped_cone_candidate_count=fallback_report.skipped_cone_candidate_count,
            topology_valid=fallback_report.topology_valid,
            topology_face_count=fallback_report.topology_face_count,
            topology_shell_count=fallback_report.topology_shell_count,
            topology_closed_shell_count=fallback_report.topology_closed_shell_count,
            topology_solid_count=fallback_report.topology_solid_count,
            solid_promotion_enabled=fallback_report.solid_promotion_enabled,
            promoted_solid_count=fallback_report.promoted_solid_count,
            solid_promotion_fallback=fallback_report.solid_promotion_fallback,
        )

    promoted_solid_count = 0
    solid_promotion_fallback = False
    if make_solids:
        output_shape, promoted_solid_count, solid_promotion_fallback = _promote_closed_shells_to_solids(output_shape)
        topology = _topology_diagnostics(output_shape)

    _write_step(output_shape, output)

    return ReverseReport(
        source=source,
        output=output,
        mode=mode_name,
        mesh_count=mesh_count,
        input_triangle_count=input_triangles,
        exported_face_count=exported_faces,
        skipped_face_count=skipped_faces,
        unit_scale=unit_scale,
        planar_region_count=planar_regions,
        fallback_face_count=fallback_faces,
        merged_triangle_count=merged_triangles,
        holed_planar_region_count=holed_planar_regions,
        hole_candidate_count=hole_candidates,
        clean_hole_candidate_count=clean_hole_candidates,
        hole_loop_count=hole_loops,
        sewing_enabled=sew,
        sewing_tolerance=sewing_tolerance if sew else 0.0,
        sewn_free_edge_count=sewn_free_edges,
        sewn_contiguous_edge_count=sewn_contiguous_edges,
        cylinder_candidate_count=len(cylinder_candidates),
        cylinder_triangle_count=sum(candidate["face_count"] for candidate in cylinder_candidates),
        cylinder_average_radius=(
            sum(candidate["radius"] for candidate in cylinder_candidates) / len(cylinder_candidates)
            if cylinder_candidates
            else 0.0
        ),
        analytic_cylinder_count=analytic_cylinders,
        analytic_cylinder_triangle_count=analytic_cylinder_triangles,
        boundary_trimmed_cylinder_count=boundary_trimmed_cylinders,
        skipped_cylinder_candidate_count=skipped_cylinder_candidates,
        sphere_candidate_count=len(sphere_candidates),
        sphere_triangle_count=sum(candidate["face_count"] for candidate in sphere_candidates),
        sphere_average_radius=(
            sum(candidate["radius"] for candidate in sphere_candidates) / len(sphere_candidates)
            if sphere_candidates
            else 0.0
        ),
        analytic_sphere_count=analytic_spheres,
        analytic_sphere_triangle_count=analytic_sphere_triangles,
        skipped_sphere_candidate_count=skipped_sphere_candidates,
        cone_candidate_count=len(cone_candidates),
        cone_triangle_count=sum(candidate["face_count"] for candidate in cone_candidates),
        cone_average_angle_degrees=(
            sum(candidate["semi_angle_degrees"] for candidate in cone_candidates) / len(cone_candidates)
            if cone_candidates
            else 0.0
        ),
        analytic_cone_count=analytic_cones,
        analytic_cone_triangle_count=analytic_cone_triangles,
        skipped_cone_candidate_count=skipped_cone_candidates,
        topology_valid=topology["valid"],
        topology_face_count=topology["faces"],
        topology_shell_count=topology["shells"],
        topology_closed_shell_count=topology["closed_shells"],
        topology_solid_count=topology["solids"],
        solid_promotion_enabled=make_solids,
        promoted_solid_count=promoted_solid_count,
        solid_promotion_fallback=solid_promotion_fallback,
    )

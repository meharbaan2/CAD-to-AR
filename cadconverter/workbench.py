import json
import mimetypes
from importlib import resources
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import converter
from .holeprobe import run_hole_probe
from .reverse import glb_to_faceted_step, glb_to_reconstructed_step
from .selftest import run_reconstruction_assessment
from .validate import validate_glb


def _json_response(handler, status, payload):
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _report_payload(report):
    return {
        "ok": report.ok,
        "path": str(report.path),
        "nodes": report.node_count,
        "meshes": report.mesh_count,
        "materials": report.material_count,
        "primitives": report.primitive_count,
        "colors": report.colored_primitive_count,
        "normals": report.normal_primitive_count,
        "namedNodes": report.named_node_count,
    }


def _reverse_payload(report):
    return {
        "ok": report.ok,
        "source": str(report.source),
        "output": str(report.output),
        "mode": report.mode,
        "meshes": report.mesh_count,
        "inputTriangles": report.input_triangle_count,
        "exportedFaces": report.exported_face_count,
        "skippedFaces": report.skipped_face_count,
        "unitScale": report.unit_scale,
        "planarRegions": report.planar_region_count,
        "holedPlanarRegions": report.holed_planar_region_count,
        "holeCandidates": report.hole_candidate_count,
        "cleanHoleCandidates": report.clean_hole_candidate_count,
        "holeLoops": report.hole_loop_count,
        "mergedTriangles": report.merged_triangle_count,
        "fallbackFaces": report.fallback_face_count,
        "sewingEnabled": report.sewing_enabled,
        "sewingTolerance": report.sewing_tolerance,
        "sewnFreeEdges": report.sewn_free_edge_count,
        "sewnContiguousEdges": report.sewn_contiguous_edge_count,
        "cylinderCandidates": report.cylinder_candidate_count,
        "cylinderTriangles": report.cylinder_triangle_count,
        "cylinderAverageRadius": report.cylinder_average_radius,
        "analyticCylinders": report.analytic_cylinder_count,
        "analyticCylinderTriangles": report.analytic_cylinder_triangle_count,
        "boundaryTrimmedCylinders": report.boundary_trimmed_cylinder_count,
        "skippedCylinderCandidates": report.skipped_cylinder_candidate_count,
        "analyticCylinderFallback": report.analytic_cylinder_fallback,
        "sphereCandidates": report.sphere_candidate_count,
        "sphereTriangles": report.sphere_triangle_count,
        "sphereAverageRadius": report.sphere_average_radius,
        "analyticSpheres": report.analytic_sphere_count,
        "analyticSphereTriangles": report.analytic_sphere_triangle_count,
        "skippedSphereCandidates": report.skipped_sphere_candidate_count,
        "coneCandidates": report.cone_candidate_count,
        "coneTriangles": report.cone_triangle_count,
        "coneAverageAngleDegrees": report.cone_average_angle_degrees,
        "analyticCones": report.analytic_cone_count,
        "analyticConeTriangles": report.analytic_cone_triangle_count,
        "skippedConeCandidates": report.skipped_cone_candidate_count,
        "topologyValid": report.topology_valid,
        "topologyFaces": report.topology_face_count,
        "topologyShells": report.topology_shell_count,
        "topologyClosedShells": report.topology_closed_shell_count,
        "topologySolids": report.topology_solid_count,
        "solidPromotionEnabled": report.solid_promotion_enabled,
        "promotedSolids": report.promoted_solid_count,
        "solidPromotionFallback": report.solid_promotion_fallback,
    }


def _assessment_payload(cases):
    by_source = {}
    for case in cases:
        by_source.setdefault(case.source, []).append(case)

    baselines = {
        source: next((case for case in source_cases if case.mode == "faceted"), None)
        for source, source_cases in by_source.items()
    }

    return {
        "ok": all(case.ok for case in cases),
        "cases": [
            {
                "ok": case.ok,
                "source": str(case.source),
                "mode": case.mode,
                "quality": _assessment_quality(case, baselines.get(case.source)),
                "sizeMb": round(case.size_mb, 3),
                "readOk": case.readback.ok,
                "shells": case.readback.shells,
                "solids": case.readback.solids,
                "faces": case.report.exported_face_count,
                "inputTriangles": case.report.input_triangle_count,
                "planarRegions": case.report.planar_region_count,
                "holeCandidates": case.report.hole_candidate_count,
                "cleanHoleCandidates": case.report.clean_hole_candidate_count,
                "holeLoops": case.report.hole_loop_count,
                "freeEdges": case.report.sewn_free_edge_count,
                "analyticCylinders": case.report.analytic_cylinder_count,
                "analyticSpheres": case.report.analytic_sphere_count,
                "coneCandidates": case.report.cone_candidate_count,
                "analyticCones": case.report.analytic_cone_count,
                "analyticCylinderFallback": case.report.analytic_cylinder_fallback,
            }
            for case in cases
        ],
    }


def _hole_probe_payload(result):
    return {
        "ok": result.ok,
        "crashed": result.crashed,
        "timedOut": result.timed_out,
        "source": str(result.source),
        "output": str(result.output),
        "returncode": result.returncode,
        "returncodeHex": f"0x{result.returncode:08X}" if result.returncode is not None else None,
        "stages": list(result.stages),
        "probe": result.payload,
    }


def _assessment_quality(case, baseline=None) -> str:
    if not case.ok:
        return "Risky"

    report = case.report
    face_reduction = 0.0
    if baseline and baseline.report.exported_face_count:
        face_reduction = 1.0 - (report.exported_face_count / baseline.report.exported_face_count)

    if report.topology_solid_count > 0 and report.sewn_free_edge_count <= 5 and face_reduction >= 0.35:
        return "Excellent"
    if report.topology_solid_count > 0 and report.sewn_free_edge_count <= 20:
        return "Good"
    if report.sewn_free_edge_count == 0 and face_reduction >= 0.2:
        return "Good"
    if face_reduction >= 0.1 or report.planar_region_count > 0:
        return "Mesh-cleaned"
    return "Mesh-like"


def _is_glb(path: Path):
    return path.is_file() and path.suffix.lower() in {".glb", ".gltf"}


def _is_step(path: Path):
    return path.is_file() and path.suffix.lower() in {".stp", ".step"}


class WorkbenchHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self.root = Path(directory or ".").resolve()
        super().__init__(*args, directory=str(self.root), **kwargs)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_workbench()
        if parsed.path == "/api/glbs":
            files = sorted(path.name for path in self.root.iterdir() if _is_glb(path))
            return _json_response(self, 200, {"files": files})
        if parsed.path == "/api/steps":
            files = sorted(path.name for path in self.root.iterdir() if _is_step(path))
            return _json_response(self, 200, {"files": files})
        if parsed.path == "/api/validate":
            name = parse_qs(parsed.query).get("file", [""])[0]
            try:
                path = self._safe_file(name, {".glb", ".gltf"})
                return _json_response(self, 200, _report_payload(validate_glb(path)))
            except Exception as exc:
                return _json_response(self, 400, {"ok": False, "error": str(exc)})
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/assess-reconstruction":
            return self._assess_reconstruction()
        if parsed.path == "/api/probe-holes":
            return self._probe_holes()
        if parsed.path == "/api/step-to-glb":
            return self._step_to_glb()
        if parsed.path != "/api/glb-to-step":
            return _json_response(self, 404, {"ok": False, "error": "Unknown endpoint"})

        return self._glb_to_step()

    def _probe_holes(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            source = self._safe_file(payload.get("file", ""), {".glb", ".gltf"})
            mode = payload.get("mode", "reconstructed")
            timeout = int(payload.get("timeout", 180))
            result = run_hole_probe(source, mode=mode, timeout=timeout)
            return _json_response(self, 200, _hole_probe_payload(result))
        except Exception as exc:
            return _json_response(self, 400, {"ok": False, "error": str(exc)})

    def _assess_reconstruction(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            source = self._safe_file(payload.get("file", ""), {".glb", ".gltf"})
            modes = payload.get("modes") or ["faceted", "reconstructed", "advanced"]
            cases = run_reconstruction_assessment(
                [str(source)],
                directory=self.root,
                modes=tuple(modes),
            )
            return _json_response(self, 200, _assessment_payload(cases))
        except Exception as exc:
            return _json_response(self, 400, {"ok": False, "error": str(exc)})

    def _step_to_glb(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            source = self._safe_file(payload.get("file", ""), {".stp", ".step"})
            output_name = payload.get("output") or f"{source.stem}.glb"
            output = self._safe_output(output_name, {".glb"})

            converter.LINEAR_DEFLECTION = float(payload.get("linearDeflection", 0.15))
            converter.ANGULAR_DEFLECTION = float(payload.get("angularDeflection", 0.25))
            converter.FORCE_DOUBLE_SIDED = bool(payload.get("doubleSided", True))
            converter.export_step_hierarchy(str(source), str(output))

            validation = validate_glb(output) if bool(payload.get("validate", True)) else None
            return _json_response(
                self,
                200,
                {
                    "ok": validation.ok if validation else True,
                    "source": str(source),
                    "output": str(output),
                    "file": output.name,
                    "linearDeflection": converter.LINEAR_DEFLECTION,
                    "angularDeflection": converter.ANGULAR_DEFLECTION,
                    "doubleSided": converter.FORCE_DOUBLE_SIDED,
                    "validation": _report_payload(validation) if validation else None,
                },
            )
        except Exception as exc:
            return _json_response(self, 400, {"ok": False, "error": str(exc)})

    def _glb_to_step(self):
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            source = self._safe_file(payload.get("file", ""), {".glb", ".gltf"})
            mode = payload.get("mode", "reconstructed")
            output_name = payload.get("output") or f"{source.stem}_{mode}.step"
            output = self._safe_output(output_name, {".stp", ".step"})
            if mode == "faceted":
                report = glb_to_faceted_step(source, output)
            elif mode in {"advanced", "reconstructed"}:
                use_advanced = mode == "advanced"
                report = glb_to_reconstructed_step(
                    source,
                    output,
                    reconstruct_holes=bool(payload.get("reconstructHoles")),
                    max_hole_loop_points=int(payload.get("maxHoleLoopPoints", 48)),
                    reconstruct_cylinders=use_advanced or bool(payload.get("reconstructCylinders")),
                    reconstruct_spheres=use_advanced or bool(payload.get("reconstructSpheres")),
                    reconstruct_cones=use_advanced or bool(payload.get("reconstructCones")),
                    min_cylinder_span_degrees=330.0,
                    max_cylinder_free_edges=20,
                    make_solids=use_advanced or bool(payload.get("makeSolids")),
                    mode_name=mode,
                    trim_cylinder_boundaries=False,
                )
            else:
                raise ValueError(f"Unsupported conversion mode: {mode}")
            return _json_response(self, 200, _reverse_payload(report))
        except Exception as exc:
            return _json_response(self, 400, {"ok": False, "error": str(exc)})

    def guess_type(self, path):
        if path.endswith(".glb"):
            return "model/gltf-binary"
        if path.endswith(".gltf"):
            return "model/gltf+json"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def _serve_workbench(self):
        workbench_path = resources.files("cadconverter") / "workbench.html"
        if not workbench_path.is_file():
            workbench_path = self.root / "workbench.html"
        if not workbench_path.is_file():
            package_root = resources.files("cadconverter").parent
            workbench_path = Path(package_root) / "workbench.html"
        try:
            body = workbench_path.read_bytes()
        except OSError as exc:
            return _json_response(self, 404, {"ok": False, "error": str(exc)})

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _safe_file(self, name, suffixes):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("File must be inside the workbench folder")
        if path.suffix.lower() not in suffixes:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        if not path.is_file():
            raise FileNotFoundError(path.name)
        return path

    def _safe_output(self, name, suffixes):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Output must be inside the workbench folder")
        if path.suffix.lower() not in suffixes:
            suffix_text = " or ".join(sorted(suffixes))
            raise ValueError(f"Output must be {suffix_text}")
        return path


def run_workbench(directory=".", host="127.0.0.1", port=8765):
    root = Path(directory).resolve()

    class Handler(WorkbenchHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Workbench: http://{host}:{port}/")
    print(f"Folder: {root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWorkbench stopped.")
    finally:
        server.server_close()

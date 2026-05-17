import argparse
from pathlib import Path

from . import converter
from .holeprobe import run_hole_probe
from .reverse import glb_to_faceted_step, glb_to_reconstructed_step
from .selftest import run_primitive_selftest, run_reconstruction_assessment, run_selftest
from .validate import validate_glb
from .workbench import run_workbench


def _step_files(path: Path):
    if path.is_file():
        if path.suffix.lower() not in {".stp", ".step"}:
            raise ValueError(f"Input is not a STEP file: {path}")
        return [path]

    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".stp", ".step"}
    )


def _output_path(step_path: Path, args) -> Path:
    if args.output:
        output = Path(args.output)
        if len(args.inputs) > 1 or (len(args.inputs) == 1 and Path(args.inputs[0]).is_dir()):
            raise ValueError("--output can only be used with one STEP file")
        return output

    out_dir = Path(args.out_dir) if args.out_dir else step_path.parent
    return out_dir / f"{step_path.stem}{args.suffix}.glb"


def _print_validation(report):
    status = "OK" if report.ok else "WARN"
    print(f"{status}: {report.path}")
    print(
        "  "
        f"nodes={report.node_count}, "
        f"meshes={report.mesh_count}, "
        f"materials={report.material_count}, "
        f"primitives={report.primitive_count}"
    )
    print(
        "  "
        f"colors={report.colored_primitive_count}/{report.primitive_count}, "
        f"normals={report.normal_primitive_count}/{report.primitive_count}, "
        f"named_nodes={report.named_node_count}/{report.node_count}"
    )


def convert_command(args) -> int:
    converter.LINEAR_DEFLECTION = args.linear_deflection
    converter.ANGULAR_DEFLECTION = args.angular_deflection
    converter.FORCE_DOUBLE_SIDED = not args.single_sided

    step_paths = []
    for raw_input in args.inputs:
        step_paths.extend(_step_files(Path(raw_input)))

    if not step_paths:
        print("No STEP files found.")
        return 1

    for step_path in step_paths:
        glb_path = _output_path(step_path, args)
        glb_path.parent.mkdir(parents=True, exist_ok=True)

        if glb_path.exists() and not args.overwrite:
            print(f"Skipping existing output: {glb_path}")
            continue

        print(f"Converting: {step_path}")
        converter.export_step_hierarchy(str(step_path), str(glb_path))
        print(f"Exported: {glb_path}")

        if args.validate:
            _print_validation(validate_glb(glb_path))

    return 0


def validate_command(args) -> int:
    exit_code = 0
    for glb_path in args.glbs:
        report = validate_glb(glb_path)
        _print_validation(report)
        if not report.ok:
            exit_code = 1
    return exit_code


def glb_to_step_command(args) -> int:
    glb_path = Path(args.input)
    step_path = Path(args.output) if args.output else glb_path.with_suffix(".step")
    max_cylinder_free_edges = getattr(args, "max_cylinder_free_edges", None)
    if max_cylinder_free_edges is not None and max_cylinder_free_edges < 0:
        max_cylinder_free_edges = None

    if getattr(args, "trim_cylinder_boundaries", False):
        raise ValueError(
            "--trim-cylinder-boundaries is disabled because it can create STEP files "
            "that crash OpenCascade's reader on some models"
        )

    if step_path.exists() and not args.overwrite:
        raise ValueError(f"Output already exists, use --overwrite: {step_path}")
    max_hole_loop_points = getattr(args, "max_hole_loop_points", None)
    if max_hole_loop_points is not None and max_hole_loop_points < 0:
        max_hole_loop_points = None

    if args.mode == "faceted":
        report = glb_to_faceted_step(
            glb_path,
            step_path,
            unit_scale=args.unit_scale,
            tolerance=args.tolerance,
        )
    else:
        use_advanced = args.mode == "advanced"
        report = glb_to_reconstructed_step(
            glb_path,
            step_path,
            unit_scale=args.unit_scale,
            tolerance=args.tolerance,
            angle_tolerance_degrees=args.angle_tolerance,
            plane_tolerance=args.plane_tolerance,
            min_region_faces=args.min_region_faces,
            reconstruct_holes=args.reconstruct_holes,
            max_hole_loop_points=max_hole_loop_points,
            reconstruct_cylinders=args.reconstruct_cylinders or use_advanced,
            reconstruct_spheres=args.reconstruct_spheres or use_advanced,
            reconstruct_cones=args.reconstruct_cones or use_advanced,
            min_cylinder_span_degrees=args.min_cylinder_span,
            max_cylinder_free_edges=max_cylinder_free_edges,
            trim_cylinder_boundaries=False,
            make_solids=args.make_solids or use_advanced,
            mode_name=args.mode,
            sew=not args.no_sew,
            sewing_tolerance=args.sew_tolerance,
        )

    print(f"Exported {report.mode} STEP: {report.output}")
    print(
        "  "
        f"meshes={report.mesh_count}, "
        f"triangles={report.input_triangle_count}, "
        f"faces={report.exported_face_count}, "
        f"skipped={report.skipped_face_count}, "
        f"unit_scale={report.unit_scale:g}"
    )
    if report.mode in {"reconstructed", "advanced"}:
        print(
            "  "
            f"planar_regions={report.planar_region_count}, "
            f"holed_regions={report.holed_planar_region_count}, "
            f"hole_candidates={report.hole_candidate_count}, "
            f"clean_hole_candidates={report.clean_hole_candidate_count}, "
            f"hole_loops={report.hole_loop_count}, "
            f"merged_triangles={report.merged_triangle_count}, "
            f"fallback_faces={report.fallback_face_count}"
        )
        print(
            "  "
            f"sewn={report.sewing_enabled}, "
            f"sew_tolerance={report.sewing_tolerance:g}, "
            f"free_edges={report.sewn_free_edge_count}, "
            f"contiguous_edges={report.sewn_contiguous_edge_count}"
        )
        print(
            "  "
            f"cylinder_candidates={report.cylinder_candidate_count}, "
            f"cylinder_triangles={report.cylinder_triangle_count}, "
            f"avg_cylinder_radius={report.cylinder_average_radius:g}, "
            f"analytic_cylinders={report.analytic_cylinder_count}, "
            f"analytic_cylinder_triangles={report.analytic_cylinder_triangle_count}, "
            f"skipped_cylinder_candidates={report.skipped_cylinder_candidate_count}, "
            f"boundary_trimmed_cylinders={report.boundary_trimmed_cylinder_count}, "
            f"analytic_cylinder_fallback={report.analytic_cylinder_fallback}"
        )
        print(
            "  "
            f"sphere_candidates={report.sphere_candidate_count}, "
            f"sphere_triangles={report.sphere_triangle_count}, "
            f"avg_sphere_radius={report.sphere_average_radius:g}, "
            f"analytic_spheres={report.analytic_sphere_count}, "
            f"analytic_sphere_triangles={report.analytic_sphere_triangle_count}, "
            f"skipped_spheres={report.skipped_sphere_candidate_count}"
        )
        print(
            "  "
            f"cone_candidates={report.cone_candidate_count}, "
            f"cone_triangles={report.cone_triangle_count}, "
            f"avg_cone_angle={report.cone_average_angle_degrees:g}, "
            f"analytic_cones={report.analytic_cone_count}, "
            f"analytic_cone_triangles={report.analytic_cone_triangle_count}, "
            f"skipped_cones={report.skipped_cone_candidate_count}"
        )
        print(
            "  "
            f"topology_valid={report.topology_valid}, "
            f"topology_faces={report.topology_face_count}, "
            f"shells={report.topology_shell_count}, "
            f"closed_shells={report.topology_closed_shell_count}, "
            f"solids={report.topology_solid_count}"
        )
        print(
            "  "
            f"solid_promotion={report.solid_promotion_enabled}, "
            f"promoted_solids={report.promoted_solid_count}, "
            f"solid_promotion_fallback={report.solid_promotion_fallback}"
        )
    return 0


def workbench_command(args) -> int:
    run_workbench(directory=args.directory, host=args.host, port=args.port)
    return 0


def selftest_command(args) -> int:
    cases = run_selftest(
        args.inputs,
        directory=args.directory,
        modes=tuple(args.modes),
        keep_outputs=args.keep_outputs,
        output_dir=args.output_dir,
    )

    failed = 0
    for case in cases:
        status = "OK" if case.ok else "FAIL"
        print(f"{status}: {case.source.name} [{case.mode}]")
        print(f"  output={case.output}")
        print(
            "  "
            f"read_ok={case.readback.ok}, "
            f"roots={case.readback.roots}, "
            f"shapes={case.readback.shapes}, "
            f"shells={case.readback.shells}, "
            f"solids={case.readback.solids}"
        )
        print(
            "  "
            f"free_edges={case.free_edges}, "
            f"reported_solids={case.solids}, "
            f"analytic_fallback={case.fallback}"
        )
        if not case.ok:
            failed += 1

    if failed:
        print(f"Self-test failed: {failed}/{len(cases)} cases failed")
        return 1

    print(f"Self-test passed: {len(cases)} cases")
    return 0


def primitive_selftest_command(args) -> int:
    cases = run_primitive_selftest(
        keep_outputs=args.keep_outputs,
        output_dir=args.output_dir,
    )

    failed = 0
    for case in cases:
        status = "OK" if case.ok else "FAIL"
        report = case.report
        print(f"{status}: {case.name}")
        print(f"  output={case.output}")
        print(
            "  "
            f"read_ok={case.readback.ok}, "
            f"shells={case.readback.shells}, "
            f"solids={case.readback.solids}, "
            f"faces={report.exported_face_count}"
        )
        print(
            "  "
            f"sphere_candidates={report.sphere_candidate_count}, "
            f"analytic_spheres={report.analytic_sphere_count}, "
            f"cone_candidates={report.cone_candidate_count}, "
            f"analytic_cones={report.analytic_cone_count}, "
            f"clean_holes={report.clean_hole_candidate_count}, "
            f"holed_regions={report.holed_planar_region_count}"
        )
        if not case.ok:
            failed += 1

    if failed:
        print(f"Primitive self-test failed: {failed}/{len(cases)} cases failed")
        return 1

    print(f"Primitive self-test passed: {len(cases)} cases")
    return 0


def probe_holes_command(args) -> int:
    result = run_hole_probe(
        args.input,
        output=args.output,
        mode=args.mode,
        timeout=args.timeout,
        keep_output=args.keep_output,
        sew=not args.no_sew,
        readback=not args.skip_readback,
        max_hole_loop_points=args.max_hole_loop_points if args.max_hole_loop_points >= 0 else None,
    )
    payload = result.payload

    if result.timed_out:
        print(f"FAIL: hole probe timed out after {args.timeout}s")
        print(f"  source={result.source}")
        return 1

    if result.crashed:
        print("FAIL: isolated hole probe process crashed")
        print(f"  source={result.source}")
        print(f"  returncode={result.returncode}")
        print(f"  returncode_hex=0x{result.returncode:08X}")
        print(f"  stages={','.join(result.stages) if result.stages else 'none'}")
        if result.stderr.strip():
            print(f"  stderr={result.stderr.strip()}")
        return 1

    status = "OK" if result.ok else "FAIL"
    print(f"{status}: hole probe")
    print(f"  source={result.source}")
    print(f"  output={result.output}")
    print(f"  returncode={result.returncode}")
    print(f"  stages={','.join(result.stages) if result.stages else 'none'}")
    if payload:
        print(
            "  "
            f"read_ok={payload.get('readOk')}, "
            f"faces={payload.get('faces')}, "
            f"shells={payload.get('shells')}, "
            f"solids={payload.get('solids')}, "
            f"free_edges={payload.get('freeEdges')}"
        )
        print(
            "  "
            f"hole_candidates={payload.get('holeCandidates')}, "
            f"clean_holes={payload.get('cleanHoleCandidates')}, "
            f"hole_loops={payload.get('holeLoops')}, "
            f"holed_regions={payload.get('holedPlanarRegions')}"
        )
    return 0 if result.ok else 1


def assess_reconstruction_command(args) -> int:
    cases = run_reconstruction_assessment(
        args.inputs,
        directory=args.directory,
        modes=tuple(args.modes),
        keep_outputs=args.keep_outputs,
        output_dir=args.output_dir,
    )

    by_source = {}
    for case in cases:
        by_source.setdefault(case.source, []).append(case)

    failed = 0
    for source, source_cases in by_source.items():
        print(f"Source: {source.name}")
        baseline = next((case for case in source_cases if case.mode == "faceted"), None)
        baseline_faces = baseline.report.exported_face_count if baseline else None
        baseline_size = baseline.output_size if baseline else None

        for case in source_cases:
            report = case.report
            status = "OK" if case.ok else "FAIL"
            face_change = "n/a"
            size_change = "n/a"
            if baseline_faces:
                reduction = 100.0 * (1.0 - (report.exported_face_count / baseline_faces))
                face_change = f"{reduction:.1f}% fewer faces"
            if baseline_size:
                reduction = 100.0 * (1.0 - (case.output_size / baseline_size))
                size_change = f"{reduction:.1f}% smaller"
            quality = _reconstruction_quality(case, baseline)

            print(
                "  "
                f"{status} {case.mode}: "
                f"quality={quality}, "
                f"size={case.size_mb:.2f} MB ({size_change}), "
                f"faces={report.exported_face_count} ({face_change}), "
                f"planar_regions={report.planar_region_count}, "
                f"hole_candidates={report.hole_candidate_count}, "
                f"clean_holes={report.clean_hole_candidate_count}, "
                f"free_edges={report.sewn_free_edge_count}, "
                f"shells={case.readback.shells}, "
                f"solids={case.readback.solids}, "
                f"analytic_cylinders={report.analytic_cylinder_count}, "
                f"analytic_spheres={report.analytic_sphere_count}, "
                f"cone_candidates={report.cone_candidate_count}, "
                f"analytic_cones={report.analytic_cone_count}, "
                f"fallback={report.analytic_cylinder_fallback}"
            )
            if args.keep_outputs:
                print(f"    output={case.output}")
            if not case.ok:
                failed += 1

    if failed:
        print(f"Assessment failed: {failed}/{len(cases)} cases failed")
        return 1

    print(f"Assessment passed: {len(cases)} cases")
    return 0


def _reconstruction_quality(case, baseline=None) -> str:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cadconverter",
        description="Convert CAD assemblies between STEP and GLB workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert = subparsers.add_parser("convert", help="Convert STEP files or folders to GLB")
    convert.add_argument("inputs", nargs="+", help="STEP file(s) or folder(s) containing STEP files")
    convert.add_argument("-o", "--output", help="Output GLB path for a single input STEP file")
    convert.add_argument("--out-dir", help="Output directory for generated GLB files")
    convert.add_argument("--suffix", default="_fixed", help="Suffix for generated files")
    convert.add_argument("--overwrite", action="store_true", help="Overwrite existing GLB outputs")
    convert.add_argument("--validate", action="store_true", help="Validate each GLB after export")
    convert.add_argument("--linear-deflection", type=float, default=converter.LINEAR_DEFLECTION)
    convert.add_argument("--angular-deflection", type=float, default=converter.ANGULAR_DEFLECTION)
    convert.add_argument("--single-sided", action="store_true", help="Disable forced double-sided materials")
    convert.set_defaults(func=convert_command)

    validate = subparsers.add_parser("validate", help="Inspect generated GLB files")
    validate.add_argument("glbs", nargs="+", help="GLB file(s) to validate")
    validate.set_defaults(func=validate_command)

    reverse = subparsers.add_parser("glb-to-step", help="Convert GLB meshes to STEP")
    reverse.add_argument("input", help="Input GLB/GLTF file")
    reverse.add_argument("-o", "--output", help="Output STEP path")
    reverse.add_argument("--overwrite", action="store_true", help="Overwrite existing STEP output")
    reverse.add_argument(
        "--mode",
        choices=("advanced", "reconstructed", "faceted"),
        default="reconstructed",
        help="STEP export mode; advanced uses guarded cylinders and solids, reconstructed merges coplanar patches, faceted preserves every triangle",
    )
    reverse.add_argument(
        "--unit-scale",
        type=float,
        default=1000.0,
        help="Scale GLB units before STEP export; default converts meters to millimeters",
    )
    reverse.add_argument(
        "--tolerance",
        type=float,
        default=1.0e-12,
        help="Degenerate triangle area tolerance",
    )
    reverse.add_argument(
        "--angle-tolerance",
        type=float,
        default=1.0,
        help="Max normal angle in degrees for planar reconstruction grouping",
    )
    reverse.add_argument(
        "--plane-tolerance",
        type=float,
        default=0.05,
        help="Max plane offset difference for planar reconstruction after unit scaling",
    )
    reverse.add_argument(
        "--min-region-faces",
        type=int,
        default=2,
        help="Minimum connected coplanar triangle count before merging a reconstructed face",
    )
    reverse.add_argument(
        "--reconstruct-holes",
        action="store_true",
        help="Experimental: reconstruct planar faces with inner hole loops; disabled by default for stability",
    )
    reverse.add_argument(
        "--max-hole-loop-points",
        type=int,
        default=48,
        help="Skip holed faces whose inner loops have more points than this; use -1 to disable the guard",
    )
    reverse.add_argument(
        "--reconstruct-cylinders",
        action="store_true",
        help="Experimental: replace detected cylindrical mesh patches with analytic cylinder faces",
    )
    reverse.add_argument(
        "--reconstruct-spheres",
        action="store_true",
        help="Experimental: replace complete detected spherical meshes with analytic sphere solids",
    )
    reverse.add_argument(
        "--reconstruct-cones",
        action="store_true",
        help="Experimental: replace complete detected conical meshes with analytic cone solids",
    )
    reverse.add_argument(
        "--min-cylinder-span",
        type=float,
        default=330.0,
        help="Minimum angular span in degrees before a detected cylinder is exported analytically",
    )
    reverse.add_argument(
        "--max-cylinder-free-edges",
        type=int,
        default=20,
        help="Fallback to stable planar reconstruction if analytic cylinders leave more free edges than this; use -1 to disable",
    )
    reverse.add_argument(
        "--make-solids",
        action="store_true",
        help="Experimental: promote sewn closed shells into STEP solids when every shell is closed",
    )
    reverse.add_argument(
        "--trim-cylinder-boundaries",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    reverse.add_argument(
        "--no-sew",
        action="store_true",
        help="Disable sewing reconstructed faces into connected shells",
    )
    reverse.add_argument(
        "--sew-tolerance",
        type=float,
        default=None,
        help="Tolerance used when sewing reconstructed faces before STEP export; defaults to 0.01, or 0.2 with analytic cylinders",
    )
    reverse.set_defaults(func=glb_to_step_command)

    workbench = subparsers.add_parser("workbench", help="Start the local browser workbench")
    workbench.add_argument("--directory", default=".", help="Folder containing GLB files")
    workbench.add_argument("--host", default="127.0.0.1", help="Host for the local workbench server")
    workbench.add_argument("--port", type=int, default=8765, help="Port for the local workbench server")
    workbench.set_defaults(func=workbench_command)

    selftest = subparsers.add_parser("selftest", help="Run reverse-conversion smoke tests")
    selftest.add_argument("inputs", nargs="*", help="Optional GLB/GLTF files or folders to test")
    selftest.add_argument("--directory", default=".", help="Default folder for bundled/sample GLB discovery")
    selftest.add_argument(
        "--modes",
        nargs="+",
        choices=("advanced", "reconstructed", "faceted"),
        default=("advanced", "reconstructed"),
        help="Reverse modes to test",
    )
    selftest.add_argument("--keep-outputs", action="store_true", help="Keep generated self-test STEP files")
    selftest.add_argument("--output-dir", help="Folder for kept self-test outputs")
    selftest.set_defaults(func=selftest_command)

    primitive_selftest = subparsers.add_parser(
        "primitive-selftest",
        help="Run synthetic sphere/cone/frustum analytic reconstruction tests",
    )
    primitive_selftest.add_argument("--keep-outputs", action="store_true", help="Keep generated primitive STEP files")
    primitive_selftest.add_argument("--output-dir", help="Folder for kept primitive test outputs")
    primitive_selftest.set_defaults(func=primitive_selftest_command)

    probe_holes = subparsers.add_parser(
        "probe-holes",
        help="Safely probe experimental planar hole reconstruction in an isolated process",
    )
    probe_holes.add_argument("input", help="Input GLB/GLTF file")
    probe_holes.add_argument("-o", "--output", help="Optional STEP output path to keep")
    probe_holes.add_argument(
        "--mode",
        choices=("reconstructed", "advanced"),
        default="reconstructed",
        help="Base reverse mode used during the isolated probe",
    )
    probe_holes.add_argument("--timeout", type=int, default=180, help="Probe timeout in seconds")
    probe_holes.add_argument(
        "--max-hole-loop-points",
        type=int,
        default=48,
        help="Skip holed faces whose inner loops have more points than this; use -1 to disable",
    )
    probe_holes.add_argument("--no-sew", action="store_true", help="Disable sewing during the isolated probe")
    probe_holes.add_argument(
        "--skip-readback",
        action="store_true",
        help="Skip STEP read-back so the probe only tests conversion/write",
    )
    probe_holes.add_argument(
        "--keep-output",
        action="store_true",
        help="Keep a probe STEP next to the input when --output is not set",
    )
    probe_holes.set_defaults(func=probe_holes_command)

    assess = subparsers.add_parser(
        "assess-reconstruction",
        help="Compare faceted, reconstructed, and advanced GLB-to-STEP outputs",
    )
    assess.add_argument("inputs", nargs="*", help="Optional GLB/GLTF files or folders to assess")
    assess.add_argument("--directory", default=".", help="Default folder for bundled/sample GLB discovery")
    assess.add_argument(
        "--modes",
        nargs="+",
        choices=("advanced", "reconstructed", "faceted"),
        default=("faceted", "reconstructed", "advanced"),
        help="Reverse modes to compare",
    )
    assess.add_argument("--keep-outputs", action="store_true", help="Keep generated assessment STEP files")
    assess.add_argument("--output-dir", help="Folder for kept assessment outputs")
    assess.set_defaults(func=assess_reconstruction_command)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

import argparse
from pathlib import Path

from . import converter
from .validate import validate_glb


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cadconverter",
        description="Convert STEP assemblies to hierarchy-preserving GLB files.",
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

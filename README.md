# CADLite - CAD to GLB Converter and Web/AR Viewer

Convert **STEP (.stp / .step)** CAD files into **GLB** for browser and AR viewing.
The converter uses `pythonocc-core` to read STEP assemblies through XCAF/`STEPCAFControl_Reader` and exports a glTF scene that preserves assembly hierarchy, part instances, and transforms.
The package also includes a reverse path that converts GLB meshes back into STEP, with planar reconstruction enabled by default and guarded analytic primitive recovery in advanced mode.

Built with `pythonocc-core`, `trimesh`, `pygltflib`, `<model-viewer>`, and `three.js`.

## Features

- Converts `.stp` / `.step` files to `.glb`
- Preserves assembly hierarchy instead of collapsing everything into one mesh
- Exports individual part/component meshes with their own transforms
- Handles nested assemblies and repeated subassemblies
- Preserves STEP part colors consistently in the exported GLB
- Creates distinct glTF materials per part type when STEP visual material data is missing
- Forces double-sided materials to reduce missing-face issues in thin CAD geometry
- Converts GLB/GLTF mesh scenes to STEP using planar reconstruction, with faceted fallback and guarded analytic primitive recovery
- Processes all STEP files in the folder automatically
- Includes browser viewers for quick inspection

## What This Fixes

This version is meant for STEP assemblies, not only single solid parts.

Older conversion approaches often:

- read the file as one flattened root shape
- bake all transforms directly into geometry
- lose nested subassemblies and repeated component structure

This converter keeps the STEP assembly tree and writes it out as GLB nodes, so repeated parts and nested subassemblies stay separate in the exported scene.

## Requirements

- Windows, macOS, or Linux
- [Miniconda or Anaconda](https://www.anaconda.com/download)
- Python 3.10 recommended

## Setup

1. Install Miniconda or Anaconda.
2. Open Anaconda Prompt or a shell where Conda is available.
3. Create and activate the project environment:

```bash
conda create -n cad2ar python=3.10
conda activate cad2ar
```

4. Install dependencies:

```bash
conda install -c conda-forge pythonocc-core
pip install -e .
```

`pythonocc-core` is installed with Conda because it is distributed most reliably through `conda-forge`. The editable package install provides the `cadconverter` command and installs the Python dependencies listed in `pyproject.toml`.

## Command Line

Convert one STEP file:

```bash
conda activate cad2ar
cadconverter convert "model.step" -o "model.glb" --overwrite --validate
```

Convert every STEP file in a folder:

```bash
cadconverter convert . --overwrite --validate
```

Inspect an existing GLB:

```bash
cadconverter validate "model.glb"
```

Run reverse-conversion smoke tests:

```bash
cadconverter selftest --directory .
```

The self-test converts available sample GLBs through `advanced` and `reconstructed` modes in a temporary folder, reads the generated STEP files back through OpenCascade, and reports shell/solid topology counts.

Run synthetic analytic primitive tests:

```bash
cadconverter primitive-selftest
```

The primitive self-test generates temporary sphere, cone, frustum, and holed-plane GLBs, converts them through the advanced reverse path, and verifies analytic STEP solids plus a safe planar inner-loop export on read-back. This is the fastest check for primitive and hole/cutout reconstruction regressions.

Compare reconstruction quality across reverse modes:

```bash
cadconverter assess-reconstruction --directory .
```

The assessment command runs faceted, reconstructed, and advanced exports in a temporary folder, reads each STEP back through OpenCascade, and reports quality grade, output size, exported face count, planar hole candidates, free edges, shells, solids, analytic cylinders, analytic spheres, cone candidates, and fallback status. Use it before chasing deeper reconstruction so you can see whether `advanced` is already good enough for the model.

Safely probe experimental real-model hole export:

```bash
cadconverter probe-holes "model.glb"
```

This runs `--reconstruct-holes` in an isolated child Python process and reads the result back if the child survives. If OpenCascade hits a native access violation, the main CLI/workbench should remain alive and report the child crash, commonly as `0xC0000005` on Windows.
The probe uses the same guarded default as the converter: holed planar faces with inner loops over `48` points are skipped instead of being handed to OpenCascade.

Convert a GLB mesh scene back to STEP:

```bash
cadconverter glb-to-step "model.glb" -o "model.step" --mode advanced --overwrite
```

The recommended reverse mode is `advanced`: it uses planar reconstruction, sewing, guarded analytic cylinder recovery, complete analytic sphere/cone recovery, cone diagnostics, and closed-shell solid promotion. If analytic cylinders leave too many free edges, it automatically falls back to the safer planar reconstruction for that model.

The conservative `reconstructed` mode uses planar reconstruction only: connected coplanar triangle patches are merged into larger planar STEP faces, then OpenCascade sews the faces into connected shells before STEP export. This reduces file size and makes flat CAD-like areas cleaner than a pure triangle export.

If you need an exact triangle-preserving fallback, use:

```bash
cadconverter glb-to-step "model.glb" -o "model_faceted.step" --mode faceted --overwrite
```

The reverse path still does not recover full CAD design intent such as fillets, sketches, constraints, feature history, or NURBS. Those are later reconstruction stages.
It reports curved and cutout-like mesh regions during reconstructed export. Guarded analytic cylinders, complete spheres, complete cones/frustums, and simple planar holes are available behind explicit flags or through `advanced` mode where safe.

Start the one-click local workbench from any folder containing STEP/STP or GLB files:

```bash
cadconverter workbench --directory .
```

Then open `http://127.0.0.1:8765/`. The installed package includes the workbench page, so this command works even when you are not running from the source repository.

From this repository on Windows, the helper batch file does the same thing:

```bash
start_workbench.bat
```

The workbench has two panels:

- `STEP -> GLB` exports browser/AR-ready GLB files with hierarchy, part names, colors/materials, mesh-quality controls, double-sided material control, and optional GLB validation.
- `GLB -> STEP` validates a GLB and exports advanced, reconstructed, or faceted STEP from the selected model.

By default, folder conversion exports one GLB per STEP file using the pattern:

```text
<original-name>_fixed.glb
```

Example:

```text
transormer assembled.stp -> transormer assembled_fixed.glb
```

While converting, the script prints a short summary such as:

- number of assemblies found
- number of part instances exported
- number of unique part definitions

The old script entry point still works and is equivalent to converting the current folder:

```bash
python convert_step_to_glb.py
```

Useful CLI options:

- `--out-dir <folder>` writes generated GLB files to another folder
- `--suffix <text>` changes the generated filename suffix
- `--linear-deflection <value>` controls mesh resolution
- `--angular-deflection <value>` controls angular mesh quality
- `--single-sided` disables forced double-sided rendering
- `--validate` checks colors, normals, materials, nodes, and mesh counts after export
- `selftest --directory <folder>` runs reverse-conversion smoke tests against GLB files in a folder
- `primitive-selftest` verifies analytic sphere, cone, frustum, and holed-plane recovery with generated temporary GLBs
- `assess-reconstruction --directory <folder>` compares faceted, reconstructed, and advanced GLB-to-STEP quality using read-back and topology metrics
- `probe-holes <model.glb>` runs experimental real-model hole reconstruction in an isolated process so native crashes are contained and reported
- `workbench --directory <folder>` starts the bundled local browser workbench for one-click STEP-to-GLB export, GLB validation, and GLB-to-STEP export
- `glb-to-step --mode advanced` runs the guarded best-available reverse path: planar reconstruction, sewing, analytic cylinders when safe, and solid promotion
- `glb-to-step --mode reconstructed` merges connected coplanar triangles into larger planar STEP faces; this is the conservative default
- `glb-to-step --mode faceted` exports one STEP face per mesh triangle
- `glb-to-step --unit-scale <value>` controls mesh scaling before STEP export; the default converts glTF meters to STEP millimeters
- `glb-to-step --angle-tolerance <degrees>` controls how similar triangle normals must be before planar merging
- `glb-to-step --plane-tolerance <value>` controls how close triangles must be to the same plane after unit scaling
- `glb-to-step --reconstruct-holes` enables experimental planar inner-loop reconstruction; keep it off unless testing because some OpenCascade builds can crash on complex hole wires
- `glb-to-step --max-hole-loop-points <count>` skips complex hole loops before OpenCascade face construction; default is `48`, use `-1` only inside isolated probing
- `glb-to-step --reconstruct-cylinders` experimentally replaces detected curved mesh patches with analytic cylindrical STEP faces when they pass the free-edge safety guard
- `glb-to-step --reconstruct-spheres` experimentally replaces complete detected spherical meshes with analytic STEP sphere solids
- `glb-to-step --reconstruct-cones` experimentally replaces complete detected conical/frustum meshes with analytic STEP cone solids
- `glb-to-step --min-cylinder-span <degrees>` controls how complete a cylinder must be before analytic export; default is `330`
- `glb-to-step --max-cylinder-free-edges <count>` falls back to stable planar reconstruction if analytic cylinders leave too many free edges; default is `20`, use `-1` to disable the guard
- `glb-to-step --make-solids` experimentally promotes sewn closed shells into STEP solids; shells that cannot make valid solids are kept as shell geometry
- `glb-to-step --no-sew` disables sewing reconstructed faces into connected shells
- `glb-to-step --sew-tolerance <value>` controls OpenCascade sewing tolerance; default is `0.01`, or `0.2` when experimental analytic cylinders are enabled

## View In Browser

Start a local web server in the project folder:

```bash
python -m http.server 8000
```

Then open one of these pages:

- `http://localhost:8000/viewer_parts.html`
  Best for checking preserved part/component separation.
- `http://localhost:8000/viewer.html`
  Simple viewer with model selection and AR support through `<model-viewer>`.

In either viewer, select the generated `_fixed.glb` file from the dropdown.

## Expected Output

For an assembly STEP file, the exported GLB should contain:

- separate mesh nodes for leaf parts
- parent nodes for assemblies and subassemblies
- transforms stored as scene-node transforms instead of one merged mesh
- part colors carried into the GLB wherever the STEP file provides them
- distinct glTF material response for metals, insulators, covers, and core-like parts

If the source STEP file contains names, those names are reused for exported nodes where possible.

For GLB to STEP, the current output should contain:

- one STEP compound built from the GLB scene meshes
- reconstructed planar faces for connected coplanar triangle patches
- sewn shell-style topology for reconstructed output when sewing succeeds
- fallback triangle faces for patches that cannot be safely merged
- cylinder-candidate diagnostics for curved mesh regions
- optional experimental analytic cylinder faces when requested
- optional complete analytic sphere solids when requested
- optional complete analytic cone/frustum solids when requested
- cone-candidate diagnostics when a cone-like region is detected but fails the full-coverage export guard
- planar hole/cutout diagnostics, including clean candidate counts, without enabling risky hole export by default
- guarded planar hole export when explicitly requested; complex inner loops are skipped by default to avoid known OpenCascade access violations
- automatic fallback from analytic cylinders to stable planar reconstruction when sewing leaves too many free edges
- optional experimental solid promotion for sewn shells that can become valid solids
- `advanced` mode combines the guarded cylinder and solid promotion steps automatically
- safe fallback around complex hole/cutout boundaries by default
- transformed mesh positions baked into STEP coordinates
- a conversion report with mesh, triangle, reconstructed face, planar-region, sewing, fallback-face, and skipped-face counts

## Notes

- STEP files exported as AP214 or AP242 are supported
- Units are assumed to be millimeters and are converted to meters
- Hierarchy is read through XCAF/STEPCAF metadata, not just a flattened root shape
- Nested subassemblies are exported as GLB nodes
- STEP colors are read from XCAF color assignments and written into the GLB as mesh colors
- STEP visual materials are used when available; otherwise the exporter infers practical PBR materials from part names, colors, and available metadata
- Double-sided rendering helps with thin walls and one-sided CAD surfaces
- GLB to STEP currently supports stable planar reconstruction with sewing, cylinder-candidate detection, guarded experimental analytic cylinder export, complete sphere solid recovery, complete cone/frustum solid recovery, cone-candidate diagnostics, and experimental closed-shell solid promotion; robust trimmed cylinder/cone boundary stitching, hole/cutout recovery, fillets, and broader freeform recovery are future stages

## Troubleshooting

- If `python` is not found, activate Conda first with `conda activate cad2ar`
- If the browser does not show models, make sure you started `python -m http.server 8000` in the same folder as the GLB files
- If you see old flattened output, make sure you are opening the new `_fixed.glb` file rather than an older `.glb`
- If a STEP file opens but exports no parts, the file may not contain triangulatable solid/surface geometry in a form OpenCascade can mesh directly

# CADConverter

Two-way local CAD conversion workbench for:

- `STEP/STP -> GLB` for browser, AR, and real-time viewing
- `GLB/GLTF -> STEP` for mesh-to-CAD reconstruction experiments

The forward path reads STEP assemblies through OpenCascade XCAF and exports hierarchy-preserving GLB files. The reverse path converts GLB mesh scenes back to STEP using faceted export, planar reconstruction, and guarded advanced analytic recovery.

Built with `pythonocc-core`, `trimesh`, `pygltflib`, and a local browser workbench.

## Current Status

This is ready as a practical local package/prototype.

The `STEP/STP -> GLB` path is the production-oriented side: it preserves assemblies, part instances, transforms, colors, and available STEP material information as much as the STEP file provides.

The `GLB/GLTF -> STEP` path is advanced but experimental: it can produce useful CAD-like STEP from meshes, but it does not recover original CAD feature history, sketches, constraints, fillets, or arbitrary NURBS design intent.

## Features

- One local workbench UI for both conversion directions
- STEP/STP assembly export to GLB
- Preserved part hierarchy instead of one flattened mesh
- Separate part/component meshes with scene-node transforms
- Nested assemblies and repeated part instances
- STEP colors and available visual materials
- Double-sided GLB material option for thin CAD surfaces
- GLB validation for meshes, materials, colors, normals, and named nodes
- GLB/GLTF to STEP reverse conversion
- Reverse modes: `faceted`, `reconstructed`, and `advanced`
- Planar triangle-region merging for cleaner CAD-like faces
- OpenCascade sewing for reconstructed topology
- Guarded analytic cylinders, spheres, cones, and frustums where safe
- Closed-shell solid promotion where OpenCascade accepts it
- Guarded planar hole probing in an isolated process to avoid killing the workbench on native OpenCascade crashes

## Setup

Create and activate the Conda environment:

```bash
conda create -n cad2ar python=3.10
conda activate cad2ar
```

Install OpenCascade from Conda Forge, then install this package:

```bash
conda install -c conda-forge pythonocc-core
pip install -e .
```

`pythonocc-core` is installed through Conda because that is the most reliable distribution path for OpenCascade bindings.

## Start The Workbench

From the project folder:

```bash
cd "D:\Coding\Python\CAD to ARVR\CadConverter"
conda activate cad2ar
cadconverter workbench --directory . --host 127.0.0.1 --port 8765
```

Or use the helper batch file:

```bash
start_workbench.bat
```

Then open:

```text
http://127.0.0.1:8765/
```

The workbench first asks what you want to convert:

- `STEP/STP to GLB`
- `GLB to STEP`

Only the matching controls are shown.

## Workbench: STEP/STP To GLB

Use this when you have a CAD STEP file and want a browser/AR-friendly GLB.

- `Input STEP/STP`: STEP file to convert.
- `Output GLB Name`: output filename.
- `Linear Deflection`: mesh accuracy. Smaller values create smoother/heavier GLBs. Default: `0.15`.
- `Angular Deflection`: angular mesh quality for curved surfaces. Smaller values create smoother/heavier GLBs. Default: `0.25`.
- `Double-sided materials`: makes exported faces visible from both sides in web/AR viewers.
- `Validate exported GLB`: checks the generated GLB after export.
- `Convert STEP to GLB`: runs the conversion.

The exported GLB is also added to the GLB list so it can be validated or used in the reverse conversion flow.

## Workbench: GLB To STEP

Use this when you have a GLB mesh and want a STEP output.

- `Input GLB`: GLB/GLTF file to convert.
- `Reverse Mode`: controls how much reconstruction is attempted.
- `Advanced`: recommended default. Uses planar reconstruction, sewing, guarded analytic primitives, and solid promotion where safe.
- `Reconstructed`: conservative CAD-like output. Merges coplanar triangles into larger planar STEP faces and sews topology.
- `Faceted`: most literal fallback. Exports one STEP face per mesh triangle. Reliable but large and mesh-like.
- `Analytic cylinders`: in reconstructed mode, attempts cylinder surfaces instead of flat triangle patches.
- `Promote solids`: tries to convert closed sewn shells into STEP solids.
- `Guarded holes`: attempts simple planar hole loops while skipping complex loops that are more likely to crash OpenCascade.
- `Output STEP Name`: output filename.
- `Convert to STEP`: runs the selected reverse conversion.
- `Assess Modes`: compares faceted, reconstructed, and advanced outputs for size, faces, shells, solids, free edges, and quality.
- `Probe Holes`: tests hole reconstruction in an isolated child process so native crashes are contained.
- `Validate GLB`: checks the selected GLB before reverse conversion.

Recommended defaults:

```text
STEP/STP -> GLB:
  Linear Deflection: 0.15
  Angular Deflection: 0.25
  Double-sided materials: On
  Validate exported GLB: On

GLB -> STEP:
  Reverse Mode: Advanced
```

If `Advanced` gives odd output, try `Reconstructed`. If you need the safest geometry transfer and do not care about very large STEP files, use `Faceted`.

## Command Line

Convert one STEP file to GLB:

```bash
cadconverter convert "model.step" -o "model.glb" --overwrite --validate
```

Convert every STEP/STP file in a folder:

```bash
cadconverter convert . --overwrite --validate
```

Validate an existing GLB:

```bash
cadconverter validate "model.glb"
```

Convert a GLB mesh scene back to STEP:

```bash
cadconverter glb-to-step "model.glb" -o "model.step" --mode advanced --overwrite
```

Use a safer planar reconstruction mode:

```bash
cadconverter glb-to-step "model.glb" -o "model_reconstructed.step" --mode reconstructed --overwrite
```

Use the heavy triangle-preserving fallback:

```bash
cadconverter glb-to-step "model.glb" -o "model_faceted.step" --mode faceted --overwrite
```

Start the workbench:

```bash
cadconverter workbench --directory .
```

## Useful CLI Options

Forward STEP/STP to GLB:

- `--out-dir <folder>` writes generated GLB files to another folder.
- `--suffix <text>` changes generated names for folder conversion. Default: `_fixed`.
- `--linear-deflection <value>` controls mesh resolution.
- `--angular-deflection <value>` controls angular mesh quality.
- `--single-sided` disables forced double-sided GLB materials.
- `--validate` checks the exported GLB after conversion.

Reverse GLB to STEP:

- `--mode advanced` runs the best guarded reverse path.
- `--mode reconstructed` merges connected coplanar triangle patches into larger planar faces.
- `--mode faceted` exports one STEP face per mesh triangle.
- `--unit-scale <value>` scales GLB units before STEP export. Default: `1000`, converting glTF meters to STEP millimeters.
- `--angle-tolerance <degrees>` controls planar normal grouping.
- `--plane-tolerance <value>` controls planar offset grouping after scaling.
- `--reconstruct-holes` enables experimental planar inner-loop reconstruction.
- `--max-hole-loop-points <count>` skips complex hole loops. Default: `48`.
- `--reconstruct-cylinders` attempts analytic cylindrical STEP faces.
- `--reconstruct-spheres` attempts complete analytic sphere STEP solids.
- `--reconstruct-cones` attempts complete cone/frustum STEP solids.
- `--min-cylinder-span <degrees>` controls how complete a cylinder must be before analytic export. Default: `330`.
- `--max-cylinder-free-edges <count>` falls back if analytic cylinders leave too many free edges. Default: `20`.
- `--make-solids` promotes sewn closed shells into STEP solids when valid.
- `--no-sew` disables OpenCascade sewing.
- `--sew-tolerance <value>` controls sewing tolerance.

## Testing And Diagnostics

Run reverse-conversion smoke tests on available GLBs:

```bash
cadconverter selftest --directory .
```

Run synthetic primitive reconstruction tests:

```bash
cadconverter primitive-selftest
```

Compare reverse modes:

```bash
cadconverter assess-reconstruction --directory .
```

Safely probe real-model hole reconstruction:

```bash
cadconverter probe-holes "model.glb"
```

`probe-holes` runs in an isolated child Python process. If OpenCascade hits a native access violation, the main CLI/workbench should survive and report the child crash, commonly as `0xC0000005` on Windows.

## Expected Outputs

For STEP/STP to GLB, output should contain:

- assembly nodes and subassembly nodes
- separate leaf part meshes
- repeated instances represented as scene nodes where possible
- transforms stored on nodes instead of baked into one flattened mesh
- STEP colors and available visual materials
- generated practical PBR material values when STEP material data is incomplete

For GLB to STEP, output may contain:

- one STEP compound built from the GLB scene meshes
- reconstructed planar STEP faces
- sewn shell-style topology
- optional promoted solids
- optional analytic cylinders, spheres, cones, and frustums
- fallback triangle faces for unsafe regions
- diagnostics for planar regions, holes, free edges, shells, solids, and skipped faces

## Optional Viewers

The workbench is the recommended interface. The older standalone viewers are still available for quick GLB inspection:

```bash
python -m http.server 8000
```

Then open:

- `http://localhost:8000/viewer_parts.html`
- `http://localhost:8000/viewer.html`

`viewer_parts.html` is useful for checking part/component separation in exported GLBs.

## Limitations

- STEP files should be AP214/AP242-style files that OpenCascade can read and mesh.
- STEP units are treated as millimeters for forward export and converted to glTF meters.
- GLB to STEP reconstruction starts from triangle mesh data, not original CAD features.
- Reverse conversion does not recover original sketches, constraints, feature tree, parametric dimensions, fillets, or arbitrary NURBS design intent.
- Hole reconstruction is guarded because some complex OpenCascade wire-building cases can crash native Python.
- Native CAD formats like `.sldprt`, `.sldasm`, `.par`, `.asm`, `.ipt`, or `.iam` are not directly supported unless they are exported to STEP first.

## Troubleshooting

- If `cadconverter` is not found, activate Conda and run `pip install -e .` again.
- If the workbench does not load, make sure the server terminal is still running and open `http://127.0.0.1:8765/`.
- If STEP to GLB creates a rough mesh, lower `Linear Deflection` and `Angular Deflection`.
- If GLB to STEP creates a huge file, use `Advanced` or `Reconstructed` instead of `Faceted`.
- If GLB to STEP advanced output looks odd, retry with `Reconstructed`.
- If hole reconstruction fails or crashes in probing, keep `Guarded holes` off for that model.

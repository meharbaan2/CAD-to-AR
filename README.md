# CADLite - CAD to GLB Converter and Web/AR Viewer

Convert **STEP (.stp / .step)** CAD files into **GLB** for browser and AR viewing.
The converter uses `pythonocc-core` to read STEP assemblies through XCAF/`STEPCAFControl_Reader` and exports a glTF scene that preserves assembly hierarchy, part instances, and transforms.

Built with `pythonocc-core`, `trimesh`, `pygltflib`, `<model-viewer>`, and `three.js`.

## Features

- Converts `.stp` / `.step` files to `.glb`
- Preserves assembly hierarchy instead of collapsing everything into one mesh
- Exports individual part/component meshes with their own transforms
- Handles nested assemblies and repeated subassemblies
- Preserves STEP part colors consistently in the exported GLB
- Creates distinct glTF materials per part type when STEP visual material data is missing
- Forces double-sided materials to reduce missing-face issues in thin CAD geometry
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

## Notes

- STEP files exported as AP214 or AP242 are supported
- Units are assumed to be millimeters and are converted to meters
- Hierarchy is read through XCAF/STEPCAF metadata, not just a flattened root shape
- Nested subassemblies are exported as GLB nodes
- STEP colors are read from XCAF color assignments and written into the GLB as mesh colors
- STEP visual materials are used when available; otherwise the exporter infers practical PBR materials from part names, colors, and available metadata
- Double-sided rendering helps with thin walls and one-sided CAD surfaces

## Troubleshooting

- If `python` is not found, activate Conda first with `conda activate cad2ar`
- If the browser does not show models, make sure you started `python -m http.server 8000` in the same folder as the GLB files
- If you see old flattened output, make sure you are opening the new `_fixed.glb` file rather than an older `.glb`
- If a STEP file opens but exports no parts, the file may not contain triangulatable solid/surface geometry in a form OpenCascade can mesh directly

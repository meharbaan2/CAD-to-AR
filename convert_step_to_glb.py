import os
import numpy as np
import trimesh
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRep import BRep_Tool
from OCC.Core.TopLoc import TopLoc_Location

# Use current working directory
input_dir = os.getcwd()
print(f"Scanning for STEP files in: {input_dir}")

# Find all STEP files
step_files = [f for f in os.listdir(input_dir) if f.lower().endswith(('.stp', '.step'))]

if not step_files:
    print("No STEP files found in this directory.")
    exit()

for step_file in step_files:
    glb_file = os.path.splitext(step_file)[0] + ".glb"
    print(f"Converting: {step_file} → {glb_file}")

    reader = STEPControl_Reader()
    status = reader.ReadFile(step_file)
    if status != IFSelect_RetDone:
        print(f"Failed to read: {step_file}")
        continue

    reader.TransferRoots()
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, 0.5)

    vertices = []
    faces = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = exp.Current()
        loc = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, loc)
        if triangulation:
            n_nodes = triangulation.NbNodes()
            n_tris = triangulation.NbTriangles()
            node_offset = len(vertices)

            for i in range(1, n_nodes + 1):
                p = triangulation.Node(i)
                vertices.append([p.X(), p.Y(), p.Z()])

            for i in range(1, n_tris + 1):
                tri = triangulation.Triangle(i)
                faces.append([
                    tri.Value(1) - 1 + node_offset,
                    tri.Value(2) - 1 + node_offset,
                    tri.Value(3) - 1 + node_offset
                ])
        exp.Next()

    if not vertices or not faces:
        print(f"No geometry found in {step_file}. Skipping.")
        continue

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
    mesh.export(glb_file)
    print(f"Converted {step_file} → {glb_file}")

print("All conversions completed.")

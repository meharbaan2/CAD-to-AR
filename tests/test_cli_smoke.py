from argparse import Namespace
from pathlib import Path
import tempfile
import unittest

from cadconverter import cli
from cadconverter.workbench import _is_glb, _is_step


class CliSmokeTests(unittest.TestCase):
    def test_step_files_discovers_step_and_stp_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b.step").write_text("step", encoding="utf-8")
            (root / "a.stp").write_text("step", encoding="utf-8")
            (root / "ignore.glb").write_text("glb", encoding="utf-8")

            discovered = [path.name for path in cli._step_files(root)]

        self.assertEqual(discovered, ["a.stp", "b.step"])

    def test_output_path_uses_suffix_for_folder_conversion(self):
        step_path = Path("model.step")
        args = Namespace(output=None, out_dir=None, suffix="_fixed", inputs=["."])

        output = cli._output_path(step_path, args)

        self.assertEqual(output, Path("model_fixed.glb"))

    def test_parser_accepts_advanced_reverse_command(self):
        parser = cli.build_parser()

        args = parser.parse_args(
            ["glb-to-step", "input.glb", "-o", "output.step", "--mode", "advanced", "--overwrite"]
        )

        self.assertEqual(args.input, "input.glb")
        self.assertEqual(args.output, "output.step")
        self.assertEqual(args.mode, "advanced")
        self.assertTrue(args.overwrite)
        self.assertIs(args.func, cli.glb_to_step_command)

    def test_workbench_suffix_helpers_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            part_step = root / "PART.STEP"
            part_stp = root / "part.stp"
            scene_glb = root / "scene.GLB"
            scene_gltf = root / "scene.gltf"
            for path in (part_step, part_stp, scene_glb, scene_gltf):
                path.write_text("", encoding="utf-8")

            self.assertTrue(_is_step(part_step))
            self.assertTrue(_is_step(part_stp))
            self.assertTrue(_is_glb(scene_glb))
            self.assertTrue(_is_glb(scene_gltf))
            self.assertFalse(_is_step(scene_glb))


if __name__ == "__main__":
    unittest.main()

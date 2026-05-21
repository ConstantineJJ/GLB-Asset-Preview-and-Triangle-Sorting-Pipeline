import argparse
import gc
import json
import math
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Vector


GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def parse_args() -> argparse.Namespace:
    argv = []
    if "--" in sys.argv:
        argv = sys.argv[sys.argv.index("--") + 1 :]

    parser = argparse.ArgumentParser(description="Blender worker for GLB preview rendering.")
    parser.add_argument("--manifest", required=True, help="JSON file with jobs, size and options.")
    parser.add_argument("--result", required=True, help="Output JSON file with render results.")
    return parser.parse_args(argv)


def clear_scene_and_memory() -> None:
    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

    data_blocks = [
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.textures,
        bpy.data.images,
        bpy.data.curves,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.collections,
        bpy.data.node_groups,
    ]
    for collection in data_blocks:
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)

    if hasattr(bpy.data, "orphans_purge"):
        bpy.data.orphans_purge(do_recursive=True)
    gc.collect()


def setup_render(scene: bpy.types.Scene, size: int, transparent: bool) -> None:
    engine_items = {
        item.identifier for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    if "BLENDER_EEVEE_NEXT" in engine_items:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    elif "BLENDER_EEVEE" in engine_items:
        scene.render.engine = "BLENDER_EEVEE"
    else:
        scene.render.engine = "BLENDER_WORKBENCH"

    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 32
        if hasattr(scene.eevee, "taa_samples"):
            scene.eevee.taa_samples = 16

    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = transparent
    scene.frame_set(scene.frame_start)

    if scene.world is None:
        scene.world = bpy.data.worlds.new("PreviewWorld")
    scene.world.use_nodes = True
    nodes = scene.world.node_tree.nodes
    links = scene.world.node_tree.links
    nodes.clear()
    bg = nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.09, 0.09, 0.09, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    out = nodes.new("ShaderNodeOutputWorld")
    links.new(bg.outputs["Background"], out.inputs["Surface"])


def import_model(scene: bpy.types.Scene, model_path: Path) -> None:
    bpy.ops.import_scene.gltf(filepath=str(model_path))
    scene.frame_set(scene.frame_start)
    remove_imported_lights_and_cameras(scene)


def compute_scene_bounds(scene: bpy.types.Scene) -> tuple[Vector, float]:
    minimum = Vector((float("inf"), float("inf"), float("inf")))
    maximum = Vector((float("-inf"), float("-inf"), float("-inf")))
    has_geometry = False

    for obj in scene.objects:
        if obj.type not in GEOMETRY_TYPES:
            continue
        if not hasattr(obj, "bound_box") or obj.bound_box is None:
            continue
        for corner in obj.bound_box:
            world_corner = obj.matrix_world @ Vector(corner)
            minimum.x = min(minimum.x, world_corner.x)
            minimum.y = min(minimum.y, world_corner.y)
            minimum.z = min(minimum.z, world_corner.z)
            maximum.x = max(maximum.x, world_corner.x)
            maximum.y = max(maximum.y, world_corner.y)
            maximum.z = max(maximum.z, world_corner.z)
            has_geometry = True

    if not has_geometry:
        for obj in scene.objects:
            if obj.type in {"EMPTY", "ARMATURE"}:
                loc = obj.matrix_world.translation
                minimum.x = min(minimum.x, loc.x)
                minimum.y = min(minimum.y, loc.y)
                minimum.z = min(minimum.z, loc.z)
                maximum.x = max(maximum.x, loc.x)
                maximum.y = max(maximum.y, loc.y)
                maximum.z = max(maximum.z, loc.z)
                has_geometry = True

    if not has_geometry:
        raise RuntimeError("No visible geometry found after import.")

    center = (minimum + maximum) * 0.5
    extent = maximum - minimum
    radius = max(extent.x, extent.y, extent.z) * 0.5
    if radius < 0.01:
        radius = 0.01
    return center, radius


def look_at(camera_obj: bpy.types.Object, target: Vector) -> None:
    direction = target - camera_obj.location
    camera_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_area_light(
    scene: bpy.types.Scene,
    name: str,
    location: Vector,
    energy: float,
    size: float,
) -> None:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.color = (1.0, 1.0, 1.0)
    light_data.shape = "SQUARE"
    light_data.size = size
    light_obj = bpy.data.objects.new(name, light_data)
    light_obj.location = location
    scene.collection.objects.link(light_obj)


def add_studio_lights(scene: bpy.types.Scene, center: Vector, radius: float) -> None:
    scale = max(radius, 0.5)
    add_area_light(
        scene=scene,
        name="PreviewKeyLight",
        location=center + Vector((2.2 * scale, -2.4 * scale, 2.1 * scale)),
        energy=1700.0,
        size=2.4 * scale,
    )
    add_area_light(
        scene=scene,
        name="PreviewFillLight",
        location=center + Vector((-2.0 * scale, 1.8 * scale, 1.2 * scale)),
        energy=700.0,
        size=3.0 * scale,
    )
    rim_data = bpy.data.lights.new(name="PreviewRimLight", type="SUN")
    rim_data.energy = 1.6
    rim = bpy.data.objects.new("PreviewRimLight", rim_data)
    rim.rotation_euler = (math.radians(50.0), 0.0, math.radians(-130.0))
    scene.collection.objects.link(rim)


def add_camera(scene: bpy.types.Scene, center: Vector, radius: float) -> None:
    cam_data = bpy.data.cameras.new("PreviewCamera")
    cam_data.angle = math.radians(40.0)
    cam_data.clip_start = 0.01
    cam_data.clip_end = 100000.0
    cam_obj = bpy.data.objects.new("PreviewCamera", cam_data)
    scene.collection.objects.link(cam_obj)

    direction = Vector((1.0, -1.15, 0.78)).normalized()
    distance = max((radius / math.tan(cam_data.angle * 0.5)) * 1.35, radius * 2.2, 1.8)
    cam_obj.location = center + direction * distance
    look_at(cam_obj, center)

    scene.camera = cam_obj


def remove_imported_lights_and_cameras(scene: bpy.types.Scene) -> None:
    for obj in list(scene.objects):
        if obj.type in {"LIGHT", "CAMERA"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def compute_triangle_count(scene: bpy.types.Scene) -> int:
    triangle_count = 0
    for obj in scene.objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        for polygon in obj.data.polygons:
            triangle_count += max(len(polygon.vertices) - 2, 0)
    return triangle_count


def render_job(job: dict[str, str], size: int, transparent: bool) -> None:
    scene = bpy.context.scene
    clear_scene_and_memory()
    setup_render(scene, size=size, transparent=transparent)

    model_path = Path(job["input"])
    output_path = Path(job["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import_model(scene, model_path)

    center, radius = compute_scene_bounds(scene)
    add_camera(scene, center=center, radius=radius)
    add_studio_lights(scene, center=center, radius=radius)

    scene.render.filepath = str(output_path)
    bpy.ops.render.render(write_still=True)


def analyze_job(job: dict[str, str]) -> int:
    scene = bpy.context.scene
    clear_scene_and_memory()
    model_path = Path(job["input"])
    import_model(scene, model_path)
    return compute_triangle_count(scene)


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    result_path = Path(args.result)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    mode = payload.get("mode", "render")
    jobs = payload.get("jobs", [])
    size = int(payload.get("size", 512))
    transparent = bool(payload.get("transparent", False))

    rendered: list[str] = []
    analyzed: list[dict[str, int | str]] = []
    failed: list[dict[str, str]] = []

    total = len(jobs)
    for index, job in enumerate(jobs, start=1):
        src = job.get("input", "<unknown>")
        dst = job.get("output", "<unknown>")
        try:
            if mode == "render":
                print(f"[{index}/{total}] Rendering: {src}")
                render_job(job=job, size=size, transparent=transparent)
                rendered.append(src)
                print(f"[OK] {dst}")
            elif mode == "analyze_tris":
                print(f"[{index}/{total}] Analyzing tris: {src}")
                tris = analyze_job(job=job)
                analyzed.append({"input": src, "tris": tris})
                print(f"[OK] {src} -> {tris} tris")
            else:
                raise RuntimeError(f"Unsupported worker mode: {mode}")
        except Exception as exc:
            failed.append({"input": src, "output": dst, "error": str(exc)})
            print(f"[ERROR] {src}: {exc}", file=sys.stderr)
            traceback.print_exc()
        finally:
            clear_scene_and_memory()

    result = {"rendered": rendered, "analyzed": analyzed, "failed": failed}
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    if mode == "render":
        print(
            f"Worker summary: found={total} rendered={len(rendered)} "
            f"skipped=0 failed={len(failed)}"
        )
    else:
        print(
            f"Worker summary: found={total} analyzed={len(analyzed)} "
            f"skipped=0 failed={len(failed)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

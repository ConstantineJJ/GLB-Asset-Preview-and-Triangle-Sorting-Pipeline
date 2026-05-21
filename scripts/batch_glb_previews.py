#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, cast


TRIANGLE_BUCKETS: list[tuple[str, int | None]] = [
    ("_01_very_low_0-5k", 5_000),
    ("_02_low_5k-20k", 20_000),
    ("_03_medium_20k-80k", 80_000),
    ("_04_high_80k-250k", 250_000),
    ("_05_heavy_250k_plus", None),
]
TRIANGLE_BUCKET_NAMES = {name for name, _ in TRIANGLE_BUCKETS}


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be > 0")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch-generate preview PNGs for all .glb files and sort them into "
            "triangle-count folders using Blender headless mode."
        )
    )
    parser.add_argument("--root", default=".", help="Root folder to scan recursively.")
    parser.add_argument("--size", type=positive_int, default=512, help="Square preview size in px.")
    parser.add_argument(
        "--suffix",
        default=".preview.png",
        help='Preview filename suffix (default: ".preview.png").',
    )
    parser.add_argument(
        "--transparent",
        action="store_true",
        help="Render transparent background.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-render even if preview exists and is newer than source.",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help="Process only first N .glb files (stable sorted order).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not render, only print what would be done.",
    )
    parser.add_argument(
        "--sort-only",
        action="store_true",
        help="Skip preview generation and only sort models by triangle-count folders.",
    )
    parser.add_argument(
        "--blender",
        default=None,
        help="Path to blender.exe. If omitted, autodetection is used.",
    )
    return parser.parse_args()


def discover_glb_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for dir_path, _, filenames in os.walk(root):
        base = Path(dir_path)
        for filename in filenames:
            if Path(filename).suffix.lower() == ".glb":
                found.append(base / filename)
    found.sort(key=lambda p: str(p).casefold())
    return found


def build_preview_path(model_path: Path, suffix: str) -> Path:
    return model_path.with_name(f"{model_path.stem}{suffix}")


def is_up_to_date(model_path: Path, preview_path: Path) -> bool:
    if not preview_path.exists():
        return False
    try:
        return preview_path.stat().st_mtime >= model_path.stat().st_mtime
    except OSError:
        return False


def _iter_common_blender_paths() -> Iterable[Path]:
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for base in program_files:
        if not base:
            continue
        parent = Path(base) / "Blender Foundation"
        if not parent.exists():
            continue
        for install_dir in sorted(parent.glob("Blender*"), reverse=True):
            candidate = install_dir / "blender.exe"
            if candidate.is_file():
                yield candidate


def resolve_blender_exe(explicit_path: str | None) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_file():
            return candidate
        return None

    env_path = os.environ.get("BLENDER_EXE")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            return candidate

    from_path = shutil.which("blender")
    if from_path:
        candidate = Path(from_path)
        if candidate.is_file():
            return candidate

    for candidate in _iter_common_blender_paths():
        return candidate
    return None


def run_blender_batch(
    blender_exe: Path,
    worker_script: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="glb_preview_") as tmp_dir:
        tmp = Path(tmp_dir)
        manifest_path = tmp / "manifest.json"
        result_path = tmp / "result.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        command = [
            str(blender_exe),
            "--background",
            "--factory-startup",
            "--python",
            str(worker_script),
            "--",
            "--manifest",
            str(manifest_path),
            "--result",
            str(result_path),
        ]

        print(f"Using Blender: {blender_exe}")
        process = subprocess.run(command, check=False)

        payload: dict[str, object] = {}
        if result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[ERROR] Failed to parse Blender result file: {exc}", file=sys.stderr)

        if process.returncode != 0:
            print(
                f"[ERROR] Blender exited with code {process.returncode}. "
                "Some files may be marked as failed.",
                file=sys.stderr,
            )

        return payload


def run_blender_render(
    blender_exe: Path,
    worker_script: Path,
    jobs: list[dict[str, str]],
    size: int,
    transparent: bool,
) -> tuple[set[str], list[dict[str, str]]]:
    payload = run_blender_batch(
        blender_exe=blender_exe,
        worker_script=worker_script,
        manifest={
            "mode": "render",
            "jobs": jobs,
            "size": size,
            "transparent": transparent,
        },
    )
    rendered_raw = payload.get("rendered", [])
    failed_raw = payload.get("failed", [])
    rendered = (
        {item for item in rendered_raw if isinstance(item, str)}
        if isinstance(rendered_raw, list)
        else set()
    )
    failed = (
        [cast(dict[str, str], item) for item in failed_raw if isinstance(item, dict)]
        if isinstance(failed_raw, list)
        else []
    )
    unresolved = len(jobs) - len(rendered) - len(failed)
    if unresolved > 0:
        print(
            f"[WARN] {unresolved} render jobs have no result entry and are counted as failed.",
            file=sys.stderr,
        )
        for job in jobs:
            src = job["input"]
            if src in rendered:
                continue
            if any(item.get("input") == src for item in failed):
                continue
            failed.append({"input": src, "error": "Missing Blender render result."})
    return rendered, failed


def run_blender_triangle_analysis(
    blender_exe: Path,
    worker_script: Path,
    model_paths: list[Path],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    jobs = [{"input": str(path)} for path in model_paths]
    payload = run_blender_batch(
        blender_exe=blender_exe,
        worker_script=worker_script,
        manifest={"mode": "analyze_tris", "jobs": jobs},
    )

    analyzed: dict[str, int] = {}
    analyzed_raw = payload.get("analyzed", [])
    if isinstance(analyzed_raw, list):
        for item in analyzed_raw:
            if not isinstance(item, dict):
                continue
            src = item.get("input")
            tris = item.get("tris")
            if isinstance(src, str) and isinstance(tris, int):
                analyzed[src] = tris

    failed_raw = payload.get("failed", [])
    failed = (
        [cast(dict[str, str], item) for item in failed_raw if isinstance(item, dict)]
        if isinstance(failed_raw, list)
        else []
    )
    unresolved = len(jobs) - len(analyzed) - len(failed)
    if unresolved > 0:
        print(
            f"[WARN] {unresolved} analysis jobs have no result entry and are counted as failed.",
            file=sys.stderr,
        )
        for job in jobs:
            src = job["input"]
            if src in analyzed:
                continue
            if any(item.get("input") == src for item in failed):
                continue
            failed.append({"input": src, "error": "Missing Blender triangle analysis result."})

    return analyzed, failed


def bucket_name_for_tris(triangle_count: int) -> str:
    for bucket_name, max_tris in TRIANGLE_BUCKETS:
        if max_tris is None or triangle_count <= max_tris:
            return bucket_name
    raise RuntimeError(f"No triangle bucket configured for {triangle_count} tris")


def get_bucket_base_dir(model_path: Path) -> Path:
    if model_path.parent.name in TRIANGLE_BUCKET_NAMES:
        return model_path.parent.parent
    return model_path.parent


def move_asset_to_bucket(
    model_path: Path,
    preview_path: Path,
    bucket_name: str,
    dry_run: bool,
) -> bool:
    target_dir = get_bucket_base_dir(model_path) / bucket_name
    target_model_path = target_dir / model_path.name
    target_preview_path = target_dir / preview_path.name

    if model_path == target_model_path and (
        not preview_path.exists() or preview_path == target_preview_path
    ):
        return False

    if dry_run:
        if model_path != target_model_path:
            print(f"[DRY-RUN MOVE] {model_path} -> {target_model_path}")
        if preview_path.exists() and preview_path != target_preview_path:
            print(f"[DRY-RUN MOVE] {preview_path} -> {target_preview_path}")
        return True

    target_dir.mkdir(parents=True, exist_ok=True)

    if model_path != target_model_path:
        if target_model_path.exists():
            raise FileExistsError(f"Destination already exists: {target_model_path}")
        shutil.move(str(model_path), str(target_model_path))
        print(f"[MOVE] {model_path} -> {target_model_path}")

    if preview_path.exists() and preview_path != target_preview_path:
        if target_preview_path.exists():
            raise FileExistsError(f"Destination already exists: {target_preview_path}")
        shutil.move(str(preview_path), str(target_preview_path))
        print(f"[MOVE] {preview_path} -> {target_preview_path}")

    return True


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] --root is not a directory: {root}", file=sys.stderr)
        return 2

    worker_script = Path(__file__).with_name("blender_glb_preview_worker.py")
    if not worker_script.is_file():
        print(f"[ERROR] Worker script not found: {worker_script}", file=sys.stderr)
        return 2

    all_glb = discover_glb_files(root)
    if args.limit is not None:
        selected = all_glb[: args.limit]
    else:
        selected = all_glb

    if args.limit is not None and len(all_glb) > args.limit:
        print(f"Total .glb found: {len(all_glb)}. Processing first {len(selected)} due to --limit.")
    else:
        print(f"Total .glb found: {len(selected)}")

    skipped_paths: list[Path] = []
    render_jobs: list[dict[str, str]] = []

    if args.sort_only:
        print("[INFO] Sort-only mode enabled. Preview generation will be skipped.")

    for model_path in selected:
        if args.sort_only:
            continue
        preview_path = build_preview_path(model_path, args.suffix)
        if not args.force and is_up_to_date(model_path, preview_path):
            skipped_paths.append(model_path)
            print(f"[SKIP PREVIEW] {model_path}")
            continue
        render_jobs.append({"input": str(model_path), "output": str(preview_path)})
        if args.dry_run:
            print(f"[DRY-RUN PREVIEW] {model_path} -> {preview_path}")

    found_count = len(selected)
    skipped_count = len(skipped_paths)
    rendered_count = 0
    render_failed_count = 0
    analysis_failed_count = 0
    move_failed_count = 0
    moved_count = 0

    blender_exe: Path | None = None
    if selected:
        blender_exe = resolve_blender_exe(args.blender)
        if blender_exe is None:
            print(
                "[ERROR] Blender executable not found.\n"
                "Specify it explicitly with:\n"
                '  --blender "C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe"\n'
                "or set environment variable BLENDER_EXE, or add Blender to PATH.",
                file=sys.stderr,
            )
            return 3
    blender_exe_path = cast(Path, blender_exe) if selected else None

    if render_jobs and not args.dry_run:
        rendered, render_failed = run_blender_render(
            blender_exe=cast(Path, blender_exe_path),
            worker_script=worker_script,
            jobs=render_jobs,
            size=args.size,
            transparent=args.transparent,
        )

        rendered_count = len(rendered)
        render_failed_count = len(render_failed)

        if render_failed:
            print("\nPreview render failures:")
            for item in render_failed:
                path = item.get("input", "<unknown>")
                error = item.get("error", "Unknown Blender error")
                print(f"- {path}\n  {error}")

    triangle_counts: dict[str, int] = {}
    analysis_failed: list[dict[str, str]] = []
    if selected:
        triangle_counts, analysis_failed = run_blender_triangle_analysis(
            blender_exe=cast(Path, blender_exe_path),
            worker_script=worker_script,
            model_paths=selected,
        )
        analysis_failed_count = len(analysis_failed)
        if analysis_failed:
            print("\nTriangle analysis failures:")
            for item in analysis_failed:
                path = item.get("input", "<unknown>")
                error = item.get("error", "Unknown Blender error")
                print(f"- {path}\n  {error}")

    for model_path in selected:
        triangle_count = triangle_counts.get(str(model_path))
        if triangle_count is None:
            continue

        preview_path = build_preview_path(model_path, args.suffix)
        bucket_name = bucket_name_for_tris(triangle_count)
        try:
            if move_asset_to_bucket(
                model_path=model_path,
                preview_path=preview_path,
                bucket_name=bucket_name,
                dry_run=args.dry_run,
            ):
                moved_count += 1
        except Exception as exc:
            move_failed_count += 1
            print(f"[ERROR] Failed to move {model_path}: {exc}", file=sys.stderr)

    sort_skipped_count = found_count - moved_count - analysis_failed_count - move_failed_count
    failed_count = render_failed_count + analysis_failed_count + move_failed_count

    if args.dry_run:
        print(
            f"Dry-run summary: found={found_count} would_render={len(render_jobs)} "
            f"preview_skip={skipped_count} would_move={moved_count} "
            f"sort_keep={sort_skipped_count} failed={failed_count}"
        )
        return 0 if failed_count == 0 else 1

    print(
        f"Summary: found={found_count} rendered={rendered_count} preview_skip={skipped_count} "
        f"moved={moved_count} sort_keep={sort_skipped_count} failed={failed_count}"
    )
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

# GLB Asset Preview and Triangle Sorting Pipeline

This workspace contains a Windows-friendly batch pipeline for browsing and organizing `.glb` assets without opening Blender manually for every model.

Documentation note: the README text was generated with assistance from GitHub Copilot (GPT-5.4) and then included in this repository for public use.

The tool does two jobs:

1. Generates preview images next to `.glb` files.
2. Sorts models into triangle-count folders so they are easier to inspect directly in Windows Explorer.

The main entry point is:

```powershell
.\generate_glb_previews.cmd
```

## What This Is Used For

This pipeline is meant for large 3D asset libraries where you want to:

- see a preview image for each `.glb` model,
- skip re-rendering previews that already exist and are up to date,
- organize assets by triangle budget,
- keep the original category structure intact,
- work directly in Explorer instead of spreadsheets or external catalog tools.

It is especially useful for game art libraries that contain vehicles, foliage, props, buildings, weapons, and other asset groups mixed across many folders.

## How It Works

When you run the pipeline, it scans the selected root folder recursively for `.glb` files.

Default behavior:

1. Check whether a preview file already exists and is newer than the model.
2. Render missing or outdated previews through Blender in headless mode.
3. Analyze the model triangle count.
4. Move the `.glb` file and its matching preview image into a triangle bucket folder inside the current asset category folder.

If previews already exist, the tool skips rendering and goes straight to triangle analysis and sorting.

## Triangle Buckets

Models are sorted into these folders:

- `_01_very_low_0-5k`
- `_02_low_5k-20k`
- `_03_medium_20k-80k`
- `_04_high_80k-250k`
- `_05_heavy_250k_plus`

These names are prefixed so Windows Explorer keeps them in the intended order.

## Preview Output

By default, the preview image is saved as:

```text
model_name.preview.png
```

Example:

```text
Buggy_fast_blue.glb
Buggy_fast_blue.preview.png
```

When a model is moved into a triangle bucket folder, its preview image is moved together with it.

## Requirements

- Windows
- Python available from the command line
- Blender installed

Blender can be resolved in any of these ways:

- found in `PATH`
- provided through the `BLENDER_EXE` environment variable
- passed explicitly with `--blender`
- auto-detected from a standard Blender installation path

## Main Commands

### 1. Full pipeline on the current folder

```powershell
.\generate_glb_previews.cmd --root "."
```

This will:

- scan all `.glb` files under the current folder,
- skip previews that are already up to date,
- render missing previews,
- analyze triangle counts,
- move models and previews into triangle bucket folders.

### 2. Sort only, without generating previews

```powershell
.\generate_glb_previews.cmd --root "." --sort-only
```

Use this when preview images already exist and you only want to sort models by triangle count.

### 3. Dry run

```powershell
.\generate_glb_previews.cmd --root "." --dry-run
```

This shows what the tool would do without rendering or moving anything.

### 4. Dry run for sort-only mode

```powershell
.\generate_glb_previews.cmd --root "." --sort-only --dry-run
```

Useful for verifying bucket moves before changing files.

### 5. Limit processing to the first N models

```powershell
.\generate_glb_previews.cmd --root "." --limit 10
```

Useful for smoke tests.

### 6. Force preview regeneration

```powershell
.\generate_glb_previews.cmd --root "." --force
```

This re-renders previews even if existing previews are newer than the source model.

### 7. Use transparent preview backgrounds

```powershell
.\generate_glb_previews.cmd --root "." --transparent
```

### 8. Set a custom preview size

```powershell
.\generate_glb_previews.cmd --root "." --size 1024
```

### 9. Set a custom preview suffix

```powershell
.\generate_glb_previews.cmd --root "." --suffix ".thumb.png"
```

### 10. Use a specific Blender executable

```powershell
.\generate_glb_previews.cmd --root "." --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
```

## Command-Line Parameters

The pipeline supports the following parameters:

- `--root ROOT`
  Recursively scans this folder for `.glb` files.

- `--size SIZE`
  Sets the square preview resolution in pixels.

- `--suffix SUFFIX`
  Sets the preview filename suffix. Default: `.preview.png`.

- `--transparent`
  Renders previews with a transparent background.

- `--force`
  Forces preview regeneration even if the preview file is newer than the model.

- `--limit LIMIT`
  Processes only the first `N` `.glb` files in stable sorted order.

- `--dry-run`
  Shows planned work without rendering or moving files.

- `--sort-only`
  Skips preview generation completely and performs only triangle analysis plus sorting.

- `--blender BLENDER`
  Explicit path to `blender.exe`.

## Typical Workflows

### Initial setup for a new asset library

```powershell
.\generate_glb_previews.cmd --root "."
```

### Re-sort after new models were added, while keeping old previews

```powershell
.\generate_glb_previews.cmd --root "." --sort-only
```

### Test on a small subset first

```powershell
.\generate_glb_previews.cmd --root ".\fast3D" --limit 5 --dry-run
```

## Notes

- The tool is designed for asset libraries, not for projects that depend on fixed asset paths.
- Models are physically moved into bucket folders.
- Existing category folders are preserved because sorting happens inside the current asset folder tree.
- If a matching preview image exists, it is moved together with the `.glb` file.
- Sorting is based on triangle count only.

## Internal Files

The command file launches this Python pipeline:

- `generate_glb_previews.cmd`
- `scripts/batch_glb_previews.py`
- `scripts/blender_glb_preview_worker.py`

## Summary

Use this pipeline when you want a fast, practical way to browse a large `.glb` library directly in Windows Explorer with:

- generated previews,
- automatic preview skipping,
- triangle-based folder sorting,
- no manual Blender imports for each asset.

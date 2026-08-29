# Fiji CLI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scale bar, LUT, channel merge, and figure assembly commands to the Fiji CLI; fix known bugs; and create a CLI reference for the fiji-best-practices skill.

**Architecture:** Each feature adds a new core module (or extends an existing one) with its ImageJ macro templates, CLI commands in `fiji_cli.py`, and unit tests. The pattern follows the existing codebase: define operations as macro templates in the core module, wire them through Click commands, test with both unit tests and E2E tests.

**Tech Stack:** Python 3.10+, Click CLI framework, ImageJ macro language, Fiji headless backend.

---

## Phase 1: Bug Fixes

### Task 1: Fix session.save_session() crash on missing metadata

**Files:**
- Modify: `cli_anything/fiji/core/session.py:108`
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Write the failing test**

```python
# In TestSession class
def test_save_session_no_metadata(self):
    """save_session should not crash if project has no metadata key."""
    sess = Session()
    proj = {"name": "bare_project", "images": []}  # no "metadata" key
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        sess.set_project(proj, path)
        saved = sess.save_session()
        assert os.path.exists(saved)
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/urkodelcastillo/Documents/0_VSC_projects/CLI_anything/fiji/agent-harness && python3 -m pytest cli_anything/fiji/tests/test_core.py::TestSession::test_save_session_no_metadata -v`
Expected: FAIL with KeyError on `project["metadata"]`

- [ ] **Step 3: Fix session.py line 108**

Change line 108 in `session.py` from:
```python
self.project["metadata"]["modified"] = datetime.now().isoformat()
```
to:
```python
if "metadata" not in self.project:
    self.project["metadata"] = {}
self.project["metadata"]["modified"] = datetime.now().isoformat()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestSession::test_save_session_no_metadata -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli_anything/fiji/core/session.py cli_anything/fiji/tests/test_core.py
git commit -m "fix: handle missing metadata key in session save"
```

### Task 2: Fix export error message truncation

**Files:**
- Modify: `cli_anything/fiji/core/export.py:144-145`

- [ ] **Step 1: Improve error output in export.py**

Change lines 141-146 in `export.py` from:
```python
    if not os.path.exists(abs_output):
        raise RuntimeError(
            f"Fiji export produced no output file.\n"
            f"  Expected: {abs_output}\n"
            f"  stderr: {result.get('stderr', '')[-500:]}\n"
            f"  stdout: {result.get('stdout', '')[-500:]}"
        )
```
to:
```python
    if not os.path.exists(abs_output):
        stderr = result.get('stderr', '')
        stdout = result.get('stdout', '')
        raise RuntimeError(
            f"Fiji export produced no output file.\n"
            f"  Expected: {abs_output}\n"
            f"  stderr: {stderr}\n"
            f"  stdout: {stdout}"
        )
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `python3 -m pytest cli_anything/fiji/tests/ -v`
Expected: All 72 tests PASS

- [ ] **Step 3: Commit**

```bash
git add cli_anything/fiji/core/export.py
git commit -m "fix: show full stderr/stdout in export error messages"
```

---

## Phase 2: Scale Bar Commands

### Task 3: Add scale bar operations to processing.py

**Files:**
- Modify: `cli_anything/fiji/core/processing.py` (add to OPERATIONS dict)
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Write the failing test**

```python
# In TestProcessing class
def test_add_scale_bar(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "add_scale_bar", params={
        "width": 10, "height": 4, "font": 14,
        "color": "White", "location": "Lower Right"
    })
    assert step["operation"] == "add_scale_bar"
    assert "Scale Bar" in step["macro"]

def test_set_scale(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "set_scale", params={
        "distance": 100, "known": 10, "unit": "um"
    })
    assert "Set Scale" in step["macro"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestProcessing::test_add_scale_bar -v`
Expected: FAIL with "Unknown operation: add_scale_bar"

- [ ] **Step 3: Add operations to OPERATIONS dict in processing.py**

Add after the "enhance_contrast" entry:
```python
    # Scale bar
    "set_scale": {
        "category": "adjust",
        "description": "Set image spatial calibration",
        "macro": 'run("Set Scale...", "distance={distance} known={known} unit={unit}");',
        "params": {"distance": 1, "known": 1, "unit": "pixel"},
    },
    "add_scale_bar": {
        "category": "adjust",
        "description": "Add scale bar overlay to image",
        "macro": 'run("Scale Bar...", "width={width} height={height} font={font} color={color} background=None location=[{location}] bold overlay");',
        "params": {"width": 10, "height": 4, "font": 14, "color": "White", "location": "Lower Right"},
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestProcessing::test_add_scale_bar cli_anything/fiji/tests/test_core.py::TestProcessing::test_set_scale -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli_anything/fiji/core/processing.py cli_anything/fiji/tests/test_core.py
git commit -m "feat: add scale bar and set_scale processing operations"
```

---

## Phase 3: LUT Management

### Task 4: Add LUT operations to processing.py

**Files:**
- Modify: `cli_anything/fiji/core/processing.py`
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_apply_lut(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "apply_lut", params={"lut": "Fire"})
    assert "Fire" in step["macro"]

def test_apply_lut_grays(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "apply_lut", params={"lut": "Grays"})
    assert "Grays" in step["macro"]

def test_set_display_range(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "set_display_range", params={"min_val": 100, "max_val": 4000})
    assert "setMinAndMax" in step["macro"]

def test_invert_lut(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "invert_lut")
    assert "Invert LUT" in step["macro"]

def test_add_calibration_bar(self):
    proj = proj_mod.create_project()
    step = proc_mod.add_processing_step(proj, "add_calibration_bar", params={
        "location": "Upper Right", "label_color": "White", "font": 12
    })
    assert "Calibration Bar" in step["macro"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestProcessing::test_apply_lut -v`
Expected: FAIL

- [ ] **Step 3: Add LUT operations to OPERATIONS dict**

```python
    # LUT / Display
    "apply_lut": {
        "category": "adjust",
        "description": "Apply a lookup table (color map) to the image",
        "macro": 'run("{lut}");',
        "params": {"lut": "Grays"},
    },
    "set_display_range": {
        "category": "adjust",
        "description": "Set the display min/max range (does not modify pixel data)",
        "macro": 'setMinAndMax({min_val}, {max_val});',
        "params": {"min_val": 0, "max_val": 255},
    },
    "invert_lut": {
        "category": "adjust",
        "description": "Invert the current lookup table",
        "macro": 'run("Invert LUT");',
        "params": {},
    },
    "add_calibration_bar": {
        "category": "adjust",
        "description": "Add a calibration bar showing LUT intensity mapping",
        "macro": 'run("Calibration Bar...", "location=[{location}] fill=None label={label_color} number=5 decimal=0 font={font} zoom=1 overlay");',
        "params": {"location": "Upper Right", "label_color": "White", "font": 12},
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestProcessing -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add cli_anything/fiji/core/processing.py cli_anything/fiji/tests/test_core.py
git commit -m "feat: add LUT, display range, and calibration bar operations"
```

---

## Phase 4: Channel Operations

### Task 5: Add channel module

**Files:**
- Create: `cli_anything/fiji/core/channel.py`
- Modify: `cli_anything/fiji/fiji_cli.py` (add channel command group)
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
# New TestChannel class
from cli_anything.fiji.core import channel as chan_mod

class TestChannel:
    def test_list_luts(self):
        luts = chan_mod.list_luts()
        assert len(luts) > 0
        names = [l["name"] for l in luts]
        assert "Grays" in names
        assert "Green" in names
        assert "Magenta" in names

    def test_build_merge_macro(self):
        channels = [
            {"path": "/tmp/ch1.tif", "color": "Green"},
            {"path": "/tmp/ch2.tif", "color": "Magenta"},
        ]
        macro = chan_mod.build_merge_macro(channels)
        assert 'open("/tmp/ch1.tif")' in macro
        assert 'open("/tmp/ch2.tif")' in macro
        assert "Merge Channels" in macro

    def test_build_merge_macro_three_channels(self):
        channels = [
            {"path": "/tmp/dapi.tif", "color": "Blue"},
            {"path": "/tmp/gfp.tif", "color": "Green"},
            {"path": "/tmp/rfp.tif", "color": "Magenta"},
        ]
        macro = chan_mod.build_merge_macro(channels)
        assert "c3=" in macro  # Blue -> c3
        assert "c2=" in macro  # Green -> c2
        assert "c6=" in macro  # Magenta -> c6

    def test_build_split_macro(self):
        macro = chan_mod.build_split_macro("/tmp/composite.tif", "/tmp/out")
        assert 'open("/tmp/composite.tif")' in macro
        assert "Split Channels" in macro
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestChannel -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Create channel.py**

```python
"""Fiji CLI - Channel operations module.

Handles channel merging, splitting, and LUT assignment
for multi-channel microscopy images.
"""

from typing import Dict, Any, List, Optional


# ImageJ channel color assignments
# c1=Red, c2=Green, c3=Blue, c4=Gray, c5=Cyan, c6=Magenta, c7=Yellow
COLOR_TO_CHANNEL = {
    "Red": "c1",
    "Green": "c2",
    "Blue": "c3",
    "Gray": "c4",
    "Cyan": "c5",
    "Magenta": "c6",
    "Yellow": "c7",
}

AVAILABLE_LUTS = [
    {"name": "Grays", "description": "Grayscale (best for single channels)", "colorblind_safe": True},
    {"name": "Green", "description": "Green channel", "colorblind_safe": True},
    {"name": "Magenta", "description": "Magenta (colorblind-safe alternative to Red)", "colorblind_safe": True},
    {"name": "Cyan", "description": "Cyan channel", "colorblind_safe": True},
    {"name": "Yellow", "description": "Yellow channel", "colorblind_safe": True},
    {"name": "Blue", "description": "Blue channel", "colorblind_safe": True},
    {"name": "Red", "description": "Red channel (avoid for red/green pairs)", "colorblind_safe": False},
    {"name": "Fire", "description": "Fire LUT (perceptually uniform)", "colorblind_safe": True},
    {"name": "Ice", "description": "Ice LUT", "colorblind_safe": True},
    {"name": "mpl-viridis", "description": "Viridis (perceptually uniform)", "colorblind_safe": True},
    {"name": "mpl-inferno", "description": "Inferno (perceptually uniform)", "colorblind_safe": True},
    {"name": "HiLo", "description": "HiLo (QC: highlights saturated/zero pixels)", "colorblind_safe": True},
]


def list_luts() -> List[Dict[str, Any]]:
    """List available lookup tables."""
    return AVAILABLE_LUTS


def build_merge_macro(
    channels: List[Dict[str, str]],
    create_composite: bool = True,
) -> str:
    """Build an ImageJ macro to merge multiple channel images.

    Args:
        channels: List of dicts with 'path' and 'color' keys.
            color must be one of: Red, Green, Blue, Gray, Cyan, Magenta, Yellow.
        create_composite: If True, create a composite (recommended). If False, create RGB.
    """
    lines = ['setBatchMode(true);']

    # Open each channel and assign a window title
    titles = []
    for i, ch in enumerate(channels):
        title = f"ch{i}"
        lines.append(f'open("{ch["path"]}");')
        lines.append(f'rename("{title}");')
        titles.append(title)

    # Build merge command
    merge_args = []
    for i, ch in enumerate(channels):
        color = ch["color"]
        if color not in COLOR_TO_CHANNEL:
            raise ValueError(f"Unknown color: {color}. Use one of: {', '.join(COLOR_TO_CHANNEL.keys())}")
        channel_key = COLOR_TO_CHANNEL[color]
        merge_args.append(f"{channel_key}={titles[i]}")

    composite_flag = " create" if create_composite else ""
    lines.append(f'run("Merge Channels...", "{" ".join(merge_args)}{composite_flag}");')
    lines.append('setBatchMode(false);')

    return "\n".join(lines)


def build_split_macro(
    input_path: str,
    output_dir: str,
) -> str:
    """Build an ImageJ macro to split a composite image into channels.

    Args:
        input_path: Path to the composite/multi-channel image.
        output_dir: Directory to save individual channel files.
    """
    lines = [
        'setBatchMode(true);',
        f'open("{input_path}");',
        'title = getTitle();',
        'run("Split Channels");',
        f'list = getList("image.titles");',
        'for (i = 0; i < list.length; i++) {',
        '    selectWindow(list[i]);',
        f'    saveAs("Tiff", "{output_dir}" + File.separator + list[i] + ".tif");',
        '    close();',
        '}',
        'setBatchMode(false);',
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestChannel -v`
Expected: All PASS

- [ ] **Step 5: Add CLI commands for channels in fiji_cli.py**

Add after the ROI command group:
```python
from cli_anything.fiji.core import channel as chan_mod

# ── Channel Commands ─────────────────────────────────────────
@cli.group()
def channel():
    """Channel operations (merge, split, LUT)."""
    pass

@channel.command("luts")
@handle_error
def channel_luts():
    """List available LUTs (lookup tables)."""
    luts = chan_mod.list_luts()
    output(luts, "Available LUTs:")

@channel.command("merge")
@click.argument("paths", nargs=-1, required=True)
@click.option("--colors", "-c", required=True, help="Comma-separated colors: Green,Magenta")
@click.option("--output-path", "-o", required=True, help="Output file path")
@handle_error
def channel_merge(paths, colors, output_path):
    """Merge channel images into a composite."""
    color_list = [c.strip() for c in colors.split(",")]
    if len(paths) != len(color_list):
        raise ValueError(f"Got {len(paths)} images but {len(color_list)} colors")
    channels = [{"path": os.path.abspath(p), "color": c} for p, c in zip(paths, color_list)]
    macro = chan_mod.build_merge_macro(channels)

    # Add save command
    abs_out = os.path.abspath(output_path)
    macro += f'\nsaveAs("Tiff", "{abs_out}");'

    from cli_anything.fiji.utils.fiji_backend import run_macro
    result = run_macro(macro)
    output({"output": abs_out, "channels": len(paths)}, f"Merged to: {abs_out}")

@channel.command("split")
@click.argument("input_path")
@click.option("--output-dir", "-o", required=True, help="Output directory")
@handle_error
def channel_split(input_path, output_dir):
    """Split a composite image into individual channels."""
    os.makedirs(output_dir, exist_ok=True)
    macro = chan_mod.build_split_macro(os.path.abspath(input_path), os.path.abspath(output_dir))
    from cli_anything.fiji.utils.fiji_backend import run_macro
    result = run_macro(macro)
    output({"output_dir": output_dir}, f"Channels saved to: {output_dir}")
```

- [ ] **Step 6: Run all tests to verify no regressions**

Run: `python3 -m pytest cli_anything/fiji/tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add cli_anything/fiji/core/channel.py cli_anything/fiji/fiji_cli.py cli_anything/fiji/tests/test_core.py
git commit -m "feat: add channel merge, split, and LUT listing commands"
```

---

## Phase 5: Figure Assembly

### Task 6: Create figure module

**Files:**
- Create: `cli_anything/fiji/core/figure.py`
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
from cli_anything.fiji.core import figure as fig_mod

class TestFigure:
    def test_build_montage_macro_2x2(self):
        panels = ["/tmp/a.tif", "/tmp/b.tif", "/tmp/c.tif", "/tmp/d.tif"]
        macro = fig_mod.build_montage_macro(panels, columns=2, rows=2)
        for p in panels:
            assert f'open("{p}")' in macro
        assert "Make Montage" in macro
        assert "columns=2 rows=2" in macro

    def test_build_montage_macro_with_labels(self):
        panels = ["/tmp/a.tif", "/tmp/b.tif"]
        macro = fig_mod.build_montage_macro(
            panels, columns=2, rows=1,
            labels=["GFP", "RFP"], border=4
        )
        assert "columns=2 rows=1" in macro
        assert "border=4" in macro

    def test_build_montage_macro_with_scale_bar(self):
        panels = ["/tmp/a.tif", "/tmp/b.tif"]
        macro = fig_mod.build_montage_macro(
            panels, columns=2, rows=1,
            scale_bar_width=10, scale_bar_color="White"
        )
        assert "Scale Bar" in macro

    def test_build_figure_macro_with_flatten(self):
        panels = ["/tmp/a.tif"]
        macro = fig_mod.build_montage_macro(panels, columns=1, rows=1, flatten=True)
        assert "Flatten" in macro

    def test_list_figure_presets(self):
        presets = fig_mod.list_figure_presets()
        names = [p["name"] for p in presets]
        assert "nature_single" in names
        assert "nature_double" in names

    def test_get_figure_preset(self):
        preset = fig_mod.get_figure_preset("nature_single")
        assert preset["width_mm"] == 89
        assert preset["dpi"] == 300
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestFigure -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Create figure.py**

```python
"""Fiji CLI - Figure assembly module.

Builds multi-panel figures from individual images using
ImageJ montage and annotation tools.
"""

from typing import Dict, Any, List, Optional


FIGURE_PRESETS = {
    "nature_single": {
        "name": "nature_single",
        "description": "Nature single column (89 mm)",
        "width_mm": 89,
        "dpi": 300,
        "width_px": 1051,  # 89mm at 300dpi
        "font": "Arial",
        "min_font_pt": 7,
    },
    "nature_double": {
        "name": "nature_double",
        "description": "Nature double column (183 mm)",
        "width_mm": 183,
        "dpi": 300,
        "width_px": 2161,  # 183mm at 300dpi
        "font": "Arial",
        "min_font_pt": 7,
    },
    "science_single": {
        "name": "science_single",
        "description": "Science single column (55 mm)",
        "width_mm": 55,
        "dpi": 300,
        "width_px": 650,
        "font": "Helvetica",
        "min_font_pt": 6,
    },
    "science_double": {
        "name": "science_double",
        "description": "Science double column (175 mm)",
        "width_mm": 175,
        "dpi": 300,
        "width_px": 2067,
        "font": "Helvetica",
        "min_font_pt": 6,
    },
    "cell_single": {
        "name": "cell_single",
        "description": "Cell single column (85 mm)",
        "width_mm": 85,
        "dpi": 300,
        "width_px": 1004,
        "font": "Arial",
        "min_font_pt": 6,
    },
    "cell_double": {
        "name": "cell_double",
        "description": "Cell double column (178 mm)",
        "width_mm": 178,
        "dpi": 300,
        "width_px": 2102,
        "font": "Arial",
        "min_font_pt": 6,
    },
}


def list_figure_presets() -> List[Dict[str, Any]]:
    """List available figure presets for journals."""
    return [
        {
            "name": p["name"],
            "description": p["description"],
            "width_mm": p["width_mm"],
            "dpi": p["dpi"],
        }
        for p in FIGURE_PRESETS.values()
    ]


def get_figure_preset(name: str) -> Dict[str, Any]:
    """Get a specific figure preset."""
    if name not in FIGURE_PRESETS:
        available = ", ".join(sorted(FIGURE_PRESETS.keys()))
        raise ValueError(f"Unknown preset: {name}. Available: {available}")
    return dict(FIGURE_PRESETS[name])


def build_montage_macro(
    panel_paths: List[str],
    columns: int = 2,
    rows: int = 2,
    border: int = 2,
    labels: Optional[List[str]] = None,
    scale_bar_width: Optional[int] = None,
    scale_bar_color: str = "White",
    scale_bar_height: int = 4,
    scale_bar_font: int = 14,
    flatten: bool = False,
    output_path: Optional[str] = None,
) -> str:
    """Build an ImageJ macro to assemble a multi-panel figure.

    Args:
        panel_paths: List of image file paths for each panel.
        columns: Number of columns in the montage grid.
        rows: Number of rows in the montage grid.
        border: Border width in pixels between panels.
        labels: Optional list of labels for each panel.
        scale_bar_width: Scale bar width in calibrated units (None = no scale bar).
        scale_bar_color: Scale bar color (White, Black).
        scale_bar_height: Scale bar thickness in pixels.
        scale_bar_font: Scale bar font size.
        flatten: Whether to flatten overlays before montage.
        output_path: Optional path to save the final figure.
    """
    lines = ['setBatchMode(true);']

    # Open all panels
    for i, path in enumerate(panel_paths):
        lines.append(f'open("{path}");')
        title = f"panel_{i}"
        lines.append(f'rename("{title}");')

        # Add scale bar to each panel if requested
        if scale_bar_width is not None:
            lines.append(
                f'run("Scale Bar...", "width={scale_bar_width} height={scale_bar_height} '
                f'font={scale_bar_font} color={scale_bar_color} background=None '
                f'location=[Lower Right] bold overlay");'
            )

        # Flatten overlays if requested
        if flatten:
            lines.append('run("Flatten");')

    # Convert all open images to a stack, then montage
    lines.append('run("Images to Stack", "use");')
    lines.append(
        f'run("Make Montage...", "columns={columns} rows={rows} '
        f'scale=1 border={border}");'
    )

    # Save if output path given
    if output_path:
        lines.append(f'saveAs("Tiff", "{output_path}");')

    lines.append('setBatchMode(false);')
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest cli_anything/fiji/tests/test_core.py::TestFigure -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add cli_anything/fiji/core/figure.py cli_anything/fiji/tests/test_core.py
git commit -m "feat: add figure assembly module with montage and journal presets"
```

### Task 7: Add figure CLI commands

**Files:**
- Modify: `cli_anything/fiji/fiji_cli.py`
- Test: `cli_anything/fiji/tests/test_core.py`

- [ ] **Step 1: Add figure command group to fiji_cli.py**

Add after the export command group:
```python
from cli_anything.fiji.core import figure as fig_mod

# ── Figure Commands ──────────────────────────────────────────
@cli.group()
def figure():
    """Figure assembly commands."""
    pass

@figure.command("presets")
@handle_error
def figure_presets():
    """List available journal figure presets."""
    presets = fig_mod.list_figure_presets()
    output(presets, "Figure presets:")

@figure.command("preset-info")
@click.argument("name")
@handle_error
def figure_preset_info(name):
    """Show preset details."""
    info = fig_mod.get_figure_preset(name)
    output(info)

@figure.command("montage")
@click.argument("panels", nargs=-1, required=True)
@click.option("--columns", "-c", type=int, default=2, help="Number of columns")
@click.option("--rows", "-r", type=int, default=2, help="Number of rows")
@click.option("--border", "-b", type=int, default=2, help="Border width in pixels")
@click.option("--output", "-o", "output_path", required=True, help="Output file path")
@click.option("--scale-bar", type=int, default=None, help="Scale bar width in calibrated units")
@click.option("--scale-bar-color", default="White", help="Scale bar color")
@click.option("--flatten", is_flag=True, help="Flatten overlays before montage")
@handle_error
def figure_montage(panels, columns, rows, border, output_path, scale_bar, scale_bar_color, flatten):
    """Assemble a multi-panel figure from individual images."""
    abs_panels = [os.path.abspath(p) for p in panels]
    abs_output = os.path.abspath(output_path)

    macro = fig_mod.build_montage_macro(
        abs_panels, columns=columns, rows=rows, border=border,
        scale_bar_width=scale_bar, scale_bar_color=scale_bar_color,
        flatten=flatten, output_path=abs_output,
    )
    from cli_anything.fiji.utils.fiji_backend import run_macro
    run_macro(macro)
    output({"output": abs_output, "panels": len(panels), "layout": f"{columns}x{rows}"},
           f"Figure saved to: {abs_output}")
```

- [ ] **Step 2: Update REPL command map**

In the REPL function, add to `_repl_commands`:
```python
"figure":   "presets|preset-info|montage",
"channel":  "luts|merge|split",
```

- [ ] **Step 3: Run all tests**

Run: `python3 -m pytest cli_anything/fiji/tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add cli_anything/fiji/fiji_cli.py
git commit -m "feat: add figure and channel CLI commands"
```

---

## Phase 6: Skill Integration

### Task 8: Add CLI reference to fiji-best-practices skill

**Files:**
- Create: `/Users/urkodelcastillo/.claude/skills/fiji-best-practices/references/cli-commands.md`
- Modify: `/Users/urkodelcastillo/.claude/skills/fiji-best-practices/SKILL.md`

- [ ] **Step 1: Create the CLI reference file**

Create `references/cli-commands.md` that documents all CLI commands, mapping them to best practices from the skill. Include the new commands from phases 2-5.

- [ ] **Step 2: Add reference pointer to SKILL.md**

Add a section at the bottom of SKILL.md:
```markdown
## CLI Tool Reference

If the user has the `cli-anything-fiji` CLI installed, you can use it to perform operations described in this skill. See `references/cli-commands.md` for the full command reference and mapping to best practices.

Key CLI commands:
- `cli-anything-fiji process add set_scale -p distance=100 known=10 unit=um` - calibrate image
- `cli-anything-fiji process add add_scale_bar -p width=10 color=White` - add scale bar
- `cli-anything-fiji process add apply_lut -p lut=Fire` - apply LUT
- `cli-anything-fiji process add set_display_range -p min_val=100 max_val=4000` - set B&C range
- `cli-anything-fiji channel merge img1.tif img2.tif -c Green,Magenta -o merged.tif` - merge channels
- `cli-anything-fiji figure montage a.tif b.tif c.tif d.tif -c 2 -r 2 -o figure.tif` - assemble figure
- `cli-anything-fiji figure presets` - list journal presets
```

- [ ] **Step 3: Commit**

```bash
git add /Users/urkodelcastillo/.claude/skills/fiji-best-practices/
git commit -m "feat: add CLI command reference to fiji-best-practices skill"
```

---

## Phase 7: Final Verification

### Task 9: Full regression test and README update

**Files:**
- Modify: `cli_anything/fiji/README.md`

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest cli_anything/fiji/tests/ -v -s`
Expected: All tests PASS (should be 72 original + new tests)

- [ ] **Step 2: Update README.md command table**

Add new command groups to the table:
```markdown
| `channel`  | Merge, split channels; list LUTs             |
| `figure`   | Assemble multi-panel figures, journal presets |
```

Add to Processing Operations section:
```markdown
New categories: `adjust` (scale bar, LUT, display range, calibration bar)
```

- [ ] **Step 3: Commit**

```bash
git add cli_anything/fiji/README.md
git commit -m "docs: update README with new channel and figure commands"
```

- [ ] **Step 4: Run CLI help to verify everything wired up**

Run: `cli-anything-fiji --help`
Expected: Shows project, image, process, roi, measure, macro, export, channel, figure, backend, session

Run: `cli-anything-fiji channel --help`
Expected: Shows luts, merge, split

Run: `cli-anything-fiji figure --help`
Expected: Shows presets, preset-info, montage

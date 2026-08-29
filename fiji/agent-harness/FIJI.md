# Fiji CLI — Software-Specific SOP

## Software Overview

**Fiji** (Fiji Is Just ImageJ) is the standard open-source platform for biological
and scientific image analysis. It bundles ImageJ2 with hundreds of plugins for
segmentation, tracking, registration, and measurement.

## Backend Engine

- **Executable**: `ImageJ-macosx` / `ImageJ-linux64` (platform-specific launchers)
- **Headless mode**: `--headless -macro script.ijm`
- **Scripting**: ImageJ macro language (`.ijm`), Jython, Groovy, BeanShell, JavaScript
- **Java runtime**: Bundled Zulu JDK 8

## Key Capabilities (Headless)

| Operation | Method |
|-----------|--------|
| Image I/O | Bio-Formats (500+ formats) |
| Filtering | Gaussian, median, unsharp mask, background subtraction |
| Thresholding | Auto (Otsu, etc.), manual, local adaptive |
| Morphology | Erode, dilate, open, close, skeletonize, watershed |
| Measurement | Area, mean, integrated density, shape descriptors |
| Particle analysis | Analyze Particles with size/circularity filters |
| Z-projection | Max, average, sum intensity |
| Batch processing | Macro-driven file iteration |
| Type conversion | 8-bit, 16-bit, 32-bit, RGB |
| Spatial transforms | Scale, rotate, flip, crop |

## CLI Architecture

### Command Groups

- **project**: JSON-based project files tracking images, pipeline, ROIs, measurements
- **image**: Add/remove image references
- **process**: Build a processing pipeline (recorded as ImageJ macro commands)
- **roi**: Rectangle, oval, line, polygon, point ROIs
- **measure**: Configure measurements, run analysis commands
- **macro**: Store custom macros, generate batch macros
- **export**: Render through Fiji headless with processing applied
- **backend**: Direct Fiji invocation (run macro, run script, version)
- **session**: Undo/redo with deep-copy snapshots

### Data Flow

```
User adds images → Builds processing pipeline → Adds ROIs
    → Pipeline generates ImageJ macro
    → Export sends macro to Fiji headless
    → Fiji processes image and saves output
```

## Native Format

Project state: `.fiji-cli.json` (JSON)
Image processing: ImageJ macro language (`.ijm`)

## Required Dependencies

- **Fiji**: https://fiji.sc/ (hard dependency, not optional)
- **Python**: click, prompt-toolkit

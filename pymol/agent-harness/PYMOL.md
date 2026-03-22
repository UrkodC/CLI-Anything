# PyMOL: Project-Specific Analysis & SOP

## Architecture Summary

PyMOL is a molecular visualization system used for viewing and rendering 3D
structures of proteins, nucleic acids, and small molecules. It loads structures
from PDB, CIF, SDF, and MOL2 files and provides a rich set of molecular
representations and coloring schemes. The CLI uses a JSON state description
that can generate PyMOL scripts (`.pml` files) for actual rendering.

```
┌──────────────────────────────────────────┐
│              PyMOL GUI                   │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐  │
│  │ Viewport │ │ Sequence │ │ Props   │  │
│  └────┬─────┘ └────┬─────┘ └────┬────┘  │
│       │             │            │        │
│  ┌────┴─────────────┴────────────┴─────┐ │
│  │       pymol.cmd (Python API)        │ │
│  │  Full scripting access to all       │ │
│  │  structures, representations,       │ │
│  │  selections, and rendering          │ │
│  └─────────────────┬───────────────────┘ │
│                    │                      │
│  ┌─────────────────┴───────────────────┐ │
│  │     Rendering Modes                 │ │
│  │  Ray Tracing | OpenGL | POV-Ray     │ │
│  └─────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

## CLI Strategy: JSON State + .pml Script Generation

Since PyMOL sessions are binary (`.pse`), we maintain molecular scene state in
JSON and generate complete `.pml` scripts that PyMOL can execute:

```bash
pymol -cq generated_script.pml
```

### Core Domains

| Domain | Module | Key Operations |
|--------|--------|----------------|
| Project | `project.py` | Create, open, save, profiles, info |
| Structure | `structure.py` | Load, remove, rename, list molecular structures |
| Selection | `selection.py` | Create, modify, remove named atom selections |
| Representation | `representation.py` | Show/hide cartoon, sticks, surface, spheres, etc. |
| Coloring | `coloring.py` | Color by element, chain, spectrum, custom color |
| View | `view.py` | Camera position, zoom, orient, clip planes |
| Label | `label.py` | Add/remove atom/residue labels |
| Render | `render.py` | Ray tracing settings, resolution, output |
| Session | `session.py` | Undo/redo with deep-copy snapshots |

### Representation Registry

9 representations with full parameter validation:
- `cartoon`: Secondary structure ribbons (helices, sheets, loops)
- `sticks`: Bond-and-atom stick models with configurable radius
- `surface`: Solvent-accessible or molecular surface
- `spheres`: Van der Waals spheres with configurable scale
- `lines`: Thin wireframe bond lines
- `mesh`: Wireframe surface mesh
- `ribbon`: Flat ribbon trace through backbone
- `dots`: Dot surface representation
- `nb_spheres`: Non-bonded interaction spheres (waters, ions)

### Coloring Modes

- `element`: CPK coloring by atom type (C=green, O=red, N=blue, S=yellow)
- `chain`: Distinct color per chain identifier
- `ss`: Secondary structure (helix=red, sheet=yellow, loop=green)
- `b-factor`: Temperature factor gradient coloring
- `spectrum`: Rainbow spectrum along residue number or other property
- `custom`: User-specified color name or hex value

### Render Presets

7 presets covering ray tracing and output configurations:
- `ray_default`: Standard ray trace, 1024x768, white background
- `ray_high`: High-quality ray trace, 4096x3072, 300 DPI, antialiasing 2
- `ray_preview`: Fast ray trace, 512x384, reduced quality for quick preview
- `ray_transparent`: Ray trace with transparent background (RGBA PNG)
- `ray_publication`: 300 DPI, 4096x3072, white background, no shadows
- `opengl_default`: OpenGL direct capture, 1024x768
- `opengl_high`: OpenGL capture, 4096x3072, 4x FSAA

### Rendering Gap: Low Risk

PyMOL's Python API (`pymol.cmd`) and scripting language (`.pml`) provide
complete access to all visualization functionality. The generated scripts
configure the exact molecular scene described in JSON, then render. No
translation gap -- `.pml` commands map directly to `pymol.cmd` calls.

## Export: .pml Script Generation

The `render execute` command generates a complete `.pml` script:
1. Loads all molecular structures from specified file paths
2. Creates named selections for atoms, residues, chains
3. Configures representations (show/hide per selection)
4. Applies coloring schemes per selection
5. Adds labels to specified atoms or residues
6. Sets camera view, orientation, zoom, and clipping planes
7. Configures ray tracing settings and resolution
8. Renders to output file (PNG, PSE, or other format)

Generated scripts are validated as syntactically correct PyMOL commands in tests.

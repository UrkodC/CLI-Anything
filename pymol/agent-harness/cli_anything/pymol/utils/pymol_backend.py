"""PyMOL CLI - Backend for finding and running PyMOL."""

import os
import subprocess
import shutil
from typing import Optional, Dict, Any


def find_pymol() -> str:
    """Find PyMOL executable in PATH."""
    path = shutil.which("pymol")
    if path:
        return path
    # Check common locations
    common_paths = [
        "/usr/bin/pymol",
        "/usr/local/bin/pymol",
        "/opt/homebrew/bin/pymol",
        "/Applications/PyMOL.app/Contents/bin/pymol",
        "/Applications/PyMOL.app/Contents/MacOS/PyMOL",
        os.path.expanduser("~/miniconda3/bin/pymol"),
        os.path.expanduser("~/anaconda3/bin/pymol"),
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        "PyMOL not found. Install with: conda install -c conda-forge pymol-open-source"
    )


def get_version() -> str:
    """Get PyMOL version string."""
    import tempfile

    pymol_path = find_pymol()
    version_file = tempfile.mktemp(suffix=".txt")
    script_file = tempfile.mktemp(suffix=".pml")
    try:
        with open(script_file, "w") as f:
            f.write(
                f"python\n"
                f"v = cmd.get_version()\n"
                f"f = open({version_file!r}, 'w')\n"
                f"f.write(str(v[0]))\n"
                f"f.close()\n"
                f"python end\n"
                f"quit\n"
            )
        subprocess.run(
            [pymol_path, "-cq", script_file],
            capture_output=True, text=True, timeout=30,
        )
        with open(version_file) as f:
            version = f.read().strip()
        return f"PyMOL {version}"
    finally:
        for p in (script_file, version_file):
            try:
                os.unlink(p)
            except OSError:
                pass


def render_script(script_path: str, timeout: int = 300) -> Dict[str, Any]:
    """Run a PyMOL script file.

    Args:
        script_path: Path to the .pml script.
        timeout: Timeout in seconds.

    Returns:
        Dict with returncode, stdout, stderr.
    """
    pymol_path = find_pymol()
    result = subprocess.run(
        [pymol_path, "-cq", script_path],
        capture_output=True, text=True, timeout=timeout,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def render_scene_headless(
    script_content: str,
    output_path: str,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Render a scene headlessly using PyMOL.

    Args:
        script_content: PyMOL script content (.pml).
        output_path: Expected output file path.
        timeout: Timeout in seconds.

    Returns:
        Dict with output path, file size, and method.
    """
    import tempfile

    # Write script to temp file
    script_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(script_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".pml", dir=script_dir, delete=False
    ) as f:
        f.write(script_content)
        script_path = f.name

    try:
        result = render_script(script_path, timeout)

        if result["returncode"] != 0:
            raise RuntimeError(
                f"PyMOL render failed (exit {result['returncode']}):\n"
                f"{result['stderr'][-1000:]}"
            )

        if not os.path.exists(output_path):
            raise FileNotFoundError(
                f"Render output not found: {output_path}\n"
                f"stdout: {result['stdout'][-500:]}"
            )

        return {
            "output": output_path,
            "file_size": os.path.getsize(output_path),
            "method": "pymol-headless",
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

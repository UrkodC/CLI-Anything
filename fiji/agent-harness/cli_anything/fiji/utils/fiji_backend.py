"""Fiji backend — invoke Fiji/ImageJ in headless mode for image processing.

Uses Fiji's macro language execution in headless batch mode.

Requires: Fiji (https://fiji.sc/)
    Download from: https://fiji.sc/#download
    macOS: Copy Fiji.app to /Applications/
"""

import os
import platform
import shutil
import subprocess
import tempfile
from typing import Optional


def find_fiji() -> str:
    """Find the Fiji/ImageJ executable.

    Searches PATH first, then common installation locations.
    Raises RuntimeError if not found.
    """
    # Try PATH first
    for name in ("fiji", "ImageJ-macosx", "ImageJ-linux64", "ImageJ-win64.exe"):
        path = shutil.which(name)
        if path:
            return path

    # Platform-specific common locations
    system = platform.system()
    candidates = []

    if system == "Darwin":
        candidates = [
            "/Applications/Fiji.app/Contents/MacOS/ImageJ-macosx",
            os.path.expanduser("~/Applications/Fiji.app/Contents/MacOS/ImageJ-macosx"),
            "/Applications/Fiji.app/Contents/MacOS/ImageJ-macos-arm64",
            os.path.expanduser("~/Applications/Fiji.app/Contents/MacOS/ImageJ-macos-arm64"),
        ]
    elif system == "Linux":
        candidates = [
            "/opt/fiji/ImageJ-linux64",
            os.path.expanduser("~/Fiji.app/ImageJ-linux64"),
            "/usr/local/Fiji.app/ImageJ-linux64",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Fiji.app\ImageJ-win64.exe",
            os.path.expanduser(r"~\Fiji.app\ImageJ-win64.exe"),
        ]

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    raise RuntimeError(
        "Fiji/ImageJ is not installed or not found.\n"
        "Install Fiji from: https://fiji.sc/#download\n"
        "  macOS: Copy Fiji.app to /Applications/\n"
        "  Linux: Extract to /opt/fiji/ or ~/Fiji.app/\n"
        "  Windows: Extract to C:\\Fiji.app\\"
    )


def get_version() -> str:
    """Get the installed Fiji/ImageJ version string."""
    fiji = find_fiji()
    try:
        result = subprocess.run(
            [fiji, "--headless", "--run", "Dummy", ""],
            capture_output=True, text=True, timeout=30,
            env=_get_env(),
        )
        # Version info is typically in stderr
        for line in result.stderr.split("\n"):
            if "ImageJ" in line or "Fiji" in line:
                return line.strip()
        return f"Fiji at {fiji}"
    except (subprocess.TimeoutExpired, OSError):
        return f"Fiji at {fiji} (version check failed)"


def run_macro(
    macro_code: str,
    timeout: int = 300,
    args: Optional[str] = None,
) -> dict:
    """Execute an ImageJ macro in Fiji headless mode.

    Args:
        macro_code: ImageJ macro language code.
        timeout: Maximum seconds to wait.
        args: Optional arguments passed to the macro.

    Returns:
        Dict with stdout, stderr, return code, and macro path.
    """
    fiji = find_fiji()

    # Append force-exit to prevent Fiji from hanging after macro completion
    if 'System.exit' not in macro_code:
        macro_code = macro_code.rstrip() + '\neval("script", "System.exit(0);");\n'

    # Write macro to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ijm", delete=False, prefix="fiji_cli_"
    ) as f:
        f.write(macro_code)
        macro_path = f.name

    try:
        cmd = [fiji, "--headless", "-macro", macro_path]
        if args:
            cmd.append(args)

        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            env=_get_env(),
        )

        return {
            "command": " ".join(cmd),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "macro_path": macro_path,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Fiji macro timed out after {timeout}s.\n"
            f"Macro file: {macro_path}"
        )
    finally:
        try:
            os.unlink(macro_path)
        except OSError:
            pass


def run_script(
    script_path: str,
    language: str = "auto",
    timeout: int = 300,
) -> dict:
    """Execute a script file in Fiji headless mode.

    Args:
        script_path: Path to the script file.
        language: Script language (auto-detected from extension if 'auto').
        timeout: Maximum seconds to wait.
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script file not found: {script_path}")

    fiji = find_fiji()
    abs_script = os.path.abspath(script_path)
    ext = os.path.splitext(abs_script)[1].lower()

    # .ijm files use -macro flag; other scripts use --run
    if ext == ".ijm":
        cmd = [fiji, "--headless", "-macro", abs_script]
    else:
        cmd = [fiji, "--headless", "--run", abs_script]

    result = subprocess.run(
        cmd,
        capture_output=True, text=True,
        timeout=timeout,
        env=_get_env(),
    )

    return {
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def process_image(
    input_path: str,
    output_path: str,
    macro_code: str = "",
    output_format: str = "tiff",
    timeout: int = 300,
) -> dict:
    """Load an image, apply macro processing, and save.

    Args:
        input_path: Path to input image.
        output_path: Path for output image.
        macro_code: ImageJ macro code to apply (optional).
        output_format: Output format (tiff, png, jpeg).
        timeout: Max seconds.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    abs_input = os.path.abspath(input_path)
    abs_output = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    format_map = {
        "tiff": "Tiff", "tif": "Tiff",
        "png": "PNG",
        "jpeg": "Jpeg", "jpg": "Jpeg",
        "bmp": "BMP",
    }
    save_format = format_map.get(output_format.lower(), "Tiff")

    macro = 'setBatchMode(true);\n'
    macro += f'open("{abs_input}");\n'
    if macro_code:
        macro += macro_code + "\n"
    macro += f'saveAs("{save_format}", "{abs_output}");\n'
    macro += 'close();\n'
    macro += 'setBatchMode(false);\n'

    result = run_macro(macro, timeout=timeout)

    if not os.path.exists(abs_output):
        raise RuntimeError(
            f"Fiji processing produced no output file.\n"
            f"  Expected: {abs_output}\n"
            f"  stderr: {result.get('stderr', '')[-500:]}\n"
            f"  stdout: {result.get('stdout', '')[-500:]}"
        )

    return {
        "output": abs_output,
        "format": output_format,
        "method": "fiji-headless",
        "file_size": os.path.getsize(abs_output),
    }


def create_test_image(
    output_path: str,
    width: int = 256,
    height: int = 256,
    image_type: str = "8-bit",
    fill: str = "noise",
    timeout: int = 120,
) -> dict:
    """Create a test image using Fiji.

    Args:
        output_path: Path for the output image.
        width: Image width.
        height: Image height.
        image_type: ImageJ image type.
        fill: Fill type (noise, ramp, black, white).
    """
    abs_output = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    fill_map = {
        "noise": 'run("Add Specified Noise...", "standard=50");',
        "ramp": 'for (y=0; y<getHeight(); y++) { for (x=0; x<getWidth(); x++) { setPixel(x, y, round(x*255/getWidth())); } }',
        "black": '',
        "white": 'run("Select All"); setForegroundColor(255, 255, 255); run("Fill", "slice"); run("Select None");',
    }
    fill_cmd = fill_map.get(fill, fill_map["noise"])

    type_map = {
        "8-bit": "8-bit Black",
        "16-bit": "16-bit Black",
        "32-bit": "32-bit Black",
        "RGB": "RGB Black",
    }
    new_type = type_map.get(image_type, "8-bit Black")

    macro = f'''setBatchMode(true);
newImage("test", "{new_type}", {width}, {height}, 1);
{fill_cmd}
saveAs("Tiff", "{abs_output}");
close();
setBatchMode(false);
'''

    result = run_macro(macro.strip(), timeout=timeout)

    if not os.path.exists(abs_output):
        raise RuntimeError(
            f"Fiji failed to create test image.\n"
            f"  stderr: {result.get('stderr', '')[-500:]}"
        )

    return {
        "output": abs_output,
        "width": width,
        "height": height,
        "image_type": image_type,
        "file_size": os.path.getsize(abs_output),
    }


def _get_env() -> dict:
    """Get environment for Fiji subprocess, ensuring Java is available."""
    env = os.environ.copy()
    # Fiji bundles its own Java, so we typically don't need to set JAVA_HOME.
    # But if the system Java is interfering, we can force Fiji's bundled Java.
    return env

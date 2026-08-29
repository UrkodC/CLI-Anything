"""R backend — invoke Rscript for statistical computing and plotting.

Uses Rscript in non-interactive mode for expressions, scripts, and plot rendering.

Requires: R (https://www.r-project.org/)
    Install from: https://cloud.r-project.org/
    macOS: brew install r  OR  download from https://cran.r-project.org/
    Linux: sudo apt install r-base  OR  sudo dnf install R
    Windows: Download installer from https://cran.r-project.org/
"""

import os
import platform
import shutil
import subprocess
import tempfile
from typing import Optional


def find_r() -> str:
    """Find the Rscript executable.

    Searches PATH first, then common installation locations.
    Raises RuntimeError if not found.
    """
    # Try PATH first
    path = shutil.which("Rscript")
    if path:
        return path

    # Platform-specific common locations
    system = platform.system()
    candidates = []

    if system == "Darwin":
        candidates = [
            "/usr/local/bin/Rscript",
            "/opt/homebrew/bin/Rscript",
            "/Library/Frameworks/R.framework/Resources/bin/Rscript",
        ]
    elif system == "Linux":
        candidates = [
            "/usr/bin/Rscript",
            "/usr/local/bin/Rscript",
            "/opt/R/bin/Rscript",
        ]
    elif system == "Windows":
        # Check common Windows R installation paths
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)",
                                            r"C:\Program Files (x86)")
        for base in (program_files, program_files_x86):
            r_dir = os.path.join(base, "R")
            if os.path.isdir(r_dir):
                # Find latest version directory
                try:
                    versions = sorted(os.listdir(r_dir), reverse=True)
                    for v in versions:
                        candidate = os.path.join(r_dir, v, "bin", "Rscript.exe")
                        candidates.append(candidate)
                except OSError:
                    pass

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    raise RuntimeError(
        "Rscript is not installed or not found.\n"
        "Install R from: https://cloud.r-project.org/\n"
        "  macOS:   brew install r\n"
        "  Linux:   sudo apt install r-base\n"
        "  Windows: Download from https://cran.r-project.org/\n"
        "After installing, ensure Rscript is on your PATH."
    )


def get_version() -> str:
    """Get the installed R version string."""
    rscript = find_r()
    try:
        result = subprocess.run(
            [rscript, "--version"],
            capture_output=True, text=True, timeout=30,
            env=_get_env(),
        )
        # R prints version info to stderr
        output = result.stderr.strip() or result.stdout.strip()
        for line in output.split("\n"):
            if "version" in line.lower() or "R scripting" in line:
                return line.strip()
        return output.split("\n")[0].strip() if output else f"Rscript at {rscript}"
    except (subprocess.TimeoutExpired, OSError):
        return f"Rscript at {rscript} (version check failed)"


def run_expression(expr: str, timeout: int = 60) -> dict:
    """Execute an R expression via Rscript.

    Args:
        expr: R expression to evaluate.
        timeout: Maximum seconds to wait.

    Returns:
        Dict with returncode, stdout, and stderr.
    """
    rscript = find_r()

    cmd = [rscript, "--no-save", "--no-restore", "-e", expr]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            env=_get_env(),
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"R expression timed out after {timeout}s.\n"
            f"Expression: {expr[:200]}"
        )


def run_script(script_path: str, timeout: int = 300) -> dict:
    """Execute an R script file via Rscript.

    Args:
        script_path: Path to the .R script file.
        timeout: Maximum seconds to wait.

    Returns:
        Dict with returncode, stdout, stderr, and script_path.
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script file not found: {script_path}")

    rscript = find_r()
    abs_script = os.path.abspath(script_path)

    cmd = [rscript, "--no-save", "--no-restore", abs_script]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            env=_get_env(),
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "script_path": abs_script,
        }
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"R script timed out after {timeout}s.\n"
            f"Script: {abs_script}"
        )


def render_plot(
    script_code: str,
    output_path: str,
    format: str = "png",
    width: int = 800,
    height: int = 600,
    dpi: int = 300,
    timeout: int = 120,
) -> dict:
    """Execute R plotting code and save the output to a file.

    Wraps the provided script_code with appropriate graphics device
    open/close calls, writes to a temp .R file, and executes it.

    Args:
        script_code: R code that produces a plot.
        output_path: Path for the output image.
        format: Output format (png, pdf, svg, jpeg, tiff).
        width: Image width in pixels (or inches * 72 for pdf).
        height: Image height in pixels (or inches * 72 for pdf).
        dpi: Resolution in DPI (for raster formats).
        timeout: Maximum seconds to wait.

    Returns:
        Dict with output path, format, and file_size.
    """
    abs_output = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    # Escape backslashes for R string (Windows paths)
    r_path = abs_output.replace("\\", "/")

    # Build device open/close wrappers
    format_lower = format.lower()
    if format_lower == "png":
        device_open = f'png("{r_path}", width={width}, height={height}, res={dpi})'
    elif format_lower == "pdf":
        # PDF uses inches; convert pixels to inches
        w_in = width / 72
        h_in = height / 72
        device_open = f'pdf("{r_path}", width={w_in}, height={h_in})'
    elif format_lower == "svg":
        w_in = width / 72
        h_in = height / 72
        device_open = f'svg("{r_path}", width={w_in}, height={h_in})'
    elif format_lower in ("jpeg", "jpg"):
        device_open = f'jpeg("{r_path}", width={width}, height={height}, res={dpi})'
        format_lower = "jpeg"
    elif format_lower in ("tiff", "tif"):
        device_open = f'tiff("{r_path}", width={width}, height={height}, res={dpi})'
        format_lower = "tiff"
    else:
        raise ValueError(
            f"Unsupported format: {format}. "
            f"Supported formats: png, pdf, svg, jpeg, tiff"
        )

    full_script = f"""{device_open}
{script_code}
dev.off()
"""

    # Write to temp file and execute
    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".R", delete=False, prefix="r_cli_plot_"
        ) as f:
            f.write(full_script)
            tmp_file = f.name

        result = run_script(tmp_file, timeout=timeout)

        if not os.path.exists(abs_output):
            raise RuntimeError(
                f"R plot rendering produced no output file.\n"
                f"  Expected: {abs_output}\n"
                f"  returncode: {result['returncode']}\n"
                f"  stderr: {result.get('stderr', '')[-500:]}\n"
                f"  stdout: {result.get('stdout', '')[-500:]}"
            )

        return {
            "output": abs_output,
            "format": format_lower,
            "file_size": os.path.getsize(abs_output),
        }
    finally:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


def install_package(
    package_name: str,
    repos: str = "https://cloud.r-project.org",
    timeout: int = 300,
) -> dict:
    """Install an R package from CRAN.

    Args:
        package_name: Name of the R package to install.
        repos: CRAN mirror URL.
        timeout: Maximum seconds to wait.

    Returns:
        Dict with success status and output details.
    """
    expr = f'install.packages("{package_name}", repos="{repos}")'

    try:
        result = run_expression(expr, timeout=timeout)

        # Check for common failure indicators
        combined = result["stdout"] + result["stderr"]
        failed = (
            result["returncode"] != 0
            or "installation of package" in combined
            and "had non-zero exit status" in combined
            or f"package '{package_name}' is not available" in combined
        )

        return {
            "package": package_name,
            "success": not failed,
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }
    except RuntimeError as e:
        return {
            "package": package_name,
            "success": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
        }


def list_packages(timeout: int = 60) -> list:
    """List all installed R packages.

    Args:
        timeout: Maximum seconds to wait.

    Returns:
        List of installed package name strings.
    """
    expr = 'cat(installed.packages()[,"Package"], sep="\\n")'
    result = run_expression(expr, timeout=timeout)

    if result["returncode"] != 0:
        raise RuntimeError(
            f"Failed to list R packages.\n"
            f"  stderr: {result['stderr'][-500:]}"
        )

    packages = [
        line.strip()
        for line in result["stdout"].split("\n")
        if line.strip()
    ]
    return packages


def check_package(package_name: str, timeout: int = 30) -> bool:
    """Check if an R package is installed and loadable.

    Args:
        package_name: Name of the R package to check.
        timeout: Maximum seconds to wait.

    Returns:
        True if the package is available, False otherwise.
    """
    expr = f'cat(requireNamespace("{package_name}", quietly=TRUE))'
    result = run_expression(expr, timeout=timeout)

    return result["returncode"] == 0 and "TRUE" in result["stdout"]


def _get_env() -> dict:
    """Get environment for R subprocess."""
    env = os.environ.copy()
    return env

"""Small Python wrapper around the bundled MoosasEnergy executable."""

from __future__ import annotations

import subprocess
import platform
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parent
BIN_DIR = MODULE_DIR / "bin"
EXECUTABLE_BY_SYSTEM = {
    "Windows": BIN_DIR / "MoosasEnergy-legacy.exe",
    "Linux": BIN_DIR / "MoosasEnergy-legacy",
}


def default_energy_executable() -> Path:
    """Return the preserved legacy .i/.o engine for the current OS."""
    system = platform.system()
    try:
        return EXECUTABLE_BY_SYSTEM[system]
    except KeyError as exc:
        raise RuntimeError(f"Unsupported OS for bundled MoosasEnergy executable: {system}") from exc


DEFAULT_EXECUTABLE = default_energy_executable()


def ensure_local_energy_binary(executable_path: str | Path | None = None) -> Path:
    """Resolve the MoosasEnergy executable and fail early when it is missing."""
    candidate = Path(executable_path).resolve() if executable_path else default_energy_executable().resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"MoosasEnergy executable not found: {candidate}")
    if platform.system() != "Windows":
        candidate.chmod(candidate.stat().st_mode | 0o755)
    return candidate


def build_energy_command(
    input_path: str | Path,
    weather_path: str | Path,
    *,
    output_path: str | Path,
    building_type: int = 1,
    latitude_radians: float,
    altitude_meters: float,
    shape_factor: float,
    export_daily: bool = True,
    export_hourly: bool = True,
    export_by_zone: bool = True,
    schedule_path: str | Path | None = None,
    executable_path: str | Path | None = None,
) -> list[str]:
    """Build a MoosasEnergy command line from explicit simulation inputs."""
    executable = ensure_local_energy_binary(executable_path)
    command = [
        str(executable),
        "-w",
        str(Path(weather_path).resolve()),
        "-t",
        str(int(building_type)),
        "-l",
        str(float(latitude_radians)),
        "-a",
        str(float(altitude_meters)),
        "-s",
        str(float(shape_factor)),
        "-d",
        "1" if export_daily else "0",
        "-r",
        "1" if export_hourly else "0",
        "-z",
        "1" if export_by_zone else "0",
        "-dc",
        "1",  # Thermal decomposition is required for AC waste-heat feedback.
        "-o",
        str(Path(output_path).resolve()),
    ]
    if schedule_path is not None:
        command.extend(["-sch", str(Path(schedule_path).resolve())])
    command.append(str(Path(input_path).resolve()))
    return command


def run_energy_simulation(
    input_path: str | Path,
    weather_path: str | Path,
    *,
    output_path: str | Path,
    building_type: int = 1,
    latitude_radians: float,
    altitude_meters: float,
    shape_factor: float,
    export_daily: bool = True,
    export_hourly: bool = True,
    export_by_zone: bool = True,
    schedule_path: str | Path | None = None,
    executable_path: str | Path | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: float | None = None,
) -> Path:
    """Run MoosasEnergy and return the generated output file path."""
    command = build_energy_command(
        input_path,
        weather_path,
        output_path=output_path,
        building_type=building_type,
        latitude_radians=latitude_radians,
        altitude_meters=altitude_meters,
        shape_factor=shape_factor,
        export_daily=export_daily,
        export_hourly=export_hourly,
        export_by_zone=export_by_zone,
        schedule_path=schedule_path,
        executable_path=executable_path,
    )
    result = subprocess.run(
        command,
        cwd=str(Path(cwd).resolve()) if cwd else str(BIN_DIR.resolve()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"MoosasEnergy failed with returncode={result.returncode}, "
            f"stdout={result.stdout.strip()}, stderr={result.stderr.strip()}"
        )

    output_file = Path(output_path).resolve()
    if not output_file.exists():
        raise FileNotFoundError(f"MoosasEnergy output file was not created: {output_file}")
    return output_file


def parse_output_sections(output_path: str | Path) -> dict[str, list[str]]:
    """Parse MoosasEnergy sectioned output into a dictionary keyed by section title."""
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for raw_line in Path(output_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == ";":
            if current_section is not None:
                sections[current_section] = current_lines[:]
            current_section = None
            current_lines = []
            continue
        if line.startswith("!"):
            if line.endswith(":"):
                if current_section is not None:
                    sections[current_section] = current_lines[:]
                current_section = line
                current_lines = []
            continue
        if current_section is not None:
            current_lines.append(line)

    if current_section is not None:
        sections[current_section] = current_lines[:]
    return sections


if __name__ == "__main__":
    print({"defaultExecutable": str(DEFAULT_EXECUTABLE.resolve()), "exists": DEFAULT_EXECUTABLE.exists()})

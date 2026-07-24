"""Download, build, and locate the whisper.cpp CLI used by this project."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import portalocker


WHISPER_CPP_VERSION = "v1.9.1"
WHISPER_MODEL = "base"
REPOSITORY_ARCHIVE_URL = (
    f"https://github.com/ggml-org/whisper.cpp/archive/refs/tags/"
    f"{WHISPER_CPP_VERSION}.zip"
)
MODEL_URL = (
    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
    f"ggml-{WHISPER_MODEL}.bin"
)

PROJECT_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_DIR / ".runtime"
SOURCE_DIR = RUNTIME_DIR / f"whisper.cpp-{WHISPER_CPP_VERSION.removeprefix('v')}"
BUILD_DIR = SOURCE_DIR / "build"
CLI_NAME = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
CLI_PATH = BUILD_DIR / "bin" / CLI_NAME
MODEL_PATH = SOURCE_DIR / "models" / f"ggml-{WHISPER_MODEL}.bin"
LOCK_PATH = RUNTIME_DIR / "whisper-setup.lock"


def _download(url: str, destination: Path, description: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    print(f"⬇️ 正在下載 {description}...")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _extract_source(archive: Path) -> None:
    temporary_dir = RUNTIME_DIR / ".whisper-source"
    if temporary_dir.exists():
        shutil.rmtree(temporary_dir)
    temporary_dir.mkdir(parents=True)

    with zipfile.ZipFile(archive) as source_zip:
        for member in source_zip.infolist():
            target = (temporary_dir / member.filename).resolve()
            if temporary_dir.resolve() not in target.parents and target != temporary_dir.resolve():
                raise RuntimeError("whisper.cpp 壓縮檔包含不安全的路徑")
        source_zip.extractall(temporary_dir)

    extracted = temporary_dir / SOURCE_DIR.name
    if not extracted.is_dir():
        raise RuntimeError("無法辨識下載的 whisper.cpp 原始碼結構")
    if SOURCE_DIR.exists():
        shutil.rmtree(SOURCE_DIR)
    extracted.replace(SOURCE_DIR)
    shutil.rmtree(temporary_dir)


def _build_cli() -> None:
    cmake = shutil.which("cmake")
    if not cmake:
        raise RuntimeError(
            "自動建置 whisper.cpp 需要 CMake，但系統找不到 cmake。"
            "macOS 可安裝 Xcode Command Line Tools 與 CMake；"
            "Ubuntu/Debian 可安裝 cmake、build-essential。"
        )

    print(f"🔨 正在建置 whisper.cpp {WHISPER_CPP_VERSION}...")
    subprocess.run(
        [cmake, "-S", str(SOURCE_DIR), "-B", str(BUILD_DIR),
         "-DCMAKE_BUILD_TYPE=Release"],
        check=True,
    )
    build_command = [
        cmake, "--build", str(BUILD_DIR), "--config", "Release",
        "--parallel", str(os.cpu_count() or 2),
    ]
    subprocess.run(build_command, check=True)

    # Multi-config CMake generators place executables under bin/Release.
    release_cli = BUILD_DIR / "bin" / "Release" / CLI_NAME
    if not CLI_PATH.is_file() and release_cli.is_file():
        return
    if not CLI_PATH.is_file():
        raise RuntimeError(f"whisper.cpp 建置完成，但找不到 {CLI_NAME}")


def _resolved_cli_path() -> Path:
    if CLI_PATH.is_file():
        return CLI_PATH
    release_cli = BUILD_DIR / "bin" / "Release" / CLI_NAME
    return release_cli


def ensure_whisper_cpp() -> tuple[str, str]:
    """Return usable CLI/model paths, installing them on first use."""
    custom_cli = os.getenv("WHISPER_CLI_PATH")
    custom_model = os.getenv("WHISPER_MODEL_PATH")
    if custom_cli or custom_model:
        cli = Path(custom_cli) if custom_cli else _resolved_cli_path()
        model = Path(custom_model) if custom_model else MODEL_PATH
        if not cli.is_file():
            raise FileNotFoundError(f"找不到 WHISPER_CLI_PATH 指定的檔案: {cli}")
        if not model.is_file():
            raise FileNotFoundError(f"找不到 WHISPER_MODEL_PATH 指定的檔案: {model}")
        return str(cli), str(model)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(LOCK_PATH), timeout=900):
        cli = _resolved_cli_path()
        if not cli.is_file():
            archive = RUNTIME_DIR / f"whisper.cpp-{WHISPER_CPP_VERSION}.zip"
            if not SOURCE_DIR.is_dir():
                _download(REPOSITORY_ARCHIVE_URL, archive, "whisper.cpp 原始碼")
                _extract_source(archive)
                archive.unlink(missing_ok=True)
            _build_cli()
            cli = _resolved_cli_path()

        if not MODEL_PATH.is_file():
            _download(MODEL_URL, MODEL_PATH, f"Whisper {WHISPER_MODEL} 模型")

    print(f"✅ whisper.cpp 已就緒: {cli}")
    return str(cli), str(MODEL_PATH)


if __name__ == "__main__":
    try:
        ensure_whisper_cpp()
    except Exception as error:
        print(f"❌ whisper.cpp 準備失敗: {error}", file=sys.stderr)
        raise SystemExit(1)

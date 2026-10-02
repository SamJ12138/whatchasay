"""Batch 6 (S1, S4): requirements.txt is enough to import the app and run the
fast suite. The full proof (fresh venv + pip install + fast suite) is
scripts/clean_install_check.py, run in CI; these are its fast proxies."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# import name -> distribution name in requirements*.txt (only where they differ)
DIST = {
    "sherpa_onnx": "sherpa-onnx", "yaml": "pyyaml", "dotenv": "python-dotenv", "multipart": "python-multipart",
    "huggingface_hub": "huggingface_hub", "pydantic_settings": "pydantic-settings", "pytest_asyncio": "pytest-asyncio",
    "sentencepiece": "sentencepiece", "soundfile": "soundfile", "starlette": "fastapi", "anyio": "fastapi",
}


def requirement_names(*files):
    names = set()
    for f in files:
        for line in (ROOT / f).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
                line = line.split(sep, 1)[0]
            names.add(line.strip().lower().replace("_", "-"))
    return names


def module_level_imports(path: Path):
    """Top-level names imported at module level, outside try/except blocks
    (optional imports are guarded or done inside functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_app_imports_without_ollama_or_llama_cpp():
    code = ("import sys; sys.modules['ollama'] = None; sys.modules['llama_cpp'] = None; "
            "sys.path.insert(0, '.'); import app.main; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-1500:]


def test_every_module_level_third_party_import_is_declared():
    declared = requirement_names("requirements.txt")
    stdlib = set(sys.stdlib_module_names)
    missing = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        for mod in module_level_imports(path):
            if mod in stdlib or mod == "app" or mod.startswith("_"):
                continue
            dist = DIST.get(mod, mod).lower().replace("_", "-")
            if dist not in declared:
                missing.setdefault(mod, []).append(str(path.relative_to(ROOT)))
    assert not missing, f"imported at module level but not in requirements.txt: {missing}"


def test_scripts_need_nothing_undeclared():
    declared = requirement_names("requirements.txt", "requirements-dev.txt")
    stdlib = set(sys.stdlib_module_names)
    for path in sorted((ROOT / "scripts").glob("*.py")):
        for mod in module_level_imports(path):
            if mod in stdlib or mod == "app":
                continue
            assert DIST.get(mod, mod).lower().replace("_", "-") in declared, f"{path.name} imports {mod}"


def test_requirements_are_split_cpu_gpu_dev():
    runtime = requirement_names("requirements.txt")
    dev = requirement_names("requirements-dev.txt")
    gpu = requirement_names("requirements-gpu.txt")
    assert not {n for n in runtime if n.startswith("pytest")}, "test tools belong in requirements-dev.txt"
    assert {"pytest", "pytest-asyncio", "pytest-timeout", "pytest-cov"} <= dev
    assert "torch" in gpu and "torch" not in runtime, "CUDA extras belong in requirements-gpu.txt"
    assert "ollama" not in runtime and "llama-cpp-python" not in runtime

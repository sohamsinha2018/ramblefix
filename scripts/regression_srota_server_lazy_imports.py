from __future__ import annotations

import builtins
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BLOCKED_PREFIXES = (
    "faster_whisper",
    "funasr",
    "mlx_qwen3_asr",
    "parakeet_mlx",
    "qwen_asr",
    "torch",
    "transformers",
)


real_import = builtins.__import__


def guarded_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
    if name == "numpy" or any(name == prefix or name.startswith(prefix + ".") for prefix in BLOCKED_PREFIXES):
        raise AssertionError(f"srota_server startup imported optional runtime dependency: {name}")
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = guarded_import
try:
    import ramblefix.srota_server  # noqa: F401
finally:
    builtins.__import__ = real_import

print("regression_srota_server_lazy_imports passed")

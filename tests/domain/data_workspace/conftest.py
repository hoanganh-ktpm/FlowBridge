# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import sys
from pathlib import Path

# Independent service package; never shadow the backend's app module in test collection.
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "workspace_app",
    ROOT / "services/data-workspace-api/app/__init__.py",
    submodule_search_locations=[str(ROOT / "services/data-workspace-api/app")],
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

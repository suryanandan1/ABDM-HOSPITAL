"""LOINC suggestions from the existing test-name mapping project (LOINC_MAPPING_DIR/map.py).

map.py is imported as a library and never modified, exactly as its own cli.py does. Its fuzzy scores are
reported as the mapping confidence; suggestions are never confirmed automatically.
"""

import importlib.util
import sys
import threading

import pandas as pd

from app.config import settings

_lock = threading.Lock()
_state: dict = {}


class LoincUnavailable(Exception):
    pass


def _load() -> dict:
    with _lock:
        if _state:
            return _state
        path = settings.loinc_mapping_dir / "map.py"
        if not path.exists():
            raise LoincUnavailable(f"LOINC mapping project not found at {path} (set LOINC_MAPPING_DIR)")
        spec = importlib.util.spec_from_file_location("loinc_mapping", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["loinc_mapping"] = module  # map.py uses dataclasses, which look the module up
        spec.loader.exec_module(module)
        try:
            index = module.load_loinc(module.LOINC_PATH)
            # map.py keeps only normalised names; the official long names are read once for display and lookup.
            frame = pd.read_csv(module.LOINC_PATH, usecols=["LOINC_NUM", "LONG_COMMON_NAME", "STATUS"], dtype=str)
        except Exception as exc:
            raise LoincUnavailable(f"LOINC dataset could not be loaded: {exc}") from exc
        frame = frame.fillna("")
        _state.update(module=module, index=index,
                      terms={c: (n, s) for c, n, s in zip(frame.LOINC_NUM, frame.LONG_COMMON_NAME, frame.STATUS)})
        return _state


def lookup(code: str) -> dict | None:
    """Official long common name and status of a LOINC code, or None if the code does not exist."""
    term = _load()["terms"].get(code.strip())
    return {"code": code.strip(), "display": term[0], "status": term[1]} if term else None


def suggest(test_name: str) -> dict | None:
    """Best LOINC match for a free-text test name, or None below map.py's threshold."""
    state = _load()
    match = state["module"].match_loinc(state["index"], state["module"].normalize(test_name))
    if match is None:
        return None
    matched_term, code, score = match
    return {"code": code, "display": state["terms"][code][0], "matched_term": matched_term,
            "confidence": round(score / 100, 2)}

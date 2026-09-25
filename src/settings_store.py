"""Persistent settings and named presets.

The parameter panel has ~50 controls, and before this every launch reset all
of them. Rather than a hand-written save/load line per control — which goes
stale the first time a control is added — state is read generically off the
panel's attributes: every spin box, check box and combo box it holds is
saved under its attribute name. A control added later is persisted with no
change here; a control removed later is simply ignored when old state loads.

Values go through the widgets' own setters, so a stored value outside a
control's range is clamped by the control, never trusted. That matters
because the ranges are the perceptual safety caps (see CLAUDE.md): a value
saved under an older, wider cap must not come back past the current one.
"""

import json
import logging
from typing import Dict, List, Optional

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

log = logging.getLogger(__name__)

ORGANIZATION = "VideoUniqualizer"
APPLICATION = "Video Uniqualizer"
# Bump when a saved value would mean something different under new code; a
# mismatched state is discarded instead of half-applied.
SCHEMA_VERSION = 1

_PRESET_GROUP = "presets"


def settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def widget_state(owner) -> Dict[str, object]:
    """Values of every input control held as an attribute of ``owner``."""
    state: Dict[str, object] = {}
    for name, obj in vars(owner).items():
        if isinstance(obj, QCheckBox):
            state[name] = obj.isChecked()
        elif isinstance(obj, (QSpinBox, QDoubleSpinBox)):
            state[name] = obj.value()
        elif isinstance(obj, QComboBox):
            data = obj.currentData()
            state[name] = {"data": data} if data is not None else {"text": obj.currentText()}
    return state


def apply_widget_state(owner, state: Dict[str, object], order: List[str] = ()) -> None:
    """Restore values; unknown names and unusable values are skipped.

    ``order`` names controls to apply first, for controls whose change
    handlers rewrite other controls (the performance profile rewrites the
    encoder, preset and bitrate): applying them first lets the saved values of
    the controls they rewrite win.
    """
    names = list(order) + [n for n in state if n not in order]
    for name in names:
        if name not in state:
            continue
        obj = getattr(owner, name, None)
        value = state[name]
        try:
            if isinstance(obj, QCheckBox) and isinstance(value, bool):
                obj.setChecked(value)
            elif isinstance(obj, QSpinBox) and isinstance(value, (int, float)):
                obj.setValue(int(value))
            elif isinstance(obj, QDoubleSpinBox) and isinstance(value, (int, float)):
                obj.setValue(float(value))
            elif isinstance(obj, QComboBox) and isinstance(value, dict):
                idx = (obj.findData(value["data"]) if "data" in value
                       else obj.findText(str(value.get("text", ""))))
                # A disabled item is an encoder this Mac's ffmpeg lacks.
                item = obj.model().item(idx) if idx >= 0 and hasattr(obj.model(), "item") else None
                if idx >= 0 and (item is None or item.isEnabled()):
                    obj.setCurrentIndex(idx)
        except (TypeError, ValueError, KeyError) as exc:
            log.warning("could not restore %s=%r: %s", name, value, exc)


def _dump(state: Dict[str, object]) -> str:
    return json.dumps({"schema": SCHEMA_VERSION, "state": state})


def _load(raw) -> Optional[Dict[str, object]]:
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("discarding unreadable saved settings")
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        log.info("discarding saved settings from schema %r", doc.get("schema") if isinstance(doc, dict) else None)
        return None
    state = doc.get("state")
    return state if isinstance(state, dict) else None


def save_last_state(state: Dict[str, object]) -> None:
    settings().setValue("params", _dump(state))


def load_last_state() -> Optional[Dict[str, object]]:
    return _load(settings().value("params"))


def list_presets() -> List[str]:
    s = settings()
    s.beginGroup(_PRESET_GROUP)
    names = sorted(s.childKeys(), key=str.lower)
    s.endGroup()
    return names


def save_preset(name: str, state: Dict[str, object]) -> None:
    settings().setValue(f"{_PRESET_GROUP}/{name}", _dump(state))


def load_preset(name: str) -> Optional[Dict[str, object]]:
    return _load(settings().value(f"{_PRESET_GROUP}/{name}"))


def delete_preset(name: str) -> None:
    settings().remove(f"{_PRESET_GROUP}/{name}")

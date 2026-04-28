import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.signal_fusion import fuse_risk_levels, normalize_confidence_level


def test_normalize_confidence_level_defaults_to_medium_for_unknown_values():
    assert normalize_confidence_level(None) == "Medium"
    assert normalize_confidence_level("unclear") == "Medium"


def test_fuse_risk_levels_escalates_on_medium_medium_agreement():
    assert fuse_risk_levels("Medium", "Medium") == "High"


def test_fuse_risk_levels_keeps_medium_medium_when_semantic_confidence_is_low():
    assert fuse_risk_levels("Medium", "Medium", semantic_confidence="Low") == "Medium"


def test_fuse_risk_levels_bounds_semantic_only_escalation_without_merge_blocking_signal():
    assert fuse_risk_levels("Low", "High") == "Medium"


def test_fuse_risk_levels_treats_low_confidence_semantic_high_as_advisory():
    assert fuse_risk_levels("Low", "High", semantic_confidence="Low") == "Low"


def test_fuse_risk_levels_allows_full_semantic_escalation_when_merge_blocking():
    assert fuse_risk_levels("Low", "High", semantic_requires_escalation=True) == "High"


def test_fuse_risk_levels_keeps_deterministic_high_risk_when_inputs_differ():
    assert fuse_risk_levels("High", "Low") == "High"
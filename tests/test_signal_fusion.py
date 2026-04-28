import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.signal_fusion import fuse_risk_levels


def test_fuse_risk_levels_escalates_on_medium_medium_agreement():
    assert fuse_risk_levels("Medium", "Medium") == "High"


def test_fuse_risk_levels_bounds_semantic_only_escalation_without_merge_blocking_signal():
    assert fuse_risk_levels("Low", "High") == "Medium"


def test_fuse_risk_levels_allows_full_semantic_escalation_when_merge_blocking():
    assert fuse_risk_levels("Low", "High", semantic_requires_escalation=True) == "High"


def test_fuse_risk_levels_keeps_deterministic_high_risk_when_inputs_differ():
    assert fuse_risk_levels("High", "Low") == "High"
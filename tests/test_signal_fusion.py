import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.signal_fusion import fuse_risk_levels


def test_fuse_risk_levels_escalates_on_medium_medium_agreement():
    assert fuse_risk_levels("Medium", "Medium") == "High"


def test_fuse_risk_levels_keeps_existing_highest_risk_when_inputs_differ():
    assert fuse_risk_levels("Low", "High") == "High"
    assert fuse_risk_levels("High", "Low") == "High"
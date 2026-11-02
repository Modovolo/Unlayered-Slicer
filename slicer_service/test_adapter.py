import re
from pathlib import Path
import pytest
from adapter import settings, MAPPING

PROFILE = {"buildVolume":{"x":220,"y":220,"z":250},"nozzleDiameter":.4}
BASE = {"max_tilt":15,"max_print_height":100,"hotend_clearance":5,"gantry_height":50}

def test_mapped_options_exist_in_pinned_engine():
    source = (Path(__file__).parents[1]/"src/libslic3r/PrintConfig.cpp").read_text()
    keys = set(re.findall(r'this->add\("([^"]+)"', source))
    output = settings({"layer_height":.2,"nozzle_temp":240,"bed_temp":80,"filament_type":"PETG"}, PROFILE, {})
    assert set(output) <= keys
    assert set(MAPPING.values()) <= keys
    assert output["hot_plate_temp_initial_layer"] == "80"
    assert output["nozzle_temperature_initial_layer"] == "240"
    assert output["curr_bed_type"] == "High Temp Plate"

def test_advanced_features_require_acceptance_and_explicit_clearances():
    with pytest.raises(ValueError, match="acceptance"):
        settings({**BASE,"nonplanar_layers":True}, PROFILE, {})
    with pytest.raises(ValueError, match="maximum tilt"):
        settings({"nonplanar_layers":True}, PROFILE, {}, ["curved_tops"])
    output = settings({**BASE,"nonplanar_layers":True,"nonplanar_max_angle":12,"nonplanar_max_height":5}, PROFILE, {}, ["curved_tops"])
    assert output["nonplanar_layers"] == "1" and output["axis_selection"] == "virtual"

def test_cut_planes_enable_grouped_virtual_mode_and_unsupported_settings_fail():
    output = settings(BASE, PROFILE, {"objects":[{"cutPlanes":[{}]}]}, ["multiplanar"])
    assert output["print_sequence"] == "by group" and output["enable_nonplanar"] == "1"
    assert output["nonplanar_virtual_max_angle"] == "15"
    with pytest.raises(ValueError, match="Unsupported"):
        settings({"post_process":"bad"}, PROFILE, {})

def test_single_nozzle_temperature_is_applied_and_conflicts_are_rejected():
    output = settings({"nozzle_temps": [245]}, PROFILE, {})
    assert output["nozzle_temperature"] == "245" and output["nozzle_temperature_initial_layer"] == "245"
    for params in ({"nozzle_temps": [210, 240]}, {"nozzle_temps": [245], "nozzle_temp": 210}):
        with pytest.raises(ValueError, match="nozzle temperature"):
            settings(params, PROFILE, {})

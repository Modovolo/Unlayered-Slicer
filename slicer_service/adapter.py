"""Explicit Fleet -> Unlayered 0.1.9 settings mapping. No embedded 3MF scripts."""
import math

VERSION = "0.1.9"
ADVANCED = {"nonplanar_layers", "nonplanar_max_angle", "nonplanar_max_height", "max_tilt", "max_print_height", "hotend_clearance", "gantry_height"}
MAPPING = {
    "layer_height": "layer_height", "first_layer_height": "initial_layer_print_height",
    "line_width": "line_width", "infill_density": "sparse_infill_density", "infill_pattern": "sparse_infill_pattern",
    "wall_count": "wall_loops", "top_layers": "top_shell_layers", "bottom_layers": "bottom_shell_layers",
    "print_speed": "outer_wall_speed", "first_layer_speed": "initial_layer_speed", "travel_speed": "travel_speed",
    "nozzle_temp": "nozzle_temperature", "bed_temp": "hot_plate_temp", "enable_support": "enable_support",
    "support_angle": "support_threshold_angle", "support_pattern": "support_base_pattern",
    "brim_width": "brim_width", "filament_type": "filament_type", "min_fan_speed": "fan_min_speed",
    "max_fan_speed": "fan_max_speed", "bridge_fan_speed": "overhang_fan_speed", "disable_fan_first_layers": "close_fan_the_first_x_layers",
    "retract_length": "retraction_length", "retract_speed": "retraction_speed", "retract_lift": "z_hop",
    "nonplanar_layers": "nonplanar_layers", "nonplanar_max_angle": "nonplanar_max_angle", "nonplanar_max_height": "nonplanar_max_height",
}

def positive(value, name, maximum=5000):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"Explicit {name} between 0 and {maximum} is required")
    return value

def settings(params, profile, project, accepted_features=()):
    unknown = set(params) - set(MAPPING) - ADVANCED - {"adhesion_type", "nozzle_temps"}
    if unknown:
        raise ValueError("Unsupported Unlayered settings: " + ", ".join(sorted(unknown)))
    # 0.1.9 emits single-tool G-code. A multi-extruder printer is accepted only
    # when the user chose which extruder prints; the others stay cold.
    count = profile.get("extruderCount", 1)
    tool = profile.get("printExtruder")
    if tool is None and count != 1:
        raise ValueError("The initial Unlayered adapter supports one extruder; choose which extruder prints")
    if tool is not None and (isinstance(tool, bool) or not isinstance(tool, int) or not 1 <= tool <= count):
        raise ValueError("Invalid print extruder")
    planes = any(o.get("cutPlanes") for o in project.get("objects", []))
    curved = params.get("nonplanar_layers") is True
    for feature, requested in (("curved_tops", curved), ("multiplanar", planes)):
        if requested and feature not in accepted_features:
            raise ValueError(f"{feature} has not passed this worker's acceptance tests")
    if curved or planes:
        positive(params.get("max_tilt"), "maximum tilt", 45)
        positive(params.get("max_print_height"), "maximum print height")
        positive(params.get("hotend_clearance"), "hotend clearance", 50)
        positive(params.get("gantry_height"), "gantry clearance")
        if curved:
            if positive(params.get("nonplanar_max_angle"), "curved-top angle", 45) > params["max_tilt"]:
                raise ValueError("Curved-top angle exceeds printer maximum tilt")
            if positive(params.get("nonplanar_max_height"), "curved-top height", 50) > params["hotend_clearance"]:
                raise ValueError("Curved-top height exceeds hotend clearance")
    volume = profile.get("buildVolume", {})
    x, y, z = (positive(volume.get(a), f"bed {a}") for a in "xyz")
    result = {
        "printable_area": f"0x0,{x}x0,{x}x{y},0x{y}", "printable_height": str(min(z, params["max_print_height"]) if curved or planes else z),
        "nozzle_diameter": str(positive(profile.get("nozzleDiameter"), "nozzle diameter", 2)),
        "filament_diameter": str(positive(profile.get("filamentDiameter", 1.75), "filament diameter", 3.5)),
        "gcode_flavor": {"marlin": "marlin2", "reprap": "reprapfirmware"}.get(profile.get("gcodeFlavor"), profile.get("gcodeFlavor", "klipper")),
        "use_relative_e_distances": "1", "layer_change_gcode": "G92 E0", "enable_arc_fitting": "0",
        "machine_start_gcode": "G28\n" + (f"T{tool - 1}\n" if count > 1 else "") + "M140 S[hot_plate_temp_initial_layer]\nM104 S[nozzle_temperature_initial_layer]\nM190 S[hot_plate_temp_initial_layer]\nM109 S[nozzle_temperature_initial_layer]", "machine_end_gcode": "M104 S0\nM140 S0",
        "curr_bed_type": "High Temp Plate", "gcode_comments": "1",
        "brim_type": "outer_only" if params.get("adhesion_type") == "brim" else "no_brim",
        "enable_nonplanar": "1" if planes else "0", "axis_selection": "virtual",
        "print_sequence": "by group" if planes else "by layer",
    }
    if profile.get("bedShape") == "circular":
        result["printable_area"] = ",".join(f"{x/2+x/2*math.cos(i*math.pi/16):g}x{y/2+y/2*math.sin(i*math.pi/16):g}" for i in range(32))
    for key, target in MAPPING.items():
        if key not in params: continue
        value = params[key]
        if not isinstance(value, (str, int, float, bool)) or isinstance(value, str) and ("\n" in value or len(value) > 80):
            raise ValueError(f"Invalid {key}")
        result[target] = "1" if value is True else "0" if value is False else str(value)
    if "infill_density" in params: result["sparse_infill_density"] += "%"
    if "filament_type" in params: result["filament_type"] = str(params["filament_type"]).upper()
    if "nozzle_temps" in params:
        temperatures = params["nozzle_temps"]
        if not isinstance(temperatures, list) or len(temperatures) != 1:
            raise ValueError("The initial Unlayered adapter requires exactly one nozzle temperature")
        temperature = positive(temperatures[0], "nozzle temperature", 400)
        if "nozzle_temp" in params and temperature != params["nozzle_temp"]:
            raise ValueError("Conflicting nozzle temperatures")
        result["nozzle_temperature"] = str(temperature)
    for key in ("nozzle_temperature_initial_layer",):
        result[key] = result.get("nozzle_temperature", "210")
    result["hot_plate_temp_initial_layer"] = result.get("hot_plate_temp", "60")
    if "print_speed" in params:
        for key in ("inner_wall_speed", "sparse_infill_speed", "internal_solid_infill_speed", "top_surface_speed"):
            result[key] = str(params["print_speed"])
    if planes:
        result.update(nonplanar_virtual_max_angle=str(params["max_tilt"]), nonplanar_virtual_max_z=str(params["max_print_height"]),
                      nonplanar_hotend_cone_height=str(params["hotend_clearance"]), nonplanar_gantry_height=str(params["gantry_height"]))
    return result

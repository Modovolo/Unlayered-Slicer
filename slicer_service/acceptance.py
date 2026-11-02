"""Image-build acceptance: advertise only features demonstrated by real G-code."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from adapter import VERSION, settings

def block(path):
    n = 30
    vertices = [(100 + i*20/n, 100 + j*20/n, 6 + math.sin(i*math.pi/n)) for j in range(n+1) for i in range(n+1)]
    vertices += [(x, y, 0) for x, y, _ in vertices]
    size = (n+1)**2
    triangles = []
    for j in range(n):
        for i in range(n):
            a = j*(n+1)+i; b=a+1; c=a+n+2; d=a+n+1
            triangles += [(a,b,c),(a,c,d),(a+size,c+size,b+size),(a+size,d+size,c+size)]
    for edge in ([i for i in range(n+1)], [j*(n+1)+n for j in range(n+1)], [n*(n+1)+i for i in range(n,-1,-1)], [j*(n+1) for j in range(n,-1,-1)]):
        for a,b in zip(edge,edge[1:]): triangles += [(a,b+size,b),(a,a+size,b+size)]
    with path.open("w") as out:
        out.write("solid acceptance\n")
        for tri in triangles:
            out.write("facet normal 0 0 0\nouter loop\n")
            for index in tri: out.write("vertex %g %g %g\n" % vertices[index])
            out.write("endloop\nendfacet\n")
        out.write("endsolid acceptance\n")

def moves(text):
    previous = {}; curved = 0; count = 0
    for line in text.splitlines():
        if not re.match(r"^G[01]\s", line): continue
        words = {k: float(v) for k,v in re.findall(r"([XYZE])([-+\d.]+)", line.split(";")[0])}
        if words.get("E", 0) > 0 and ("X" in words or "Y" in words):
            count += 1
            if "Z" in words and abs(words["Z"] - previous.get("Z", words["Z"])) > .00001: curved += 1
        previous.update(words)
    return count, curved

def main(output):
    report = {"version": VERSION, "features": [], "failures": {}}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); mesh = root / "part.stl"; block(mesh)
        profile = {"buildVolume": {"x": 220,"y": 220,"z": 250}, "nozzleDiameter": .4, "filamentDiameter": 1.75}
        base = {"layer_height": .2, "first_layer_height": .2, "nonplanar_max_angle": 12, "nonplanar_max_height": 5,
                "max_tilt": 15, "max_print_height": 30, "hotend_clearance": 5, "gantry_height": 50}
        for feature in ("planar", "curved_tops", "multiplanar"):
            try:
                planes = [{"point": [110,110,3], "normal": [0,.15,1], "enabled": True}] if feature == "multiplanar" else []
                project = {"objects": [{"cutPlanes": planes}]}
                config = settings({**base, "nonplanar_layers": feature == "curved_tops"}, profile, project, ["curved_tops", "multiplanar"])
                path = root / (feature+".json"); gcode = root / (feature+".gcode")
                path.write_text(json.dumps({"settings": config, "objects": [{"path": str(mesh), "cutPlanes": planes}]}))
                result = subprocess.run([os.environ.get("UNLAYERED_BIN", "/opt/unlayered/bin/unlayered-slicer"), str(path), str(gcode)], capture_output=True, text=True, timeout=300)
                if result.returncode: raise RuntimeError((result.stderr + result.stdout)[-2000:])
                count, curved = moves(gcode.read_text())
                if count < 20: raise RuntimeError("Insufficient extrusion paths")
                if feature != "planar" and curved < 20: raise RuntimeError("No demonstrated non-flat extrusion")
                report["features"].append(feature)
            except Exception as error:
                report["failures"][feature] = str(error)
                if feature == "planar": raise
    Path(output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report))

if __name__ == "__main__": main(sys.argv[1])

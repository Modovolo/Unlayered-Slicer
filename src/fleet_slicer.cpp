// Fleet's display-free entry point. Geometry arrives in bed coordinates; no
// desktop preset loading, project scripts, reorientation or network plugins run.
#define NANOSVG_IMPLEMENTATION
#include "nanosvg/nanosvg.h"
#define NANOSVGRAST_IMPLEMENTATION
#include "nanosvg/nanosvgrast.h"
#include "libslic3r/Model.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/GCode.hpp"
#include "libslic3r/GCode/GCodeProcessor.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/libslic3r.h"
#include "libslic3r/Utils.hpp"
#include "nlohmann/json.hpp"
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <vector>
#include <cstdlib>
#include <algorithm>
#include <tbb/task_arena.h>

using namespace Slic3r;
using json = nlohmann::json;

static void slice(const char *job_path, const char *gcode_path)
{
    set_resources_dir("/opt/unlayered/resources");
    json job;
    std::ifstream input(job_path);
    input >> job;
    DynamicPrintConfig config = DynamicPrintConfig::full_print_config();
    for (auto it = job.at("settings").begin(); it != job.at("settings").end(); ++it)
        config.set_deserialize_strict(it.key(), it.value().get<std::string>());
    Model model;
    for (const auto &entry : job.at("objects")) {
        Model source = Model::read_from_file(entry.at("path").get<std::string>());
        if (source.objects.size() != 1) throw std::runtime_error("Each mesh must contain exactly one object");
        ModelObject *object = model.add_object(*source.objects.front());
        if (object->instances.empty()) object->add_instance();
        object->cut_planes.enabled = true;
        object->cut_planes.user_ordered = entry.value("userOrdered", false);
        for (const auto &plane : entry.at("cutPlanes")) {
            CutPlane cp;
            const auto p = plane.at("point").get<std::vector<double>>();
            const auto n = plane.at("normal").get<std::vector<double>>();
            cp.point = Vec3d(p.at(0), p.at(1), p.at(2));
            cp.normal = Vec3d(n.at(0), n.at(1), n.at(2)).normalized();
            cp.enabled = plane.value("enabled", true);
            object->cut_planes.planes.push_back(cp);
        }
    }
    Print print;
    print.set_status_callback([](const PrintBase::SlicingStatus &status) {
        std::cout << json{{"progress", status.percent}, {"message", status.text}, {"warning", status.warning_step >= 0}}.dump() << std::endl;
    });
    for (ModelObject *object : model.objects) print.auto_assign_extruders(object);
    print.apply(model, config);
    const auto validation = print.validate();
    if (!validation.string.empty()) throw std::runtime_error(validation.string);
    print.process();
    GCodeProcessorResult result;
    print.export_gcode(gcode_path, &result, nullptr);
    if (!print.get_conflict_string().empty()) throw std::runtime_error(print.get_conflict_string());
    if (result.gcode_check_result.error_code) throw std::runtime_error("Generated G-code exceeds the printer limits");
}

int main(int argc, char **argv)
{
    try {
        if (argc != 3) throw std::runtime_error("Usage: unlayered-slicer job.json output.gcode");
        const char *threads = std::getenv("SLICER_THREADS");
        // A sized arena, not global_control: Print::process waits until
        // this_task_arena::max_concurrency() tasks run at once, which never
        // happens when global_control caps workers below the CPU count.
        tbb::task_arena arena(threads ? std::clamp(std::stoi(threads), 1, 8) : 2);
        arena.execute([&] { slice(argv[1], argv[2]); });
        return 0;
    } catch (const std::exception &error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}

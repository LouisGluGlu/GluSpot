#include "helper_commands.h"
#include <cassert>
#include <vector>

int main() {
    using gluspot::volumeCommand;
    assert(volumeCommand(R"({"command":"set_volume","volume":0})") == 0.0);
    assert(volumeCommand(R"({"command": "set_volume", "volume": 1.0})") == 1.0);
    assert(volumeCommand(" { \"volume\": 5e-1, \"command\": \"set_volume\" }\r") == 0.5);
    for (const auto& line : std::vector<std::string>{
        "stop", "[]", "{}", R"({"command":"set_volume","volume":NaN})",
        R"({"command":"set_volume","volume":true})", R"({"command":"set_volume","volume":"0.5"})",
        R"({"command":"set_volume","volume":1.001})", R"({"command":"set_volume","volume":-0.1})",
        R"({"command":"set_volume","volume":1e999})", R"({"command":"set_volume","volume":01})",
        R"({"command":"set_volume","volume":+1})", R"({"command":"set_volume","volume":.5})",
        R"({"command":"set_volume","volume":0.5,"volume":1})",
        R"({"command":"set_volume","volume":0.5,"extra":1})",
        R"({"command":"set_volume","volume":0.5}garbage)", std::string(1025, ' ')})
        assert(!volumeCommand(line));
    return 0;
}

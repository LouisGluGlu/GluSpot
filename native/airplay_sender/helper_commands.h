#pragma once

#include <charconv>
#include <cmath>
#include <optional>
#include <regex>
#include <string>

namespace gluspot {

// Only the parent's tiny, versioned command grammar is accepted. This avoids
// interpreting partial JSON, duplicate keys, NaN/Inf or nested/unrelated data.
inline std::optional<double> volumeCommand(const std::string& line) {
    if (line.size() > 1024) return std::nullopt;
    static const std::string number = R"((-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?))";
    static const std::regex forward("^\\s*\\{\\s*\"command\"\\s*:\\s*\"set_volume\"\\s*,\\s*\"volume\"\\s*:\\s*" + number + "\\s*\\}\\s*$");
    static const std::regex reverse("^\\s*\\{\\s*\"volume\"\\s*:\\s*" + number + "\\s*,\\s*\"command\"\\s*:\\s*\"set_volume\"\\s*\\}\\s*$");
    std::smatch match;
    if (!std::regex_match(line, match, forward) && !std::regex_match(line, match, reverse))
        return std::nullopt;
    const auto text = match[1].str();
    double volume = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), volume);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() ||
        !std::isfinite(volume) || volume < 0 || volume > 1) return std::nullopt;
    return volume;
}

}  // namespace gluspot

#pragma once

#include <string>

struct Event {
    long seq;
    std::string topic;
    std::string payload;
};

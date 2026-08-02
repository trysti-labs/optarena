#include "event_log.hpp"

#include <cassert>
#include <iostream>

int main() {
    {
        EventLog log(2);
        log.record(Event{1, "a", "x"});
        log.record(Event{2, "b", "y"});
        log.record(Event{3, "c", "z"});

        auto history = log.history();
        assert(history.size() == 2);
        assert(history[0].topic == "b");
        assert(history[1].topic == "c");
        assert(log.size() == 2);
    }
    {
        EventLog log(10);
        assert(log.size() == 0);
        log.record(Event{1, "a", "x"});
        assert(log.size() == 1);
    }
    std::cout << "PASS" << std::endl;
    return 0;
}

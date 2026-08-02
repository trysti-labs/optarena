#pragma once

#include <cstddef>
#include <vector>

#include "event.hpp"

/* A capped history of published events. Once `capacity` is reached, the
 * oldest entry is dropped to make room for the newest (ring-buffer
 * semantics, implemented over a plain vector since capacities here are
 * small). */
class EventLog {
public:
    explicit EventLog(size_t capacity) : capacity_(capacity) {}

    void record(const Event& event) {
        if (entries_.size() >= capacity_) {
            entries_.erase(entries_.begin());
        }
        entries_.push_back(event);
    }

    std::vector<Event> history() const { return entries_; }

    size_t size() const { return entries_.size(); }

private:
    std::vector<Event> entries_;
    size_t capacity_;
};

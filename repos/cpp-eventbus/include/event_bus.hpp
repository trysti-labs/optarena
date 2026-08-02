#pragma once

#include <functional>
#include <string>
#include <unordered_map>
#include <utility>

#include "event.hpp"
#include "event_log.hpp"

class EventBus {
public:
    using Callback = std::function<void(const Event&)>;

    explicit EventBus(EventLog& log) : log_(log) {}

    /* Registers a callback for an exact topic match. Returns a
     * subscription id usable with unsubscribe(). */
    int subscribe(const std::string& topic, Callback cb) {
        int id = next_id_++;
        subscribers_[id] = std::make_pair(topic, std::move(cb));
        return id;
    }

    void unsubscribe(int subscription_id) {
        subscribers_.erase(subscription_id);
    }

    /* Records the event in the log, then invokes every subscriber whose
     * topic exactly matches. Returns how many callbacks were invoked. */
    int publish(const std::string& topic, const std::string& payload) {
        Event event{next_seq_++, topic, payload};
        log_.record(event);

        int invoked = 0;
        for (const auto& entry : subscribers_) {
            const std::string& sub_topic = entry.second.first;
            const Callback& cb = entry.second.second;
            if (sub_topic == topic) {
                cb(event);
                invoked++;
            }
        }
        return invoked;
    }

private:
    EventLog& log_;
    std::unordered_map<int, std::pair<std::string, Callback>> subscribers_;
    int next_id_ = 1;
    long next_seq_ = 1;
};

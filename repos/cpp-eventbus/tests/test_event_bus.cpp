#include "event_bus.hpp"

#include <cassert>
#include <iostream>
#include <vector>

int main() {
    {
        EventLog log(10);
        EventBus bus(log);

        std::vector<std::string> received;
        bus.subscribe("orders.created", [&received](const Event& e) {
            received.push_back(e.payload);
        });

        int invoked = bus.publish("orders.created", "order-1");
        assert(invoked == 1);
        assert(received.size() == 1);
        assert(received[0] == "order-1");

        int notInvoked = bus.publish("orders.shipped", "order-1");
        assert(notInvoked == 0);
    }
    {
        EventLog log(10);
        EventBus bus(log);
        int count = 0;
        int id = bus.subscribe("topic", [&count](const Event&) { count++; });
        bus.publish("topic", "1");
        bus.unsubscribe(id);
        bus.publish("topic", "2");
        assert(count == 1);
    }
    {
        EventLog log(10);
        EventBus bus(log);
        bus.publish("a", "x");
        bus.publish("b", "y");
        assert(log.size() == 2);
    }
    std::cout << "PASS" << std::endl;
    return 0;
}

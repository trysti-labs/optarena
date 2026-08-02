module EventBoard
  class EventRepository
    def initialize
      @events = {}
      @next_id = 1
    end

    def create(title:, capacity:)
      event = Event.new(@next_id, title, capacity, false)
      @events[event.id] = event
      @next_id += 1
      event
    end

    def find(id)
      @events[id]
    end

    def all
      @events.values.sort_by(&:id)
    end
  end

  class AttendeeRepository
    def initialize
      @attendees = {}
      @next_id = 1
    end

    def create(name:, email:)
      attendee = Attendee.new(@next_id, name, email)
      @attendees[attendee.id] = attendee
      @next_id += 1
      attendee
    end

    def find(id)
      @attendees[id]
    end

    def all
      @attendees.values.sort_by(&:id)
    end
  end

  class RsvpRepository
    def initialize
      @rsvps = {}
      @next_id = 1
    end

    def create(event_id:, attendee_id:, status:, created_at:)
      rsvp = Rsvp.new(@next_id, event_id, attendee_id, status, created_at)
      @rsvps[rsvp.id] = rsvp
      @next_id += 1
      rsvp
    end

    def find(id)
      @rsvps[id]
    end

    def for_event(event_id)
      @rsvps.values.select { |r| r.event_id == event_id }.sort_by(&:id)
    end

    def all
      @rsvps.values.sort_by(&:id)
    end
  end
end

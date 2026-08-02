module EventBoard
  class RsvpError < StandardError; end

  class RsvpService
    def initialize(events:, attendees:, rsvps:)
      @events = events
      @attendees = attendees
      @rsvps = rsvps
    end

    # Registers an attendee for an event. If the event's confirmed RSVP
    # count is already at capacity, the attendee is waitlisted instead of
    # rejected.
    def register(event_id:, attendee_id:)
      event = @events.find(event_id)
      raise RsvpError, "event not found" unless event
      raise RsvpError, "attendee not found" unless @attendees.find(attendee_id)

      status = confirmed_count(event_id) < event.capacity ? "confirmed" : "waitlisted"
      @rsvps.create(event_id: event_id, attendee_id: attendee_id, status: status, created_at: Time.now.to_f)
    end

    # Cancels an RSVP. If the canceled RSVP was confirmed, promotes the
    # earliest still-waitlisted RSVP for that event (FIFO, by id) to
    # confirmed - freeing a spot must not just vanish it.
    def cancel(rsvp_id)
      rsvp = @rsvps.find(rsvp_id)
      raise RsvpError, "rsvp not found" unless rsvp
      raise RsvpError, "rsvp already canceled" if rsvp.status == "canceled"

      was_confirmed = rsvp.status == "confirmed"
      rsvp.status = "canceled"

      if was_confirmed
        next_waiting = @rsvps.for_event(rsvp.event_id).select { |r| r.status == "waitlisted" }.min_by(&:id)
        next_waiting.status = "confirmed" if next_waiting
      end

      rsvp
    end

    private

    def confirmed_count(event_id)
      @rsvps.for_event(event_id).count { |r| r.status == "confirmed" }
    end
  end
end

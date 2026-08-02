require_relative "test_helper"

class RsvpServiceTest < Minitest::Test
  include EventBoardTestSetup

  def test_registers_confirmed_until_capacity_then_waitlists
    events, attendees, rsvps, service = build_service
    event = events.create(title: "Meetup", capacity: 1)
    a1 = attendees.create(name: "Ada", email: "a@example.com")
    a2 = attendees.create(name: "Bob", email: "b@example.com")

    r1 = service.register(event_id: event.id, attendee_id: a1.id)
    r2 = service.register(event_id: event.id, attendee_id: a2.id)

    assert_equal "confirmed", r1.status
    assert_equal "waitlisted", r2.status
  end

  def test_cancel_promotes_earliest_waitlisted_rsvp
    events, attendees, rsvps, service = build_service
    event = events.create(title: "Meetup", capacity: 1)
    a1 = attendees.create(name: "Ada", email: "a@example.com")
    a2 = attendees.create(name: "Bob", email: "b@example.com")

    r1 = service.register(event_id: event.id, attendee_id: a1.id)
    r2 = service.register(event_id: event.id, attendee_id: a2.id)
    assert_equal "waitlisted", r2.status

    service.cancel(r1.id)

    assert_equal "canceled", rsvps.find(r1.id).status
    assert_equal "confirmed", rsvps.find(r2.id).status
  end

  def test_register_raises_for_unknown_event_or_attendee
    events, attendees, rsvps, service = build_service
    a1 = attendees.create(name: "Ada", email: "a@example.com")

    assert_raises(EventBoard::RsvpError) { service.register(event_id: 999, attendee_id: a1.id) }

    event = events.create(title: "Meetup", capacity: 1)
    assert_raises(EventBoard::RsvpError) { service.register(event_id: event.id, attendee_id: 999) }
  end
end

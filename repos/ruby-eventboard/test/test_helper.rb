require "minitest/autorun"
require_relative "../lib/event_board"

module EventBoardTestSetup
  def build_service
    events = EventBoard::EventRepository.new
    attendees = EventBoard::AttendeeRepository.new
    rsvps = EventBoard::RsvpRepository.new
    service = EventBoard::RsvpService.new(events: events, attendees: attendees, rsvps: rsvps)
    [events, attendees, rsvps, service]
  end
end

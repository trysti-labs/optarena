require_relative "test_helper"

class EventRepositoryTest < Minitest::Test
  include EventBoardTestSetup

  def test_create_and_find
    events, = build_service
    event = events.create(title: "Meetup", capacity: 2)
    assert_equal "Meetup", event.title
    assert_equal 2, event.capacity
    assert_equal event, events.find(event.id)
  end

  def test_all_sorted_by_id
    events, = build_service
    events.create(title: "B", capacity: 1)
    events.create(title: "A", capacity: 1)
    ids = events.all.map(&:id)
    assert_equal ids.sort, ids
  end
end

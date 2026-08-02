module EventBoard
  Event = Struct.new(:id, :title, :capacity, :canceled) do
    def canceled?
      canceled
    end
  end

  Attendee = Struct.new(:id, :name, :email)

  Rsvp = Struct.new(:id, :event_id, :attendee_id, :status, :created_at)
end

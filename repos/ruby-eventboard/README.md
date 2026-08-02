# ruby-eventboard

A small event RSVP tracker (plain Ruby, no web framework - the sandbox's
Ruby image only ships Minitest + Rake, not Sinatra/Rack). Three entities:
events, attendees, and RSVPs, with a waitlist-promotion business rule when a
confirmed RSVP is canceled.

Run tests with `rake test` (Rake::TestTask running every `test/**/*_test.rb`
file via Minitest).

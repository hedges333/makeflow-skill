# Polyglot Stack Guide: Ruby

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for Ruby.

## 1. Detection Markers

Look for these files when scanning a Ruby application or gem:

- `Gemfile`, `Gemfile.lock`, `*.gemspec`, `Rakefile`

## 2. Standard Commands

Run these commands with `bundle exec`:

- Targeted Test: `bundle exec rspec <spec_file>` or `bundle exec rake test TEST=<test_file>`
- Format and Lint: `bundle exec rubocop` (auto-fix: `bundle exec rubocop -a`)

## 3. Smoke Test Template

If no spec exists, create `spec/smoke_spec.rb`:

```ruby
# frozen_string_literal: true

require 'rspec'

RSpec.describe 'Smoke Test Harness' do
  it 'runs cleanly' do
    expect(true).to be true
  end
end
```

## 4. Test Database and Network Isolation

Follow three isolation rules in Ruby tests:

1. Make sure that `RAILS_ENV=test` or `RACK_ENV=test` is set before running any test command.
2. Configure `database_cleaner` with `:transaction` strategy so all SQL changes roll back after each example.
3. Block and stub external HTTP calls with `webmock` or `vcr`.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place gem entrypoints in `lib/<gem>.rb` and namespaced classes in `lib/<gem>/` (or `app/models/` and `app/services/` in Rails).
- **Executable Entrypoints and Controllers**: Place thin CLI binstubs in `exe/` or `bin/` that `require` the gem from `lib/` and invoke a runner class.
- **Test Suite Placement and Naming**: Mirror the `lib/` tree inside `spec/` with `*_spec.rb` files for RSpec or `test/` with `*_test.rb` for Minitest.
- **Structural Anti-Patterns to Flag at Target 15**: Flag business logic in `exe/` binstubs or Rake tasks, or class namespaces that mismatch `lib/` paths.

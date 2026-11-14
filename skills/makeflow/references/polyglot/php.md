# Polyglot Stack Guide: PHP

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for PHP.

## 1. Detection Markers

Look for these files when scanning a PHP subproject:

- `composer.json`, `composer.lock`, `phpunit.xml`, `phpunit.xml.dist`

## 2. Standard Commands

Run these commands inside the PHP subproject directory:

- Targeted Test: `vendor/bin/phpunit --filter <test>` or `vendor/bin/pest <path>`
- Format: `vendor/bin/php-cs-fixer fix --dry-run --diff`
- Lint: `vendor/bin/phpstan analyse --level=max`

## 3. Smoke Test Template

If no test exists, create `tests/SmokeTest.php`:

```php
<?php

namespace Tests;

use PHPUnit\Framework\TestCase;

class SmokeTest extends TestCase
{
    public function testSmokeHarness(): void
    {
        $this->assertTrue(true);
    }
}
```

## 4. Test Database Isolation

Follow three database safety rules in PHP tests:

1. Make sure that `.env.testing` sets `DB_CONNECTION=sqlite` and `DB_DATABASE=:memory:`.
2. Wrap database tests in transaction traits, for example `RefreshDatabase` or `DatabaseTransactions`.
3. Never run `php artisan migrate` unless you first make sure that `APP_ENV=testing` is active.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place domain classes in `src/` or `app/` following `PSR-4` autoloading so namespaces match directory paths.
- **Executable Entrypoints and Controllers**: Keep HTTP front controllers in `public/index.php` and CLI runners in `bin/` as thin callers of `src/` services.
- **Test Suite Placement and Naming**: Place PHPUnit or Pest suites in `tests/Unit/` and `tests/Feature/` using the `*Test.php` filename suffix.
- **Structural Anti-Patterns to Flag at Target 15**: Flag side effects alongside class declarations (`PSR-1` violation), `PSR-4` namespace mismatches, or SQL queries inside controllers.

# Polyglot Stack Guide: Swift

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Swift.

## 1. Detection Markers

Look for these files when scanning a Swift package or Xcode workspace:

- `Package.swift`, `*.xcodeproj`, `*.xcworkspace`

## 2. Standard Commands

Run these commands inside the Swift package directory:

- Targeted Test: `swift test --filter <TestClass>`
- Format: `swiftformat --lint .`
- Lint: `swiftlint`

## 3. Smoke Test Template

If no test target exists, create `Tests/SmokeTests/SmokeTests.swift`:

```swift
import XCTest

final class SmokeTests: XCTestCase {
    func testSmokeHarness() {
        XCTAssertTrue(true)
    }
}
```

## 4. Test Isolation

Follow three isolation rules in Swift tests:

1. Configure CoreData and SwiftData stores in memory during tests so zero rows write to disk.
2. Place HTTP calls behind protocols and pass `URLProtocol` stubs in unit tests.
3. Write temporary test files under unique subdirectories of `FileManager.default.temporaryDirectory`.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place reusable module code in `Sources/<LibraryTarget>/` and declare the library target in `Package.swift` with minimal `public` surface.
- **Executable Entrypoints and Controllers**: Place thin `@main` structs or `main.swift` files in `Sources/<ExecutableTarget>/` that depend on `<LibraryTarget>`.
- **Test Suite Placement and Naming**: Place XCTest and Swift Testing suites in `Tests/<Target>Tests/*Tests.swift` using `@testable import <LibraryTarget>`.
- **Structural Anti-Patterns to Flag at Target 15**: Flag business logic inside executable targets that test targets cannot import, or view controllers executing raw networking.

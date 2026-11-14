# Polyglot Stack Guide: JVM (Java, Kotlin, and Scala)

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for JVM subprojects.

## 1. Detection Markers

Look for these files when scanning a JVM subproject:

- Java and Kotlin: `pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`
- Scala: `build.sbt`

## 2. Standard Commands

Run these commands inside the JVM module directory:

- Maven Test: `mvn test -Dtest=<ClassName>`
- Gradle Test: `./gradlew test --tests <ClassName>`
- Scala SBT Test: `sbt "testOnly <ClassName>"`
- Java Format and Lint: `mvn spotless:check` or `./gradlew checkstyleMain`
- Kotlin Format and Lint: `./gradlew ktlintCheck` or `./gradlew detekt`
- Scala Format: `sbt scalafmtCheck`

## 3. Smoke Test Templates

If no unit test exists, create one of the templates below.

### Java (`src/test/java/com/example/SmokeTest.java`)

```java
package com.example;

import static org.junit.jupiter.api.Assertions.assertTrue;
import org.junit.jupiter.api.Test;

class SmokeTest {
    @Test
    void testSmokeHarness() {
        assertTrue(true);
    }
}
```

### Kotlin (`src/test/kotlin/com/example/SmokeTest.kt`)

```kotlin
package com.example

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class SmokeTest {
    @Test
    fun testSmokeHarness() {
        assertTrue(true)
    }
}
```

### Scala (`src/test/scala/com/example/SmokeTest.scala`)

```scala
package com.example

import org.scalatest.funsuite.AnyFunSuite

class SmokeTest extends AnyFunSuite {
  test("smoke test harness") {
    assert(true)
  }
}
```

## 4. Test Database Isolation

Follow three database isolation rules in JVM tests:

1. Use an embedded H2 database (`jdbc:h2:mem:testdb;DB_CLOSE_DELAY=-1`) for fast unit tests.
2. For database integration tests, launch isolated Docker instances with Testcontainers.
3. Isolate service classes from database repositories with Mockito or MockK.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place domain and service packages in `src/main/java/`, `src/main/kotlin/`, or `src/main/scala/` with `package` headers matching directory paths.
- **Executable Entrypoints and Controllers**: Keep `main` classes, CLI commands, and HTTP controllers thin at the application boundary. Inject service interfaces one-way.
- **Test Suite Placement and Naming**: Mirror the domain package tree inside `src/test/java/`, `src/test/kotlin/`, or `src/test/scala/` so tests share package visibility.
- **Structural Anti-Patterns to Flag at Target 15**: Flag cyclic dependencies between packages, business logic inside controllers, or test sources placed under `src/main/`.

## Code Documentation Standards and Tree Assembly

- **Project Convention Detection**: Inspect `pom.xml`, `build.gradle`, or `build.sbt` for `Javadoc`, `KDoc`, or `Scaladoc` plugins at Target 15 (`SCAR-PROC-159`).
- **Idiomatic Doc Comment Standard**: Write `Javadoc` (`/** @param @return @throws */`), `KDoc`, or `Scaladoc` comments on public classes and methods.
- **Documentation Tree Assembly Command**: Run `mvn javadoc:javadoc`, `./gradlew dokkaHtml`, or `sbt doc` to build and verify the documentation tree.
- **Documentation Anti-Patterns to Flag at Target 40**: Flag `@param` or `@throws` tags that do not match method signatures or broken `@link` references.

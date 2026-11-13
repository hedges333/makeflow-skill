# 14. Design Patterns, Data Access, and Game Architecture

This manual defines object-oriented patterns, data access trade-offs, and game engineering patterns for Target 30 implementation and Target 40 review.

## 1. Balancing Encapsulation and Speed

Encapsulation (hiding internal state behind module boundaries) keeps modules testable, while runtime speed keeps large workloads responsive. Balance both goals with four default rules:

1. Place business rules behind clear module, trait, or repository interfaces so callers do not depend on storage internals.
2. Use an ORM (a library that maps database rows to objects) by default for standard single-record reads, writes, and schema changes.
3. Large datasets can trigger ORM memory overhead or an N+1 query problem (one extra query per row). If an ORM slows down large queries, write a parameterized SQL or NoSQL query inside the repository method.
4. Bind query parameters instead of concatenating strings, and test custom queries against an isolated `*_test` database.

---

## 2. Classic Object-Oriented Patterns (*Design Patterns*)

Follow two core principles from Gamma, Helm, Johnson, and Vlissides. First, program to an interface instead of a concrete class. Second, favor object composition (combining small objects at runtime) over deep class inheritance trees.

### Class-Scope Patterns (Compile-Time Inheritance)

Class patterns fix relationships between base classes and subclasses at compile time:

- `Factory Method` (Creational): Lets a subclass decide which concrete object type to create.
- `Class Adapter` (Structural): Adapts one interface to another through multiple inheritance or trait mixins.
- `Interpreter` (Behavioral): Evaluates sentences in a small, structured domain grammar.
- `Template Method` (Behavioral): Defines an algorithm skeleton in a base class and lets subclasses override steps.

### Object-Scope Patterns (Runtime Composition)

Object patterns connect interchangeable objects at runtime so unit tests can pass mock collaborators:

- Creational Object Patterns:
  - `Abstract Factory`: Creates families of related objects without naming concrete classes.
  - `Builder`: Constructs complex objects step by step with clear field defaults.
  - `Prototype`: Creates new objects by copying a preconfigured template instance.
  - `Singleton`: Restricts a class to one instance. Avoid mutable global Singletons in new code. Pass dependencies into constructors instead so unit tests stay isolated.
- Structural Object Patterns:
  - `Object Adapter`: Wraps an incompatible object behind the interface that callers expect.
  - `Bridge`: Splits a high-level abstraction from its platform driver so each can change independently.
  - `Composite`: Treats individual items and nested trees through one uniform interface.
  - `Decorator`: Wraps an object to add behavior without changing its class hierarchy.
  - `Facade`: Provides one clean entry interface over a complex multi-class subsystem.
  - `Flyweight`: Shares immutable intrinsic data across thousands of fine-grained instances to save memory.
  - `Proxy`: Places a surrogate in front of an object for lazy loading, caching, or access control.
- Behavioral Object Patterns:
  - `Chain of Responsibility`: Passes a request along a chain of handlers until one processes it.
  - `Command`: Packages an action as an object for queuing, logging, undo, or replay.
  - `Iterator`: Traverses elements of a collection without exposing its underlying storage.
  - `Mediator`: Centralizes communication among peer objects to prevent tangled direct references.
  - `Memento`: Captures and restores object state externally without breaking encapsulation, like `.workflow/state/` cards.
  - `Observer`: Notifies subscribed listeners automatically when subject state changes.
  - `State`: Delegates state-specific behavior to an active state object instead of long conditional branches.
  - `Strategy`: Encapsulates interchangeable algorithms behind one shared interface.
  - `Visitor`: Adds new operations across a stable set of node types without modifying those node classes.

---

## 3. Game Engineering Patterns (*Game Programming Patterns*)

Game engines include Godot, Haxe Flixel or Heaps, Unity, Unreal, SDL, and Raylib. If a subproject builds a game or real-time simulation, apply the patterns from `https://gameprogrammingpatterns.com/contents.html`.  Real-time loops run under strict frame budgets, such as 16.6 milliseconds at 60 frames per second. Avoid hot-loop heap allocations while keeping gameplay systems decoupled.  A summary of the public technical concepts detailed in that work follows:

### Design Patterns Revisited for Games

- `Command`: Represents player inputs and AI actions as objects for key rebinding, undo, and deterministic replays.
- `Flyweight`: Shares terrain tile definitions, meshes, and textures across thousands of world instances.
- `Observer`: Emits physics or combat signals to UI and achievement systems without coupling gameplay code.
- `Prototype`: Spawns entities from data-driven templates or scene prefabs.
- `Singleton`: Avoids global singletons in favor of passed context references or scoped service locators.
- `State`: Models character movement and AI behavior with finite, hierarchical, or pushdown state machines.

### Sequencing Patterns

- `Double Buffer`: Writes next-frame state to a back buffer and swaps buffers atomically so readers see consistent data.
- `Game Loop`: Advances physics in fixed time steps while rendering at variable display rates.
- `Update Method`: Advances each active world entity by one frame slice inside the main loop.

### Behavioral Patterns

- `Bytecode`: Encodes custom spells, rules, or mods as compact virtual-machine instructions in data files.
- `Subclass Sandbox`: Gives ability subclasses a small set of safe protected methods from one base class.
- `Type Object`: Defines monster breeds or item stats in data resources instead of creating hundreds of subclasses.

### Decoupling Patterns

- `Component`: Splits physics, AI, audio, and rendering into independent components attached to one entity container.
- `Event Queue`: Buffers asynchronous audio, combat, or network notifications in a ring buffer to decouple senders from receivers.
- `Service Locator`: Provides runtime-swappable audio, input, or analytics services so headless unit tests can register null providers.

### Optimization Patterns

- `Data Locality`: Stores active components in contiguous arrays so iterations stay inside fast CPU cache lines.
- `Dirty Flag`: Caches derived data such as world transforms and recomputes them only when local values change.
- `Object Pool`: Preallocates fixed arrays of projectiles or particles and reuses slots to prevent garbage-collection pauses.
- `Spatial Partition`: Indexes entities in grids, quadtrees, octrees, or bounding volume hierarchies for fast neighbor queries.

---

## 4. Testable Object-Oriented Script Pattern (BaseScript / Template Method)

Procedural CLI scripts and standalone automation utilities often resist clean unit testing. When scripts execute procedural code at the top level or within a monolithic procedural block, test suites are forced to invoke slow OS subprocesses or mutate global variables. Moreover, calling process termination functions (`sys.exit()`, `process.exit()`, `os.Exit()`) inside business logic aborts the test runner itself.

Under `SCAR-ARCH-05`, encapsulate script execution in class objects or runner structures using the classic GoF **Template Method** pattern (`BaseScript`). Separate Python modules into three layers:

1. Put reusable classes, validators, and `BaseScript` subclasses in `lib/` (such as `lib/base_script.py`) without executable shebang headers.
2. Keep `scripts/` strictly for thin executable CLI wrappers (`sys.exit(ScriptClass().execute())`) that import from `lib/` and never from peer scripts.
3. Place all `test_*.py` unit test suites in a top-level `tests/` directory.

### Execution Lifecycle Stages

The Template Method (`execute()`) controls execution flow across five distinct lifecycle phases:

```
+-------------------------------------------------------------+
| execute(args, stdout, stderr)                               |
|                                                             |
| 1. Bind Streams: Assign stdout and stderr streams           |
| 2. parse_args(args): Convert CLI strings into structures    |
| 3. setup(): Pre-flight verification and resource acquisition|
| 4. run(): Core business logic, returns exit code            |
| 5. cleanup(exit_code): Guaranteed resource release (finally)|
| 6. Trap Unhandled Exceptions: Return non-zero status        |
+-------------------------------------------------------------+
```

### Polyglot Implementations

#### 1. Python (`BaseScript`)
```python
import sys
from typing import List, Optional, TextIO

class BaseScript:
    def __init__(self) -> None:
        self.stdout: TextIO = sys.stdout
        self.stderr: TextIO = sys.stderr
        self.raw_args: List[str] = []

    def parse_args(self, args: Optional[List[str]] = None) -> None:
        self.raw_args = list(args) if args is not None else []

    def setup(self) -> None:
        pass

    def run(self) -> Optional[int]:
        return 0

    def cleanup(self, exit_code: int) -> None:
        pass

    def execute(self, args: Optional[List[str]] = None, stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        if args is None:
            args = sys.argv[1:]
        exit_code = 0
        try:
            self.parse_args(args)
            self.setup()
            res = self.run()
            exit_code = 0 if res is None else int(res)
        except Exception as exc:
            raw_code = getattr(exc, "exit_code", 1)
            try:
                exit_code = int(raw_code)
            except (ValueError, TypeError):
                exit_code = 1
            self.stderr.write(f"Error: {exc}\n")
        finally:
            self.cleanup(exit_code)
        return exit_code

if __name__ == "__main__":
    sys.exit(BaseScript().execute())
```

#### 2. TypeScript / Node (`BaseScript`)
```typescript
import { Writable } from 'node:stream';

export abstract class BaseScript {
  protected stdout: Writable = process.stdout;
  protected stderr: Writable = process.stderr;

  abstract run(): Promise<number>;
  async setup(): Promise<void> {}
  async cleanup(exitCode: number): Promise<void> {}

  async execute(args: string[] = process.argv.slice(2), stdout?: Writable, stderr?: Writable): Promise<number> {
    if (stdout) this.stdout = stdout;
    if (stderr) this.stderr = stderr;
    let exitCode = 0;
    try {
      await this.setup();
      exitCode = await this.run();
    } catch (err) {
      exitCode = 1;
      this.stderr.write(`Error: ${err}\n`);
    } finally {
      await this.cleanup(exitCode);
    }
    return exitCode;
  }
}
```

#### 3. Go / Rust (`Runner` Pattern)
In Go and Rust, implement the runner lifecycle via an interface/trait without calling `os::exit` inside business methods:
```go
type Script interface {
    Setup(ctx context.Context) error
    Run(ctx context.Context) (int, error)
    Cleanup(exitCode int)
}

func Execute(s Script, args []string, stdout, stderr io.Writer) int {
    // Coordinate lifecycle and return integer status code
}
```

#### 4. Dart (`BaseScript` with Zones)
```dart
abstract class BaseScript {
  Future<int> run();
  Future<void> setup() async {}
  Future<void> cleanup(int exitCode) async {}

  Future<int> execute({List<String>? args, StringSink? stdout, StringSink? stderr}) async {
    int exitCode = 0;
    try {
      await setup();
      exitCode = await run();
    } catch (e) {
      exitCode = 1;
      stderr?.writeln('Error: $e');
    } finally {
      await cleanup(exitCode);
    }
    return exitCode;
  }
}
```

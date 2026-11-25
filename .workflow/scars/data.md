# Synthetic Scars: Data

### SCAR-LOCAL-DATA-0001: Track Fenced Code Block State During Line-Based Markdown Parsing

- **Category**: `DATA`
- **Wound**: Line-based Markdown parsers split or drop records when code examples inside fenced blocks contain `---`, `## `, or bullet headers.
- **Trap**: Parsers match heading and separator syntax on every line without tracking active triple-backtick or tilde fences.
- **Circuit Breaker**: If a line sits inside an open fenced code block, stop structural matching until the closing fence appears.
- **Permanent Reflex**: Track fence character and length state across lines. Preserve indentation and blank lines inside fenced blocks, and test round-trips with embedded `---` and `## ` lines.
- **Socratic Probe**: Does your line-based Markdown parser track open code fences before matching headings or separators? Will indented code examples lose formatting on round-trip?

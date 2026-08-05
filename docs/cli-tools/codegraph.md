# CodeGraph

CodeGraph is a local structural index for navigating unfamiliar or large repositories. It maps
symbols, files, imports, calls, inheritance, and related code relationships.

It is a navigation and impact-analysis aid, not a replacement for reading the current source or
for documented architectural intent.

## When to use

Use CodeGraph to:

- understand an unfamiliar subsystem or trace an execution path
- find relationships among several symbols, including callers and callees
- estimate the likely blast radius of a change
- identify tests affected by changed source files
- navigate a large or cross-language repository

Prefer direct file reads, `rg`, `fd`, `git`, or LSP for a known file, exact string, uncommitted
content, or simple filename discovery.

## Start here

Run the installed version without downloading a replacement:

```bash
bunx --no-install codegraph version
```

Before structural queries, generate or update the local `.codegraph/` index:

```bash
# First use in this repository
bunx --no-install codegraph init

# Later uses, after source changes
bunx --no-install codegraph sync
```

Check the index when results seem incomplete or stale:

```bash
bunx --no-install codegraph status
```

`init`, `sync`, and `index --force` write local index data. Do not run them where repository
modifications are restricted without permission. Rebuild only when the index is structurally
incorrect:

```bash
bunx --no-install codegraph index --force
```

## Example queries

```bash
# Orient around a behavior or subsystem
bunx --no-install codegraph explore "how does user authentication work"

# Find a known symbol
bunx --no-install codegraph query UserService --kind class --limit 10

# Inspect a symbol or a file
bunx --no-install codegraph node UserService
bunx --no-install codegraph node src/auth/service.ts

# Trace calls
bunx --no-install codegraph callers handleRequest
bunx --no-install codegraph callees handleRequest

# Estimate the candidate impact radius
bunx --no-install codegraph impact AuthMiddleware --depth 3

# Find tests connected through import dependencies
bunx --no-install codegraph affected src/auth/service.ts
```

Use bounded, focused queries. Treat impact results as candidates, not proof of every runtime
dependency: dynamic imports, generated code, dependency injection, configuration, and reflection
may not be represented.

## Help

```bash
bunx --no-install codegraph --help
bunx --no-install codegraph <command> --help
```

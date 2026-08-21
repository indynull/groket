# Examples

**Supported reference packs** — CI and `just examples-check` refuse to break
them. Copy into `~/.groket/` or pass paths explicitly. Nothing under
`examples/` is auto-loaded by the product.

| Pack | What it teaches | Install / use |
|------|-----------------|---------------|
| [`config/`](config/) | Prefs TOML (`config.toml`) | `~/.groket/config.toml` |
| [`tasks/`](tasks/) | Batch task catalogs | `groket batch -t <file>` |
| [`personas/`](personas/) | Persona JSON (e.g. marketplace plugins) | `~/.groket/personas/` |
| [`notes/`](notes/) | Operator notes schema TOML (field list) | `~/.groket/notes_schema.toml` |
| [`keys/`](keys/) | Key overlay (`colemak.toml`) | `~/.groket/keys.toml` |

## Start here

| Goal | Open / run |
|------|------------|
| Batch tasks | [`tasks/demo_tasks.yaml`](tasks/demo_tasks.yaml) |

```bash
uv run groket batch validate examples/tasks/demo_tasks.yaml
uv run groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
uv run groket gen tasks
```

## Contract

```bash
just examples-check   # or: uv run python scripts/check_examples.py
```

Validates: task YAML schemas, persona JSON, keys overlays
(`groket keys --check`), pack READMEs. Part of `just ci`.

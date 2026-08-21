# Operator notes schema example

Turn-linked operator notes use a **configurable** field list. Copy this file:

```bash
mkdir -p ~/.groket
cp examples/notes/notes_schema.example.toml ~/.groket/notes_schema.toml
```

Then edit `id` / `label` for your workflow. Field ids must be
`^[A-Za-z_][A-Za-z0-9_-]*$`. Keep program-specific templates in your local kit,
not in the groket package.

### Field kinds (one path)

| Schema | Terminal / desktop HUD | Stored value |
|--------|------------------------|----------------|
| no `choices` (or empty) | text area / text field | free string |
| `choices = […]` + `pick = "one-of"` (default when `pick` omitted) | dropdown | one token |
| `choices = […]` + `pick = "many"` | multi-select / filter chips | newline-joined tokens |

Existing schemas without `choices` keep working as free text.

## Session file

Notes are stored as `<session_dir>/operator_notes.toml` (fallback:
`~/.groket/notes/<session_id>/operator_notes.toml`).

Host Grok sessions (optional **Host** catalog / `H` on the sessions list) use
the same notes flow; notes always write under
`~/.groket/notes/<session_id>/` so the live `~/.grok/sessions` tree is not
modified.

## TUI

In the session browser:

- **`N`** — create a note (linked to the current turn and optional selected event)
- **`O`** / command palette — edit or delete an existing note (Delete button in the edit modal)

Report tab lists notes. Export (`E`) includes `notes/operator_notes.toml` when
notes exist.

**Authoring** is via TUI, Emacs, Neovim, or HUD (control plane). Batch does not write notes.

## Ingest

External tools can parse the TOML from the export tarball without scraping the
Report markdown.

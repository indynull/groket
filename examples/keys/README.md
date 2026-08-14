# Key overlay examples

Copy a map to `~/.groket/keys.toml` (or point `GROKET_KEYS` at the file).
The file is diffs only: omitted ids keep catalog defaults.

```bash
mkdir -p ~/.groket
# After colemak.toml ships:
# cp examples/keys/colemak.toml ~/.groket/keys.toml
uv run groket keys --check
```

`colemak.toml` is not in this pack yet. It will be a home-row nav map
plus leader verbs, not a full layout emulator.

A bad overlay is refused in full (`groket keys --check` exits 1) and
the catalog defaults stay active. The TUI and HUD both apply a valid
map to footer, help, and key dispatch.

# tumbler-snapper

A codec that recovers a lossless, tracker-like intermediate representation (IR) from a `.sid` by algorithmic P-Code analysis, plus a VM that replays the IR byte-exact.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

Console script `tsnap`:

```bash
tsnap recover <file.sid>    # recover per-frame register generators
tsnap tracker <file.sid>    # build the tracker IR
```

## Testing

```bash
pytest -n auto
```

## Docs

- [docs/prototype.md](docs/prototype.md) — symbolic P-Code recovery prototype.
- [docs/tracker-model.md](docs/tracker-model.md) — tracker-IR model.
- [CLAUDE.md](CLAUDE.md) — design constraints and correctness workflow.

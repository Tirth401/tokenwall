Generated decode-step traces live here.

- `<run>.segs`: the compact segment list (gitignored; regenerate in under a second with
  `python -m tokenwall gen ...`, the exact command is in the matching `.meta.json`).
- `<run>.meta.json`: run configuration, footprint, bytes and requests per step (committed).
- `*.txt`: Ramulator `LD/ST` exports (gitignored, large).

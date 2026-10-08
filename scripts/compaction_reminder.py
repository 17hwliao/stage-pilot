"""Keep the configured hook command stable; stage_gate owns the new phase policy."""
from stage_gate import main

if __name__ == "__main__":
    raise SystemExit(main())
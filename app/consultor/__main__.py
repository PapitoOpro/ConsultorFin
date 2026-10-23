import sys

from app.consultor.cli import main

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.exit(main())

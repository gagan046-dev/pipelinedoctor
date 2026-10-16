# demo/ - invoicer, the deliberately breakable project

A small, correct Python library that exists to fail on purpose, so PipelineDoctor has real
CI failures to diagnose. Walkthrough: [`docs/demo.md`](../docs/demo.md).

```
src/invoicer/
  money.py    Decimal money, half-up rounding to cents, formatting
  totals.py   line items -> subtotal, tax, total
  store.py    tiny PostgreSQL store (psycopg 3)
tests/        unit tests (100 % coverage) + integration tests (-m integration)
break/        apply.py + patches: inject one real defect (see break/README.md)
scripts/      break.ps1, cleanup-breaks.ps1, publish_dry_run.py
Dockerfile    test image: unit tests run inside it with a 256 MiB memory limit
```

```powershell
cd demo
pip install -r requirements-dev.txt     # run from demo/: "-e ." means this directory
pytest --cov=invoicer                   # integration tests are deselected by default
pytest -m integration                   # needs PostgreSQL on localhost:$env:DEMO_DB_PORT (5432)
```

Database settings (`DEMO_DB_HOST`, `DEMO_DB_PORT`, `DEMO_DB_USER`, `DEMO_DB_PASSWORD`,
`DEMO_DB_NAME`) default to the throwaway CI service (`postgres` / `demo`); not secrets.

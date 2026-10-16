# PipelineDoctor Console

FastAPI app that reads the GitHub API and shows the occurrence register, investigation
reports, the eval ledger and DORA metrics. See [`../docs/console.md`](../docs/console.md).

```powershell
npm ci; npm run fonts          # self-hosted fonts -> src/pipelinedoctor_console/static/fonts
$env:PD_CONSOLE_FIXTURES = "1"  # offline: bundled sample data, no GitHub calls
uv run uvicorn pipelinedoctor_console.app:app --port 8091
```

# runner/ - self-hosted runner for production deploys

A containerised GitHub Actions runner (`pd-laptop`, label `pd-prod`) that runs only the
`production` job of `deploy.yml` and the `rollback` job of `rollback.yml`.
Full explanation and the security model: [`docs/self-hosted-runner.md`](../docs/self-hosted-runner.md).

| File | Purpose |
|---|---|
| `Dockerfile` | `actions-runner:2.337.0` + docker compose plugin v5.5.1 (checksum-verified) |
| `entrypoint.sh` | `register` / `unregister` (token from `$RUNNER_TOKEN`), otherwise runs `./run.sh` |
| `compose.yml` | container `pd-runner`, volume `pd-runner-home`, Docker socket, `no-new-privileges` |
| `register.ps1` | build + one-time registration; asks for the token (or `-UseGh`) |
| `start.ps1` / `stop.ps1` | run / pause the runner |
| `remove.ps1` | unregister (`-UseGh`, `-DeleteVolume`) |

```powershell
.\runner\register.ps1
.\runner\start.ps1
gh api repos/gagan046-dev/pipelinedoctor/actions/runners --jq '.runners[] | {name,status,labels:[.labels[].name]}'
.\runner\stop.ps1
```

Never commit a token, never add `pd-prod` to any other workflow, keep it stopped when idle.

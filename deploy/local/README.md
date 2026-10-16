# deploy/local - blue-green on your laptop, no GitHub

`deploy-local.ps1` builds the console (`console/`) and the proxy (`deploy/proxy/`) locally and
runs `deploy/scripts/bluegreen.sh` through Git Bash against Docker Desktop. It is the
production flow minus signing, staging and approval. Result: http://127.0.0.1:8090

```powershell
.\deploy\local\deploy-local.ps1 -Version dev                       # first deploy
.\deploy\local\deploy-local.ps1 -Version dev2                      # switch blue <-> green
.\deploy\local\deploy-local.ps1 -Version dev3 -ExpectVersion nope  # forced failure -> automatic rollback
.\deploy\local\deploy-local.ps1 -Action rollback                   # previous colour
.\deploy\local\deploy-local.ps1 -Action status
.\deploy\local\deploy-local.ps1 -Action down -RemoveVolumes        # remove everything
.\deploy\local\deploy-local.ps1 -ConsoleImage ghcr.io/gagan046-dev/pipelinedoctor-console:0.2.0 -Version 0.2.0
```

Speed up the soak while experimenting: `$env:PD_SOAK_INTERVAL_S = '1'`.
Requirements: Docker Desktop running, Git for Windows (Git Bash). See `docs/deployment.md`.

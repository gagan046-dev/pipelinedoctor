# PipelineDoctor

An AI agent that **diagnoses failed GitHub Actions runs** and posts the diagnosis on the pull
request, delivered by a production-grade CI/CD pipeline.

This is a **teaching repository**: the git history is the course. Every commit tagged
`stage-NN` adds one CI/CD topic, and its Actions runs show the topic in action.

## Stage 01 - Hello workflow

This first commit contains only one workflow, [`.github/workflows/hello.yml`](.github/workflows/hello.yml),
and the architecture document that the next stages implement.

What to look at:

1. **Actions tab** -> run "Hello" -> two jobs, `greet` and `report`. `report` waits for `greet`
   (`needs:`) and prints the greeting that `greet` produced (a *job output*).
2. The run's **summary page**: a table written by the step into `$GITHUB_STEP_SUMMARY`, and a
   `::notice::` annotation.
3. The **logs**: expandable `::group::` sections, and how every step is a separate shell process.

```powershell
gh workflow run hello.yml -f who=Philips   # trigger it yourself (workflow_dispatch)
gh run watch                               # follow it live
gh run view --log                          # read the logs in the terminal
```

Details: [docs/stages/01-hello.md](docs/stages/01-hello.md). Running it locally with `act`:
[docs/local-act.md](docs/local-act.md).

## Coming next

02 quality gates - 03 speed - 04 containers - 05 security - 06 LLM evals - 07 releases -
08 PipelineDoctor - 09 continuous delivery - 10 reuse. See [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT, see [LICENSE](LICENSE).

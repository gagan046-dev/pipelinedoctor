# Changelog

## [0.2.0](https://github.com/gagan046-dev/pipelinedoctor/compare/v0.1.0...v0.2.0) (2026-10-16)


### Features

* **stage-02:** diagnosis engine with lint, type and coverage gates ([855859b](https://github.com/gagan046-dev/pipelinedoctor/commit/855859b6377a344812ebccaff840584db1c7f8e7))
* **stage-04:** container images with buildx cache and GHCR push ([93d346b](https://github.com/gagan046-dev/pipelinedoctor/commit/93d346be7c082810228b8bb2b55a0e9e2e26cbf5))
* **stage-06:** LLM eval gate with cached results and nightly drift run ([47cc5f8](https://github.com/gagan046-dev/pipelinedoctor/commit/47cc5f802d601e2f6fbdd7ae59a65b18c287c13b))
* **stage-08:** doctor workflow and reusable Docker action ([7240866](https://github.com/gagan046-dev/pipelinedoctor/commit/7240866c9e08d07a49ff1f4cea42c2f65745f976))
* **stage-09:** staging smoke and blue-green production deploys with rollback ([9f2ae4e](https://github.com/gagan046-dev/pipelinedoctor/commit/9f2ae4e8828fc4846e73a4bc18cb143728716289))


### Bug Fixes

* **ci:** keep bash as the shell in jobs that set their own run defaults ([7e40e1a](https://github.com/gagan046-dev/pipelinedoctor/commit/7e40e1a668c1c8baefe87b0b287c697ee6c895e2))
* **ci:** tag releases as vX.Y.Z without the component prefix ([db10005](https://github.com/gagan046-dev/pipelinedoctor/commit/db1000565ac0e26350764fdd6d28ff42aa945510))
* **console:** probe GitHub on the first /readyz call on a freshly booted host ([6286730](https://github.com/gagan046-dev/pipelinedoctor/commit/6286730ce7ffb8a554b5a029a3e4ada32c221a35))
* **engine:** stop the ruff rule from matching artifact digest lines ([#6](https://github.com/gagan046-dev/pipelinedoctor/issues/6)) ([bb82d05](https://github.com/gagan046-dev/pipelinedoctor/commit/bb82d0517d5614e4198f395d482b5f9f2aab67e4))

## Changelog

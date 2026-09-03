# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Analyses can now target multiple languages per repository (previously a single language).
- Repository statistics captured during analysis (e.g. number of files and number of files
  per extension) and surfaced to the end user once the analysis completes.

### Changed

- Authentication now issues short-lived, internally-signed access and refresh tokens; the
  identity provider's OIDC token is verified only at sign-in.
- Files are auto-detected by extension, and files outside the selected languages are kept rather
  than discarded.
- Improved AST, DFG, and CFG extraction.
- Deep-analysis PDF export is now rendered via [Gotenberg](https://gotenberg.dev/).

## [0.1.0] - 2026-06-11

### Added

- Initial release of Retro-Doc.

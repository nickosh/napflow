# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- *(core)* S1/M4 checker — E001-E012, W101-W107, closure checking
- *(core)* S1/M3 discovery — manifest walk-up, flows, env profiles
- *(core)* S1/M2 loader + write path — positioned reads, canonical emit
- *(core)* S1/M1 Pydantic models — full node catalog + JSON Schema export
- *(docs)* Initial project docs and requirements

### Documentation

- Add development plan, working journal, changelog requirement
- Park endpoint collections + Postman/OpenAPI import for v2
- Consistency pass across amendment rounds
- *(review)* Apply senior-review fixes (D25, EC28-EC37)

### Internal

- Fix Windows path assertion in closure test
- Pin setup-uv to v8.2.0 — no moving v8 major tag exists
- Close out M0 — tick boxes, bump CI actions to Node 24 majors
- Scaffold repo for S1/M0 — packaging, CI, contracts, changelog
- Add SessionEnd session-breadcrumb hook


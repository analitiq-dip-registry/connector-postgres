# Changelog

## [2.0.0] - 2026-08-02

### Changed
- feat!: rebuild against the rc17 write-path contract (#22)

## [1.0.0] - 2026-07-23

### Changed
- feat: align connector with rc13 contracts (ADBC default transport, precision-preserving type maps) (#21)

## [0.1.2] - 2026-07-09

### Fixed
- fix: map PostgreSQL array types to Json container and set connector_id to repo slug (#20)

## [0.1.1] - 2026-06-29

### Fixed
- fix: map native JSON/JSONB to canonical Json on read (#19)

## [0.1.0] - 2026-06-04

### Added
- feat: package connector with CDK dialect class and driver deps (#18)

## [0.0.7] - 2026-05-15

### Fixed
- bug: match Analitiq webhook API Gateway schema exactly (#17)

## [0.0.6] - 2026-05-15

### Fixed
- bug: re-add name/type to webhook payload from display_name/kind (#16)

## [0.0.5] - 2026-05-15

### Fixed
- bug: drop slug/name/type from Analitiq webhook payload (#15)

## [0.0.4] - 2026-05-15

### Fixed
- bug: refire release webhook (#13)

## [0.0.3] - 2026-04-27

### Fixed
- feat: consolidate manifest into connector.json and add type map (#9)

## [0.0.2] - 2026-04-20

### Fixed
- docs: point engine references to analitiq-ai/analitiq-engine (#8)

## [0.0.1] - 2026-03-29

### Added
- Initial connector definition with database credentials authentication

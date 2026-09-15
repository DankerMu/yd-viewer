## ADDED Requirements
### Requirement: IFS canonical grid identity is lowercase end to end
Canonical IFS default grid URI SHALL be canonical/ifs/grid/ifs_0p25/grid.json. Canonical grid writes/checks/catalog rows and forcing default references SHALL use that identity without uppercase aliases or case fallback. Other product values/bytes, GFS identity and raw source capitalization MUST remain unchanged.
#### Scenario: Canonical and forcing exact identity
- **WHEN** a valid IFS manifest produces canonical products and downstream forcing consumes the catalog
- **THEN** each grid_definition_uri equals canonical/ifs/grid/ifs_0p25/grid.json by case-sensitive string comparison, even on APFS
#### Scenario: Parser remains case preserving without a new alias
- **WHEN** the emitted lowercase key is passed to the existing object_path parser
- **THEN** its source component is ifs; no uppercase grid key or phantom-source compatibility rule is introduced

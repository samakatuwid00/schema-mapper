## ADDED Requirements

### Requirement: Searchable persisted conversations

Persisted agent conversations SHALL be searchable by safe text fields for the authenticated user.

#### Scenario: Search uses redacted content

- **WHEN** the system searches messages
- **THEN** it searches only persisted, already-redacted conversation content
- **AND** it does not query raw source or target row values

### Requirement: Bulk delete persisted conversations

Persisted agent conversations SHALL support bulk deletion scoped to the authenticated user.

#### Scenario: Bulk delete is scoped

- **WHEN** a user bulk deletes conversation ids
- **THEN** the delete statement is constrained by that user's id
- **AND** conversations for other users remain unchanged


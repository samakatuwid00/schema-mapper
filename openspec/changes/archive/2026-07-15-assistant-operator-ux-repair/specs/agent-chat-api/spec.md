## ADDED Requirements

### Requirement: Conversation search API

The agent conversation API SHALL support searching the authenticated user's conversations.

#### Scenario: Search conversations

- **WHEN** a user requests their conversations with a search query
- **THEN** the API returns only that user's conversations whose title or persisted redacted message text matches the query

#### Scenario: Search is user scoped

- **WHEN** another user's conversation matches the query
- **THEN** it is not returned and its existence is not revealed

### Requirement: Bulk conversation delete API

The agent conversation API SHALL support deleting multiple conversations owned by the authenticated user in one request.

#### Scenario: Bulk delete owned conversations

- **WHEN** a user submits a list of conversation ids they own for deletion
- **THEN** the API deletes those conversations and returns the number deleted

#### Scenario: Bulk delete does not leak foreign ids

- **WHEN** the request includes ids owned by another user or nonexistent ids
- **THEN** the API does not reveal which ids were foreign or nonexistent
- **AND** only conversations owned by the caller are deleted


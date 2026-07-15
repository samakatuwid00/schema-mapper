## ADDED Requirements

### Requirement: Deploy failure repair workflow

The assistant SHALL guide operators from a failed deploy job to mapping repair, review, approval, and redeploy.

#### Scenario: Failed deploy job becomes repair checklist

- **WHEN** an operator asks how to fix a failed `deploy_lrmis` job
- **THEN** the assistant identifies the failed proposal when available
- **AND** returns a checklist covering missing mappings, mapping review, approval, and redeploy

#### Scenario: Proposal disappeared from queue

- **WHEN** the failed proposal is not visible in the normal review queue
- **THEN** the assistant still provides a direct open-proposal action for `/mappings/{proposal_id}`
- **AND** explains that approved proposals may leave the review queue while remaining accessible by URL

#### Scenario: Redeploy remains explicit

- **WHEN** a mapping repair is applied
- **THEN** the assistant recommends reviewing/approving the proposal and explicitly starting deploy again
- **AND** no deploy job is enqueued without the user's separate confirmation


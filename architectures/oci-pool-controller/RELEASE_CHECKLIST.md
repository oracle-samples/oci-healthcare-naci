# Public release checklist

Status: **draft, not approved for external distribution**. A clean source tree,
license file and passing tests do not constitute release authorization.

## Required decisions and approvals

- [ ] Confirm the intended source, image and documentation distribution scope
  with the release owner and D&E Legal.
- [ ] Confirm ownership, contributor/upstream provenance, licensing authority,
  the correct copyright years and required third-party attributions.
- [ ] Obtain applicable business/management, Oracle API-use, export compliance,
  Corporate Architecture and other required approvals.
- [ ] Obtain approval for the external repository, publishing identity and
  release process. Keep approval records in approved internal systems, not in
  the public source tree.

## Source and dependency preparation

- [ ] Review licensing terms and the unsupported-sample notices with Legal.
- [ ] Apply approved copyright/license headers to all substantive source files.
  Root notices alone do not complete this task.
- [ ] Prepare and review `THIRD_PARTY_LICENSES.txt`. It is not supplied in this
  draft; the source dependency and upstream attribution review is incomplete.
- [ ] Inventory the OCI Python SDK, Fn FDK, Python/base images, Terraform
  tooling/provider and upstream source as applicable. Assess transitive
  dependencies and the different obligations of source versus binary delivery.
- [ ] Review and pin approved image/dependency inputs; perform required security
  and source-disclosure checks. Do not include credentials, Terraform state,
  internal policy documents or operator operational data.

## Technical release gates

- [ ] Run the matching offline controller/client regressions and verify the
  source snapshot, image build and Terraform configuration. Tests are maintained
  separately from this minimal public-source draft.
- [ ] Complete the staging and operational gates in `docs/RUNBOOK.md`, including
  real worker readiness, targeted retirement, retries and recovery limitations.
- [ ] Review upgrades against existing resource names, pool identity, generations
  and retirement ledgers. Never clear safety state to adopt new naming.
- [ ] Regenerate `RELEASE_MANIFEST.json` after approved source/header/notice edits.
  Include the required licenses and attributions in any distributed image or
  archive, and verify the final distribution contents.
- [ ] Remove the draft warning only after the release owner confirms that all
  required approvals and release gates are complete. Publish only the reviewed
  clean-history branch; do not push other branches, tags or a mirror of this
  development repository.

This checklist is a preparation aid, not a replacement for current company
policy or a statement that any approval has been obtained. Do not place
confidential approval emails or internal review records in this branch.

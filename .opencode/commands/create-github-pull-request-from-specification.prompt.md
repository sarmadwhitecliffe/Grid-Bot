---
agent: "@planner"
description: "Create GitHub Pull Request from specification file using template"
tools: ["read", "bash", "glob"]
applies-to: "GitHub project management"
---
# Create GitHub Pull Request from Specification

Create GitHub Pull Request for the specification at `${input:SpecificationFile}`.

## Process

1. Analyze specification file template from `.github/pull_request_template.md` to extract requirements
2. Create pull request draft template by using GitHub CLI on `${input:targetBranch}` and ensure no existing pull request exists
3. Get changes in pull request to analyze information that was changed
4. Update the pull request body and title using the template information
5. Switch from draft to ready for review
6. Assign pull request to current user
7. Return URL of created pull request

## Requirements
- Single pull request for the complete specification
- Clear title/pull_request_template.md identifying the specification
- Fill enough information into pull_request_template.md
- Verify against existing pull requests before creation

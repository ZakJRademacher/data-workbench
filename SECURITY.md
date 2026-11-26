# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 3.0.x | Yes |
| Older | No |

## Reporting a vulnerability

Please report security problems privately, not in a public issue.

1. Go to the repository's **Security** tab and choose **Report a vulnerability**. This opens a private advisory that only the maintainer can see.
2. Describe the problem, the version and download you used, and how to reproduce it. Use made-up data, not real records.

You should get a reply within 7 days. Once a fix is released, the advisory is published with credit to you, unless you'd rather stay anonymous.

## Scope

These are in scope:

- The app in `workbench/` and `web/`, including its local server on `127.0.0.1`: another website or local process reading or changing project data.
- Opening a crafted CSV, `.xlsx` or project folder that runs code, reads or writes files outside the project, or puts live formulas into exports.
- The Windows download and installer, the build scripts and workflows that produce them, and pinned dependencies.

Out of scope are attacks that need someone who already controls your Windows account, and the SmartScreen warning shown because the setup isn't code-signed yet.

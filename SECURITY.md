# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately through GitHub: go to the repository's **Security** tab
and choose **Report a vulnerability**. Include:

- what the problem is and what an attacker could do with it
- the steps to reproduce it
- the Sushi version and your operating system

You should get a reply within 7 days. Once the problem is confirmed, a fix is
prepared and released, and you are credited in the release notes unless you
would rather not be.

## Scope

Particularly important areas:

- API keys being written to disk, logs or the database
- personal details (email, phone, CV contents) reaching a model or a third
  party outside the form being filled
- an application being submitted without the user's approval
- code running from a job page or downloaded file

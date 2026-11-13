# Keys, secrets and the spreadsheet's sharing list

An internal tool on Google Sheets has three secrets and one setting that matter more than any code:

| What | Gives | Where it belongs |
|---|---|---|
| Service-account key (JSON) | everything the service account can do on every spreadsheet shared with it | the deployment platform's secret store |
| Session signing secret (`sessions.new_secret()`) | the power to forge a "stay signed in" token for any account | the deployment platform's secret store |
| API keys of outside services (AI models) | spending and data access on that service | the deployment platform's secret store |
| The spreadsheet's sharing list | direct access to every row, bypassing the application | the spreadsheet's owner |

## Rules

1. **Never in the repository**, not even a private one: not in a file, a zip, a notebook output, a screenshot or the history. A private repository is shared with every collaborator, every clone and every tool given access to it.
2. **Never through chat or e-mail.** Give access to the secret store instead, or hand a key over in person.
3. **A key that leaked is rotated, not deleted.** Create a new key, deploy it, then delete the old one in Google Cloud. Deleting the file from the latest commit leaves the key in the history and in every clone.
4. **Rotate on a schedule** (Google suggests at most 90 days for service-account keys) and whenever someone with access leaves.
5. **One service account per environment.** The test copy and production have different accounts, so that a test can never write to production.

## The sharing list is the permission boundary

The application's roles apply only to people who go through the application. Anyone who can edit the spreadsheet can make themselves an administrator, overwrite a password hash or delete the audit log; anyone who can view it can read every password hash. So:

- Share the production spreadsheet with **the service account (editor) and one owner** only. Everyone else uses the application.
- Turn **"anyone with the link"** off.
- **Protect the audit log and the accounts table** (Data → Protect sheets and ranges) so that only the service account can edit them, even if more people are given access later.
- Give people who need to read raw data a **regular read-only export without the password column**, not access to production.
- **Review the sharing list and the version history** once a month.

## Rotating the session secret without signing everyone out

`sessions.issue` and `sessions.verify` accept a list of secrets: tokens are issued with the first and accepted with any.

1. Generate a new secret and deploy `[new, old]`.
2. After the token lifetime (7 days by default), deploy `[new]`.

To sign everyone out at once instead (for example after a leak), deploy `[new]` immediately.

## Checklist for a hand-over

- [ ] New service-account key created, old one deleted
- [ ] New session secret deployed
- [ ] API keys of outside services renewed
- [ ] Sharing list reduced to the service account and the new owner
- [ ] No secret left in the repository, its history, shared drives or chat

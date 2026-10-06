# Goals

Each `## ` heading is one goal. Text above the first heading is ignored.
Optional `outcome:` and `unblocks:` lines follow the heading. Any `repo#n`
ref in the section (or `owner/repo#n`) counts as named by that goal, and a
named item ranks above otherwise-equal items.

## Faster sign-up
outcome: New users finish sign-up in under a minute.
unblocks: The spring onboarding campaign, which waits on api#42.

- web#17 trims the sign-up form to three fields.
- api#42 adds email verification links.

## Reliable nightly exports
outcome: Exports finish every night without a manual retry.
unblocks: Customer reporting dashboards.

Tracked in example-org/api#88 and web#23.

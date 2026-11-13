# Testing an internal tool

## Test as every kind of person

Most holes in internal tools are rules that work for the person who wrote them and fail for someone else. For each rule, try it as each person it concerns:

| Person | Typical question |
|---|---|
| the owner of a record | can do what the role allows, and nothing after the record is closed |
| a collaborator on the record | can contribute, cannot take over |
| an unrelated user of the same role | sees and changes nothing of the record |
| a manager | can do what managers do, but not to peers or to themselves |
| a deactivated account | is signed out at once, everywhere |
| a hidden system account | does not appear anywhere ordinary users look |

`fakedata.persona_accounts` creates such accounts in a test copy; `tests/test_roles.py` and `tests/test_admin_and_audit.py` show the pattern as parametrized tests, where a refused action must also leave the data unchanged.

## Stress data

A handful of tidy records hides the cases that break pages: one person with five times the average load, deadlines on the same day, overdue items, long names, empty fields, formula-like text. Generate a larger set with `fakedata.fill_table` and `fakedata.skewed_choice`, from a fixed seed so a failure can be reproduced, and look at every page with it.

Keep a test copy of the application running next to production on another port, and make the difference impossible to miss (a coloured banner on every page of the test copy).

## Check that the tests can fail

A test that cannot fail checks nothing. For each guarantee that matters, break it on purpose (remove a lock, skip a check, write values as formulas) and confirm that some test fails. The storage guarantees of this kit were checked this way: removing the lock around id allocation, reading ids from a cache, writing Sheets values as user input, letting openpyxl store formulas, and skipping the duplicate-key check each make a test fail.

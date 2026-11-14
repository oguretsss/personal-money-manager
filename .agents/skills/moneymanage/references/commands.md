# MoneyManage commands

Pass these arguments to `scripts/moneymanage.py`. Use `--help` for the complete argument list. Amounts are in budget currency units, such as `12.50` for €12.50.

## Analysis

| Goal | Arguments |
| --- | --- |
| Verify connection | `whoami` |
| Resolve the name of a family member | `members` |
| Family expenses by category and person | `spending --start 2026-11-01 --end 2026-12-01` |
| Expenses for one family member | `spending --user-id 123 --start 2026-11-01 --end 2026-12-01` |
| Income, expenses, and current household balances | `summary --start 2026-11-01 --end 2026-12-01` |
| Monthly subscriptions and recorded payment status | `subscriptions --year 2026 --month 11` |

Run `spending` separately for each comparison period. Members with no expenses in the period are absent from `by_member`. Keep household and individual totals distinct when reporting. The `summary` cash and Space balances are all-time ledger values. The `spending` report counts expense transactions. Space transfers and investment purchases use separate ledgers.

## Transactions

| Goal | Arguments |
| --- | --- |
| Find a transaction | `find --id 42` or `find --type expense --q "groceries" --start 2026-11-01 --end 2026-12-01` |
| Filter by account | Add `--user-id 123` or use `--mine` with a personal token |
| Record an expense | `add --type expense --amount 12.50 --category "Groceries" --date 2026-11-14 --note "Market"` |
| Record income | `add --type income --amount 500 --category "Salary" --date 2026-11-14` |
| Edit an entry | `edit 42 --amount 13.50` (also `--category`, `--date`, and `--note`; `--note=` clears the note) |
| Delete an entry | `delete 42` |

`find --q` matches a literal substring in the note, and `--category` matches the exact category. Use `--page` when `has_more` is true. Sorted income is protected until its allocation is undone. When adding, check `ok` and report the returned ID or possible duplicates.

## Categories and limits

| Goal | Arguments |
| --- | --- |
| List categories with IDs and usage | `categories` |
| List category names by transaction type | `categories --type expense` or `categories --type income` |
| Create or rename a category | `category-add "Groceries" --type expense` or `category-rename 7 "Food shopping"` |
| Delete an unused category | `category-delete 7` |
| See spending against monthly limits | `limits --year 2026 --month 11` |
| Set or remove a category limit | `limit-set 7 --amount 300` or `limit-delete 7` |

Category names and limits are shared. Renaming a category affects its historical display. Deleting a category is allowed when no transactions or subscriptions use it and also removes its monthly limit.

## Savings

| Goal | Arguments |
| --- | --- |
| List Spaces and balances | `spaces` |
| See transfer history | `transfers --space-id 3 --start 2026-11-01 --end 2026-12-01` |
| Create or rename a Space | `space-add "Vacation"` or `space-rename 3 "Summer trip"` |
| Delete a Space with no transfers | `space-delete 3` |
| Record a deposit | `transfer "Vacation" --direction to_space --amount 50 --date 2026-11-14` |
| Record a withdrawal | `transfer "Vacation" --direction from_space --amount 20 --date 2026-11-14` |

Use `--user-id` on transfer history to see one member's entries. Follow `has_more` when listing transfers. A transfer requires an existing Space and withdrawals are limited by its balance. Deleting a Space with no transfer history also removes its entries from saved income allocation templates.

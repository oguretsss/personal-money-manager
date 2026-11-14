---
name: moneymanage
description: Use for live MoneyManage family-budget requests to find, add, edit, or delete transactions, list categories, or analyze spending over a period. The included client calls the MoneyManage HTTPS API.
---

# MoneyManage

Run `python <skill-directory>/scripts/moneymanage.py` with the commands below. The client reads `MONEYMANAGE_API_URL` and `MONEYMANAGE_API_TOKEN` from its environment. Each person uses their own MoneyManage user token. If setup is missing, point them to the MoneyManage README. Keep tokens out of chat, command arguments, files committed to Git, and output.

| Goal | Command arguments |
| --- | --- |
| Find a transaction | `find --id 123` or `find --type expense --q "groceries" --start 2026-11-01 --end 2026-12-01` |
| List categories | `categories --type expense` (or `income`) |
| Add a transaction | `add --type expense --amount 12.50 --category "Groceries" --date 2026-11-14 --note "Market"` |
| Edit a transaction | `edit 123 --amount 13.50` (also `--category`, `--date`, `--note`; use `--note=` to clear a note) |
| Delete a transaction | `delete 123` |
| Analyze a period | `summary --start 2026-11-01 --end 2026-12-01` |

- Find searches the shared household ledger. `--q` matches a literal substring in the note, `--category` matches the exact category, and `--mine` filters by the signed-in user. Use `--page` when `has_more` is true. For edits and deletions, find the matching ID and ask the user to resolve ambiguity before making a change. Use only fields the user requested. The API derives the actor from the token.
- Amounts are in budget currency units, such as `12.50` for €12.50. Use existing categories where possible. Supply the user's local calendar date for dates such as “today” and “yesterday”. Date ranges include the start and exclude the end. Supply both bounds to summarize or compare specific periods.
- For analysis, use `expense_total` and the expense entries in `by_category` for household spending. Use separate summaries for period comparisons. Cash and space balances are all-time values. Transaction lists can support detail or individual spending breakdowns. Check pagination before treating such totals as complete.
- Report the outcome after a successful API response, including the ID for new records. If a write loses its response, search the ledger to establish what happened before retrying. The server can reject changes to a sorted income until its allocation is undone.

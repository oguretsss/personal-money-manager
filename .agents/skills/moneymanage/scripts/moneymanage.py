import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ClientError(Exception):
    pass


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_api(method, path, *, query=None, payload=None):
    base = os.environ.get("MONEYMANAGE_API_URL", "").strip()
    token = os.environ.get("MONEYMANAGE_API_TOKEN", "").strip()
    if not base or not token:
        raise ClientError("Set MONEYMANAGE_API_URL and MONEYMANAGE_API_TOKEN first.")
    if not token.isascii() or any(character.isspace() for character in token):
        raise ClientError("MONEYMANAGE_API_TOKEN must be a single bearer token.")

    try:
        origin = urlsplit(base)
        origin.port  # Validate an explicit port.
    except ValueError:
        raise ClientError("MONEYMANAGE_API_URL is invalid.") from None
    local_http = origin.scheme == "http" and origin.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not origin.hostname
        or (origin.scheme != "https" and not local_http)
        or origin.path not in ("", "/")
        or any(character in base for character in "?#\\")
        or origin.username is not None
        or any(character.isspace() for character in base)
    ):
        raise ClientError("MONEYMANAGE_API_URL must be an HTTPS origin or a loopback HTTP origin.")

    url = base.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    handlers = [RejectRedirects()]
    if local_http:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    try:
        with opener.open(Request(url, data=body, headers=headers, method=method), timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        status = error.code
        with error:
            detail = api_error_message(error, token)
        raise ClientError(f"MoneyManage returned HTTP {status}. {detail}") from None
    except (URLError, HTTPException, OSError, ValueError):
        message = "MoneyManage did not return a usable response."
        if method != "GET":
            message += " Check the ledger before retrying this write."
        raise ClientError(message) from None


def api_error_message(error, token):
    if error.code == 401:
        return "Check the personal token and whether the user is still active."
    if 300 <= error.code < 400:
        return "Configure the final API origin. Redirects are disabled."
    if error.code in (400, 403, 404, 409):
        try:
            body = json.loads(error.read(8192))
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(detail, str):
                return detail.replace(token, "[redacted]")
        except (HTTPException, OSError, ValueError):
            pass
    if error.code >= 500:
        return "Check the ledger before retrying a write."
    return "Check the endpoint, requested fields, and token scopes."


def positive_int(value):
    number = int(value)
    if not 0 < number < 2**63:
        raise argparse.ArgumentTypeError("expected a positive integer within the API range")
    return number


def money(value):
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("expected a decimal amount") from None
    if not amount.is_finite() or not 0 < amount or amount.as_tuple().exponent < -2:
        raise argparse.ArgumentTypeError("expected a positive amount with at most two decimal places")
    number = float(amount)
    if not 0 < number * 100 < 2**63:
        raise argparse.ArgumentTypeError("amount exceeds the API range")
    return number


def add_transaction_fields(parser, *, required):
    parser.add_argument("--amount", required=required, type=money)
    parser.add_argument("--category", dest="category_name", required=required)
    parser.add_argument("--date", dest="happened_at", type=date.fromisoformat)
    parser.add_argument("--note")


def add_date_range(parser, *, required=False):
    parser.add_argument("--start", type=date.fromisoformat, required=required, help="inclusive date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, required=required, help="exclusive date, YYYY-MM-DD")


def add_pagination(parser):
    parser.add_argument("--page", type=positive_int)
    parser.add_argument("--per-page", type=positive_int)


def add_month(parser):
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int, choices=range(1, 13))


def add_budget_commands(commands):
    commands.add_parser("members", help="list family members and their IDs")
    commands.add_parser("whoami", help="show the connected account and granted permissions")

    spending = commands.add_parser("spending", help="summarize expenses by member and category")
    add_date_range(spending, required=True)
    spending.add_argument("--user-id", type=positive_int)

    for resource in ("category", "space"):
        create = commands.add_parser(f"{resource}-add", help=f"create a shared {resource}")
        create.add_argument("name")
        if resource == "category":
            create.add_argument("--type", choices=("income", "expense"), required=True)

        rename = commands.add_parser(f"{resource}-rename", help=f"rename a shared {resource}")
        rename.add_argument("id", type=positive_int)
        rename.add_argument("name")
        delete = commands.add_parser(f"{resource}-delete", help=f"delete an unused {resource}")
        delete.add_argument("id", type=positive_int)

    commands.add_parser("spaces", help="list shared savings spaces and current balances")
    transfers = commands.add_parser("transfers", help="list savings transfers")
    transfers.add_argument("--space-id", type=positive_int)
    transfers.add_argument("--user-id", type=positive_int)
    add_date_range(transfers)
    add_pagination(transfers)

    transfer = commands.add_parser("transfer", help="record a ledger movement to or from an existing savings space")
    transfer.add_argument("space_name")
    transfer.add_argument("--direction", choices=("to_space", "from_space"), required=True)
    transfer.add_argument("--amount", type=money, required=True)
    transfer.add_argument("--date", dest="happened_at", type=date.fromisoformat)
    transfer.add_argument("--note")

    add_month(commands.add_parser("limits", help="show monthly category limits and progress"))
    set_limit = commands.add_parser("limit-set", help="set an expense category's monthly limit")
    set_limit.add_argument("id", type=positive_int)
    set_limit.add_argument("--amount", type=money, required=True)
    delete_limit = commands.add_parser("limit-delete", help="remove a category's monthly limit")
    delete_limit.add_argument("id", type=positive_int)

    add_month(commands.add_parser("subscriptions", help="show recorded recurring expenses and payment status"))


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the family budget with the MoneyManage API.")
    commands = parser.add_subparsers(dest="command", required=True)

    find = commands.add_parser("find", help="find transactions and their IDs")
    find.add_argument("--id", type=positive_int)
    find.add_argument("--type", choices=("income", "expense"))
    find.add_argument("--category")
    find.add_argument("--q", help="literal substring in the note")
    owner = find.add_mutually_exclusive_group()
    owner.add_argument("--mine", action="store_true", default=None)
    owner.add_argument("--user-id", type=positive_int)
    add_pagination(find)

    summary = commands.add_parser("summary", help="get household totals and category breakdowns")
    for period in (find, summary):
        add_date_range(period)

    categories = commands.add_parser("categories", help="list available categories")
    categories.add_argument("--type", choices=("income", "expense"), help="return names using the original category listing")

    add = commands.add_parser("add", help="create a transaction")
    add.add_argument("--type", choices=("income", "expense"), required=True)
    add_transaction_fields(add, required=True)
    add.add_argument("--allow-duplicate", action="store_true", help="record an expense despite a duplicate warning")

    edit = commands.add_parser("edit", help="change selected fields of a transaction")
    edit.add_argument("id", type=positive_int)
    add_transaction_fields(edit, required=False)

    delete = commands.add_parser("delete", help="delete a transaction by ID")
    delete.add_argument("id", type=positive_int)
    add_budget_commands(commands)
    return parser


READ_ENDPOINTS = {
    "find": "/transactions",
    "members": "/family/members",
    "whoami": "/family/me",
    "spending": "/spending",
    "spaces": "/spaces",
    "transfers": "/spaces/transfers",
    "limits": "/limits",
    "subscriptions": "/subscriptions",
}


def run_command(args):
    fields = {key: value for key, value in vars(args).items() if key != "command" and value is not None}
    start, end = fields.get("start"), fields.get("end")
    if start and end and start >= end:
        raise ClientError("--end must be after --start.")
    if args.command in ("limits", "subscriptions") and ("year" in fields) != ("month" in fields):
        raise ClientError("Supply both --year and --month, or omit both for the current month.")
    if args.command in READ_ENDPOINTS:
        return request_api("GET", READ_ENDPOINTS[args.command], query=fields)
    if args.command == "summary":
        if (start is None) != (end is None):
            raise ClientError("Supply both --start and --end, or omit both for the current month.")
        return request_api("GET", "/summary", query={
            key: f"{value.isoformat()}T00:00:00" for key, value in fields.items()
        })
    if args.command == "categories":
        return request_api("GET", "/categories" if args.type else "/categories/details", query=fields)
    if args.command == "delete":
        return request_api("DELETE", f"/transactions/{args.id}")
    if args.command.startswith(("category-", "space-", "limit-")):
        resource, action = args.command.split("-")
        collection = {"category": "categories", "space": "spaces", "limit": "limits"}[resource]
        endpoint = f"/{collection}"
        if action == "add":
            return request_api("POST", endpoint, query=fields)
        identifier = fields.pop("id")
        method = "DELETE" if action == "delete" else "PUT"
        return request_api(method, f"{endpoint}/{identifier}", payload=fields or None)

    if happened_at := fields.get("happened_at"):
        fields["happened_at"] = f"{happened_at.isoformat()}T00:00:00"
    if args.command == "transfer":
        return request_api("POST", "/spaces/transfer", query={"existing_only": "true"}, payload=fields)
    if args.command == "add":
        allow_duplicate = fields.pop("allow_duplicate")
        if args.type == "expense":
            fields["check_duplicates"] = not allow_duplicate
        return request_api("POST", "/transactions", payload=fields)
    fields.pop("id")
    if not fields:
        raise ClientError("Supply at least one field to edit.")
    return request_api("PATCH", f"/transactions/{args.id}", payload=fields)


def main():
    args = build_parser().parse_args()
    try:
        result = run_command(args)
    except ClientError as error:
        print(error, file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

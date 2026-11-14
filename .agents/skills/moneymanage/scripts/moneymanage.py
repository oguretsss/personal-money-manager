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


def build_parser():
    parser = argparse.ArgumentParser(description="Use the MoneyManage API with a personal token.")
    commands = parser.add_subparsers(dest="command", required=True)

    find = commands.add_parser("find", help="find transactions and their IDs")
    find.add_argument("--id", type=positive_int)
    find.add_argument("--type", choices=("income", "expense"))
    find.add_argument("--category")
    find.add_argument("--q", help="literal substring in the note")
    find.add_argument("--mine", action="store_true", default=None)
    find.add_argument("--page", type=positive_int)
    find.add_argument("--per-page", type=positive_int)

    summary = commands.add_parser("summary", help="get household totals and category breakdowns")
    for period in (find, summary):
        period.add_argument("--start", type=date.fromisoformat, help="inclusive date, YYYY-MM-DD")
        period.add_argument("--end", type=date.fromisoformat, help="exclusive date, YYYY-MM-DD")

    categories = commands.add_parser("categories", help="list available categories")
    categories.add_argument("--type", choices=("income", "expense"), required=True)

    add = commands.add_parser("add", help="create a transaction")
    add.add_argument("--type", choices=("income", "expense"), required=True)
    add_transaction_fields(add, required=True)

    edit = commands.add_parser("edit", help="change selected fields of a transaction")
    edit.add_argument("id", type=positive_int)
    add_transaction_fields(edit, required=False)

    delete = commands.add_parser("delete", help="delete a transaction by ID")
    delete.add_argument("id", type=positive_int)
    return parser


def run_command(args):
    fields = {key: value for key, value in vars(args).items() if key != "command" and value is not None}
    if args.command == "find":
        return request_api("GET", "/transactions", query=fields)
    if args.command == "summary":
        if (args.start is None) != (args.end is None):
            raise ClientError("Supply both --start and --end, or omit both for the current month.")
        if args.start and args.start >= args.end:
            raise ClientError("--end must be after --start.")
        return request_api("GET", "/summary", query={
            key: f"{value.isoformat()}T00:00:00" for key, value in fields.items()
        })
    if args.command == "categories":
        return request_api("GET", "/categories", query=fields)
    if args.command == "delete":
        return request_api("DELETE", f"/transactions/{args.id}")

    if args.happened_at:
        fields["happened_at"] = f"{args.happened_at.isoformat()}T00:00:00"
    if args.command == "add":
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

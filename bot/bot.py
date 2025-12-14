import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv

from api_client import ApiClient
from states import AddTx

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
api = ApiClient()

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➖ Expense"), KeyboardButton(text="➕ Income")],
        [KeyboardButton(text="📊 Summary")],
    ],
    resize_keyboard=True,
)


@dp.message(F.text.in_({"/start", "/menu"}))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Budget bot is ready. Choose an action:", reply_markup=MAIN_KB)


@dp.message(F.text == "📊 Summary")
async def summary(message: Message):
    try:
        data = await api.summary(message.from_user.id)
    except Exception:
        await message.answer("Sorry, I can't get the summary. Are you in the allowed users list?")
        return

    lines = [
        f"Period: {data['start'][:10]} → {data['end'][:10]}",
        f"Income:  {data['income_total']:.2f}",
        f"Expense: {data['expense_total']:.2f}",
        f"Balance: {data['balance']:.2f}",
        "",
        "By category:"
    ]
    for item in data["by_category"][:12]:
        sign = "+" if item["type"] == "income" else "-"
        lines.append(f"{sign} {item['category']}: {item['total']:.2f}")

    await message.answer("\n".join(lines), reply_markup=MAIN_KB)


@dp.message(F.text.in_({"➖ Expense", "➕ Income"}))
async def begin_add(message: Message, state: FSMContext):
    tx_type = "expense" if "Expense" in message.text else "income"
    await state.update_data(type=tx_type)
    await state.set_state(AddTx.entering_amount)
    await message.answer("Enter amount (e.g. 12.50):", reply_markup=MAIN_KB)


def categories_keyboard(categories: list[str]) -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text=cat)] for cat in categories]
    buttons.append([KeyboardButton(text="✍️ Enter manually")])
    buttons.append([KeyboardButton(text="⬅️ Cancel")])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
    )

@dp.message(AddTx.entering_amount)
async def enter_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError()
    except Exception:
        await message.answer("Please enter a positive number, e.g. 12.50")
        return

    await state.update_data(amount=amount)

    data = await state.get_data()
    tx_type = data["type"]

    try:
        categories = await api.top_categories(message.from_user.id, tx_type)
    except Exception:
        categories = []

    await state.set_state(AddTx.entering_category)

    if categories:
        await message.answer(
            "Choose a category:",
            reply_markup=categories_keyboard(categories),
        )
    else:
        await message.answer("Enter category name:")



@dp.message(AddTx.entering_category)
async def enter_category(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if text == "⬅️ Cancel":
        await state.clear()
        await message.answer("Cancelled", reply_markup=MAIN_KB)
        return

    if text == "✍️ Enter manually":
        await message.answer("Enter category name:")
        return

    # категория выбрана кнопкой или введена вручную
    await state.update_data(category_name=text)
    await state.set_state(AddTx.entering_note)
    await message.answer("Optional note (or '-' to skip):", reply_markup=MAIN_KB)



@dp.message(AddTx.entering_note)
async def enter_note(message: Message, state: FSMContext):
    note = (message.text or "").strip()
    if note == "-":
        note = ""

    data = await state.get_data()
    payload = {
        "type": data["type"],
        "amount": data["amount"],
        "category_name": data["category_name"],
        "note": note,
    }

    try:
        res = await api.create_transaction(message.from_user.id, payload)
    except Exception:
        await message.answer("Failed to add transaction. Are you in the allowed users list?")
        return

    await state.clear()
    await message.answer(f"Saved ✅ (id={res['id']})", reply_markup=MAIN_KB)


def main():
    import asyncio
    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()

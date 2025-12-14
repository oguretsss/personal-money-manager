from aiogram.fsm.state import State, StatesGroup


class AddTx(StatesGroup):
    choosing_type = State()
    entering_amount = State()
    entering_category = State()
    entering_note = State()

from aiogram.fsm.state import State, StatesGroup


class AddTx(StatesGroup):
    choosing_type = State()
    entering_amount = State()
    entering_category = State()
    entering_note = State()

class SpaceTx(StatesGroup):
    choosing_direction = State()
    entering_amount = State()
    choosing_space = State()
    entering_note = State()
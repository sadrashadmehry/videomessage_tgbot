from aiogram.fsm.state import State, StatesGroup


class CropStates(StatesGroup):
    """Conversation states for the "video -> round video message" flow."""

    # Waiting for the user to send a video (default/idle state, not strictly
    # required by aiogram since handlers can be state-less, but kept
    # explicit for clarity and for the /cancel command).
    waiting_for_video = State()

    # A preview with the draggable-by-buttons crop circle is on screen and
    # we're waiting for the user to nudge/zoom/confirm/cancel.
    selecting_crop = State()

    # User tapped "Trim" and we're waiting for them to reply with a
    # start-end timestamp range (or a lone start time).
    entering_timestamps = State()

    # ffmpeg is rendering the final output; further button presses are
    # ignored (debounced) while in this state.
    processing = State()

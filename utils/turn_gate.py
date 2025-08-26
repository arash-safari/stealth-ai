import asyncio
from time import monotonic

EOU_PROB_THRESHOLD = 0.6        # only accept final turns at/above this
ANTICIPATION_SILENCE_MS = 0  # 3 seconds

class TurnGate:
    def __init__(self):
        self.awaiting_user = False     # after assistant asks, set True
        self.user_speaking = False
        self.last_speech_end_ms = None
        self.anticipating = False
        self._silence_timer = None
        self._final_text = None
        self._final_prob = 0.0

    def assistant_asked(self):
        self.awaiting_user = True
        self.user_speaking = False
        self.anticipating = False
        self.last_speech_end_ms = None
        self._final_text = None
        self._final_prob = 0.0

    def on_vad_speech_start(self):
        # user started (or resumed) speaking → anticipate and cancel any silence timer
        self.user_speaking = True
        self.anticipating = True
        self._cancel_timer()

    def on_vad_speech_end(self):
        # user stopped speaking → start a 3s countdown before we accept the turn end
        self.user_speaking = False
        self.last_speech_end_ms = monotonic() * 1000
        self._start_timer(ANTICIPATION_SILENCE_MS)

    def on_asr_final(self, text: str, eou_prob: float):
        # store the final hypothesis and probability;
        # do NOT act yet — the silence timer will decide when to fire
        self._final_text = text
        self._final_prob = eou_prob
        # if there was no speech_start (rare), still require the 3s wait:
        if not self.anticipating:
            self._start_timer(ANTICIPATION_SILENCE_MS)

    def _start_timer(self, ms: int):
        self._cancel_timer()
        self._silence_timer = asyncio.create_task(self._after_silence(ms))

    def _cancel_timer(self):
        if self._silence_timer and not self._silence_timer.done():
            self._silence_timer.cancel()
        self._silence_timer = None

    async def _after_silence(self, ms: int):
        try:
            await asyncio.sleep(ms / 1000)
        except asyncio.CancelledError:
            return
        # Only now is the user turn considered complete
        if self.awaiting_user and not self.user_speaking and self._final_text:
            if self._final_prob >= EOU_PROB_THRESHOLD:
                self.awaiting_user = False
                await self.invoke_planner_with(self._final_text)
            # else: low confidence → keep listening (do nothing)

    async def invoke_planner_with(self, final_text: str):
        # YOUR call into the planner here; this is the only place that triggers action
        pass

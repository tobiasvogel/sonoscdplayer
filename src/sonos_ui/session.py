"""Side-effect-free UI session; speaker selection only emits an intent."""
from dataclasses import dataclass, field, replace
from .model import DisplayModel
from .navigation import NavigationState, step, project, SelectionIntent, selection_acknowledged

@dataclass
class UISession:
    model: DisplayModel = field(default_factory=DisplayModel)
    navigation: NavigationState = field(default_factory=NavigationState)

    def update_status(self, new_model: DisplayModel):
        # Preserve menu while updating observed playback status. Never copy stale
        # metadata from an old disc; adapter decides the current source fields.
        self.model = replace(new_model, speakers=self.model.speakers,
                             selected_index=self.model.selected_index)

    def set_speaker_names(self, names):
        # Names are UI demonstration data only; real discovery/UID mapping is
        # intentionally out of scope. Never claim selection was applied.
        self.model = replace(self.model, speakers=tuple(names))
        if self.navigation.focus_index >= len(names):
            self.navigation = replace(self.navigation, focus_index=max(0,len(names)-1))

    def input(self, action, now_ms):
        result = step(self.model, self.navigation, action, now_ms=now_ms)
        self.navigation = result.navigation
        return result.intent

    def snapshot(self):
        return project(self.model, self.navigation)

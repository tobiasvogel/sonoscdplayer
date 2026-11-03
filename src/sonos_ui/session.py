"""Side-effect-free UI session; speaker selection only emits an intent."""
from dataclasses import dataclass, field, replace
from .model import DisplayModel
from .navigation import NavigationState, step, project, SelectionIntent, selection_acknowledged
from .checkbox_selection import CheckboxSelection, SpeakerChoice, TargetChoice

@dataclass
class UISession:
    model: DisplayModel = field(default_factory=DisplayModel)
    navigation: NavigationState = field(default_factory=NavigationState)
    checkbox_selection: CheckboxSelection | None = None
    speaker_choices: tuple[SpeakerChoice, ...] = ()

    def update_status(self, new_model: DisplayModel):
        # Preserve menu while updating observed playback status. Never copy stale
        # metadata from an old disc; adapter decides the current source fields.
        self.model = replace(new_model, speakers=self.model.speakers,
                             selected_index=self.model.selected_index,
                             checkbox_mode=self.model.checkbox_mode,
                             checked_indices=self.model.checked_indices)

    def set_speaker_names(self, names):
        # Legacy demo/single-target menu; no UID is inferred from a name.
        self.speaker_choices = ()
        self.checkbox_selection = None
        self.model = replace(self.model, speakers=tuple(names),
                             checkbox_mode=False, checked_indices=())
        if self.navigation.focus_index >= len(names):
            self.navigation = replace(self.navigation, focus_index=max(0,len(names)-1))

    def set_speaker_choices(self, choices):
        """Opt into UID-backed checkbox UI using read-only discovery records."""
        choices = tuple(choices)
        if any(not isinstance(item, SpeakerChoice) for item in choices):
            raise ValueError('expected SpeakerChoice entries')
        if len({item.uid for item in choices}) != len(choices):
            raise ValueError('duplicate speaker UID')
        if self.checkbox_selection is None:
            self.checkbox_selection = CheckboxSelection()
        self.speaker_choices = choices
        self.model = replace(self.model, speakers=tuple(item.name for item in choices),
                             checkbox_mode=True)
        if self.navigation.focus_index >= len(choices):
            self.navigation = replace(self.navigation, focus_index=max(0, len(choices)-1))
        self._sync_checkboxes()

    def _sync_checkboxes(self):
        if self.checkbox_selection is None:
            return
        checked = set(self.checkbox_selection.checked_uids)
        self.model = replace(self.model,
                             checked_indices=tuple(i for i, speaker in enumerate(self.speaker_choices)
                                                   if speaker.uid in checked))

    def input(self, action, now_ms):
        if self.checkbox_selection is not None:
            if action == 'apply_selection':
                # A separate, explicit confirmation action. It only returns a
                # plan; the application worker must send set-target and ACK it.
                if not self.navigation.menu_open:
                    raise ValueError('speaker menu must be open')
                return self.checkbox_selection.preview(self.speaker_choices)
            if action == 'open_speakers' or (action == 'confirm' and not self.navigation.menu_open):
                self.checkbox_selection.begin()
                self._sync_checkboxes()
            elif action == 'back' and self.navigation.menu_open:
                self.checkbox_selection.cancel()
                self._sync_checkboxes()
            elif action == 'confirm' and self.navigation.menu_open:
                i = self.navigation.focus_index
                if not 0 <= i < len(self.speaker_choices):
                    return None
                self.checkbox_selection.toggle(self.speaker_choices[i].uid)
                self._sync_checkboxes()
                return None
        result = step(self.model, self.navigation, action, now_ms=now_ms)
        self.navigation = result.navigation
        return result.intent

    def acknowledge_selection(self, plan: TargetChoice, *, now_ms: int):
        """ONLY after successful player set-target RPC; never starts playback."""
        if self.checkbox_selection is None:
            raise RuntimeError('UID checkbox selection inactive')
        self.checkbox_selection.acknowledge(plan)
        self.navigation = selection_acknowledged(self.navigation, now_ms=now_ms)
        self._sync_checkboxes()

    def snapshot(self):
        return project(self.model, self.navigation)

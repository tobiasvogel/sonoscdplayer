/* SonosCDPlayer: approved light/petrol and dark/warm UI with live, bounded JSON API. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  let theme = 'auto';
  let coverKey = '';
  let pending = false;
  const darkMedia = window.matchMedia('(prefers-color-scheme: dark)');


  // WEB I18N: one catalog shared with the device display, loaded over loopback.
  let language = 'de';
  let messages = Object.create(null);
  function tr(key, values = {}) {
    const template = messages[key];
    if (typeof template !== 'string') return key;
    return template.replace(/\{([a-z][a-z0-9_]*)\}/gi, (match, field) =>
      Object.prototype.hasOwnProperty.call(values, field) ? String(values[field]) : match);
  }
  function translatePage() {
    for (const element of document.querySelectorAll('[data-i18n]')) {
      element.textContent = tr(element.getAttribute('data-i18n'));
    }
    for (const attribute of ['aria-label', 'title', 'alt', 'placeholder']) {
      const key = 'data-i18n-' + attribute;
      for (const element of document.querySelectorAll('[' + key + ']')) {
        element.setAttribute(attribute, tr(element.getAttribute(key)));
      }
    }
  }
  async function loadLanguage() {
    const reply = await request('/api/i18n');
    if (!reply || typeof reply.language !== 'string' || !reply.strings ||
        typeof reply.strings !== 'object' || Array.isArray(reply.strings)) {
      throw new Error('invalid i18n response');
    }
    language = reply.language;
    messages = reply.strings;
    document.documentElement.lang = language;
    translatePage();
  }

  function toast(message) {
    const box = $('#toast');
    box.textContent = message;
    box.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => box.classList.remove('show'), 4200);
  }

  async function request(url, payload) {
    const options = {cache: 'no-store'};
    if (payload !== undefined) {
      options.method = 'POST';
      options.headers = {'Content-Type': 'application/json'};
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok || body.ok === false) {
      throw new Error(body.error || `HTTP ${response.status}`);
    }
    return body;
  }

  function effectiveTheme() { return theme === 'auto' ? (darkMedia.matches ? 'dark' : 'light') : theme; }
  function paintTheme() {
    const actual = effectiveTheme();
    document.documentElement.dataset.themeMode = theme;
    document.documentElement.dataset.theme = actual;
    document.querySelectorAll('[data-theme-choice]').forEach(btn => {
      const selected = btn.dataset.themeChoice === theme;
      btn.classList.toggle('active', selected);
      btn.setAttribute('aria-pressed', String(selected));
    });
    $('#theme-setting').value = theme;
    $('#theme-example-label').textContent = tr('web.text.current_theme') +
      (actual === 'dark' ? tr('web.text.dark_espresso_amber_2') : tr('web.text.light_teal_2')) +
      (theme === 'auto' ? tr('web.text.automatic_2') : '');
  }
  async function saveTheme(mode) {
    if (!['auto', 'light', 'dark'].includes(mode)) return;
    const previous = theme;
    theme = mode;
    paintTheme();
    try {
      await request('/api/theme', {theme: mode});
    } catch (err) {
      theme = previous;
      paintTheme();
      toast(tr('web.text.theme_could_not_be_saved') + err.message);
    }
  }
  async function loadTheme() {
    try {
      const result = await request('/api/theme');
      if (['auto', 'light', 'dark'].includes(result.theme)) theme = result.theme;
      paintTheme();
    } catch (_) { paintTheme(); }
  }
  darkMedia.addEventListener?.('change', () => { if (theme === 'auto') paintTheme(); });

  function showPage(name) {
    document.querySelectorAll('.navbtn').forEach(button => button.classList.toggle('active', button.dataset.page === name));
    document.querySelectorAll('.screen').forEach(screen => screen.classList.toggle('active', screen.id === name));
    $('#pagetitle').textContent = name === 'playback' ? tr('web.text.now_playing') : tr('web.text.settings');
    $('#eyebrow').textContent = name === 'playback' ? tr('web.text.listen_to_music') : tr('web.text.manage_device');
    if (name === 'settings') loadSettings();
  }
  function showPanel(panel) {
    document.querySelectorAll('.settings-nav button').forEach(button => button.classList.toggle('active', button.dataset.panel === panel));
    document.querySelectorAll('.subpanel').forEach(section => section.classList.toggle('active', section.id === panel));
    showPage('settings');
    if (panel === 'library') openLibrary();
  }
  // WEB-05b/05c: playback and display language use separate revisions.
  let settingsBusy = false;
  let playbackRevision = null;
  let playbackEditable = false;
  let languageRevision = null;
  let coverRevision = null;
  let inputRevision = null;
  let inputEditable = false;
  let inputResettable = false;
  const inputSpecs = [
    ['gpio.poll_interval_ms', 'input-poll', 1, 1000],
    ['volume.repeat_delay_ms', 'input-repeat-delay', 100, 5000],
    ['volume.repeat_interval_ms', 'input-repeat-interval', 50, 2000],
    ['encoder_input.button_debounce_ms', 'input-debounce', 0, 250],
    ['encoder_input.steps_per_detent', 'input-steps', 1, 4],
    ['encoder_input.invert_direction', 'input-invert', null, null]
  ];
  const playbackKeys = ['sonos.use_queue', 'playback.continue', 'streaming.max_buffer_mib'];
  function settingsText(value) {
    if (value === null || value === undefined) return tr('web.settings.unknown');
    if (value === true) return tr('web.settings.yes');
    if (value === false) return tr('web.settings.no');
    if (Array.isArray(value)) return value.join(', ');
    return String(value);
  }
  function installPlaybackControls() {
    const footer = $('#audio .panel-footer');
    if (!footer) return;
    let save = $('#playback-settings-save');
    if (!save) {
      save = footer.querySelector('button');
      save.id = 'playback-settings-save';
      save.removeAttribute('data-i18n');
      save.addEventListener('click', () => editPlaybackSettings('save'));
      const reset = document.createElement('button');
      reset.id = 'playback-settings-reset';
      reset.className = 'btn secondary';
      reset.type = 'button';
      reset.addEventListener('click', () => editPlaybackSettings('reset'));
      footer.appendChild(reset);
    }
    save.textContent = tr('web.settings.save_playback');
    save.disabled = !playbackEditable;
    const reset = $('#playback-settings-reset');
    reset.textContent = tr('web.settings.reset_playback');
    reset.disabled = !playbackEditable;
    let status = $('#playback-settings-status');
    if (!status) {
      status = document.createElement('p');
      status.id = 'playback-settings-status';
      status.className = 'help';
      status.setAttribute('role', 'status');
      $('#audio').insertBefore(status, $('#audio .fieldgrid'));
    }
  }
  async function loadPlaybackRuntime(snapshot) {
    const status = $('#playback-settings-status');
    if (!status) return;
    try {
      const answer = await request('/api/settings/runtime');
      const runtime = answer.values;
      if (!runtime) { status.textContent = tr('web.settings.runtime_unconfirmed'); return; }
      const byKey = new Map(snapshot.settings.map(row => [row.key, row]));
      const changed = playbackKeys.some(key => byKey.get(key)?.value !== runtime[key]);
      status.textContent = changed ? tr('web.settings.restart_needed') : tr('web.settings.runtime_applied');
    } catch (_) {
      status.textContent = tr('web.settings.runtime_unconfirmed');
    }
  }
  async function editPlaybackSettings(action) {
    if (!playbackEditable || playbackRevision === null) return;
    if (action === 'reset' && !window.confirm(tr('web.settings.reset_confirm'))) return;
    const values = action === 'save' ? {
      'sonos.use_queue': $('#queue').value === 'true',
      'playback.continue': $('#continue').value === 'true',
      'streaming.max_buffer_mib': Number($('#buffer').value)
    } : {};
    if (action === 'save' && (!['true', 'false'].includes($('#queue').value) ||
        !['true', 'false'].includes($('#continue').value) ||
        !Number.isInteger(values['streaming.max_buffer_mib']) ||
        values['streaming.max_buffer_mib'] < 16 || values['streaming.max_buffer_mib'] > 512)) {
      toast(tr('web.settings.invalid_playback')); return;
    }
    $('#playback-settings-save').disabled = true;
    $('#playback-settings-reset').disabled = true;
    try {
      await request('/api/settings/playback', {action, revision: playbackRevision, values});
      toast(tr(action === 'save' ? 'web.settings.playback_saved' : 'web.settings.playback_reset'));
    } catch (error) {
      toast(tr('web.settings.save_failed') + error.message);
    } finally {
      await loadSettings();
    }
  }
  async function saveDisplayLanguage() {
    if (languageRevision === null || !['de', 'en'].includes($('#lang').value)) return;
    const selected = $('#lang').value;
    $('#save-display-language').disabled = true;
    try {
      await request('/api/settings/language', {language: selected, revision: languageRevision});
      await loadLanguage();
      toast(tr('web.settings.language_saved'));
    } catch (error) {
      toast(tr('web.settings.save_failed') + error.message);
    }
    await loadSettings();
  }
  async function saveCoverMode(reset = false) {
    if (coverRevision === null || (!reset && coverRevision === 'invalid')) return;
    const mode = reset ? null : $('#cover').value;
    if (mode !== null && !['auto', 'on', 'off'].includes(mode)) return;
    if (reset && coverRevision === 'invalid' &&
        !window.confirm(tr('web.settings.cover_corrupt_reset_confirm'))) return;
    $('#save-cover-mode').disabled = true;
    $('#reset-cover-mode').disabled = true;
    try {
      await request('/api/settings/cover', {mode, revision: coverRevision});
      toast(tr('web.settings.cover_saved'));
    } catch (error) {
      toast(tr('web.settings.save_failed') + error.message);
    } finally {
      await loadSettings();
    }
  }
  function renderInputSettings(snapshot) {
    const section = snapshot.input;
    const values = section && section.values;
    inputRevision = typeof section?.revision === 'string' ? section.revision : null;
    inputEditable = section?.editable === true && inputRevision !== null &&
      inputRevision !== 'invalid' && values && typeof values === 'object' &&
      !Array.isArray(values) && inputSpecs.every(([key]) =>
        Object.prototype.hasOwnProperty.call(values, key));
    inputResettable = section?.resettable === true && inputRevision === 'invalid';
    for (const [key, id] of inputSpecs) {
      const field = $('#' + id);
      field.disabled = !inputEditable;
      field.value = inputEditable ? String(values[key]) : '';
    }
    $('#input-settings-save').disabled = !inputEditable;
    $('#input-settings-reset').disabled = !(inputEditable || inputResettable);
  }
  async function editInputSettings(action) {
    if (inputRevision === null ||
        (action === 'save' && !inputEditable) ||
        (action === 'reset' && !(inputEditable || inputResettable))) return;
    if (action === 'reset' && !window.confirm(tr('web.settings.input_reset_confirm'))) return;
    const values = {};
    if (action === 'save') {
      for (const [key, id, low, high] of inputSpecs) {
        const raw = $('#' + id).value;
        if (key === 'encoder_input.invert_direction') {
          if (raw !== 'true' && raw !== 'false') {
            toast(tr('web.settings.input_invalid')); return;
          }
          values[key] = raw === 'true';
        } else {
          if (!/^[0-9]+$/.test(raw) || !Number.isSafeInteger(Number(raw)) ||
              Number(raw) < low || Number(raw) > high ||
              (key === 'encoder_input.steps_per_detent' && ![1, 2, 4].includes(Number(raw)))) {
            toast(tr('web.settings.input_invalid')); return;
          }
          values[key] = Number(raw);
        }
      }
    }
    $('#input-settings-save').disabled = true;
    $('#input-settings-reset').disabled = true;
    try {
      await request('/api/settings/input', {action, revision: inputRevision, values});
      toast(tr(action === 'reset' ? 'web.settings.input_reset' : 'web.settings.input_saved'));
    } catch (error) {
      toast(tr('web.settings.save_failed') + error.message);
    } finally {
      await loadSettings();
    }
  }
  function settingsOrigin(row) {
    const source = ({config: 'web.settings.config', state: 'web.settings.state',
      default: 'web.settings.default', invalid: 'web.settings.invalid',
      unknown: 'web.settings.unknown'})[row.source];
    const label = tr(source || 'web.settings.unknown') +
      (row.origin_file ? ' (' + row.origin_file + ')' : '');
    return row.source === 'state' ? label + ' · ' + tr('web.settings.base') + ': ' +
      settingsText(row.base) : label;
  }
  function settingsField(id, row, optionValues) {
    const element = $(id);
    if (!element) return;
    if (!row) {
      element.value = ''; element.disabled = true;
      element.closest('.field')?.querySelector('.settings-field-origin')?.remove();
      return;
    }
    if (optionValues) {
      optionValues.forEach((value, index) => {
        if (element.options[index]) element.options[index].value = String(value);
      });
    }
    element.value = row.value === null ? '' : String(row.value);
    if (row.value !== null && element.tagName === 'SELECT' && element.selectedIndex < 0) {
      const option = document.createElement('option');
      option.value = String(row.value); option.textContent = String(row.value);
      element.appendChild(option); element.value = option.value;
    }
    element.disabled = !playbackEditable || !['#queue', '#continue', '#buffer'].includes(id);
    const parent = element.closest('.field');
    if (!parent) return;
    let note = parent.querySelector('.settings-field-origin');
    if (!note) {
      note = document.createElement('small');
      note.className = 'help settings-field-origin';
      parent.appendChild(note);
    }
    note.textContent = settingsOrigin(row);
  }
  function renderSettings(data) {
    document.querySelectorAll('.settings-load-error').forEach(node => node.remove());
    const rows = Array.isArray(data.settings) ? data.settings : [];
    const byKey = new Map(rows.map(row => [row.key, row]));
    playbackRevision = typeof data.playback?.revision === 'string' ? data.playback.revision : null;
    playbackEditable = data.playback?.editable === true && playbackRevision !== null &&
      playbackKeys.every(key => byKey.get(key)?.value !== null && byKey.has(key));
    languageRevision = typeof data.display?.language_revision === 'string' ? data.display.language_revision : null;
    coverRevision = typeof data.display?.cover_revision === 'string' ? data.display.cover_revision : null;
    settingsField('#queue', byKey.get('sonos.use_queue'), ['true', 'false']);
    settingsField('#continue', byKey.get('playback.continue'), ['true', 'false']);
    settingsField('#buffer', byKey.get('streaming.max_buffer_mib'));
    settingsField('#lang', byKey.get('ui.language'), ['de', 'en']);
    $('#lang').disabled = languageRevision === null || !['de', 'en'].includes(byKey.get('ui.language')?.value);
    $('#save-display-language').disabled = $('#lang').disabled;
    settingsField('#cover', byKey.get('ui.cover.mode'), ['auto', 'off', 'on']);
    const coverBaseValid = ['auto', 'on', 'off'].includes(byKey.get('ui.cover.mode')?.value);
    const coverEditable = coverRevision !== null && coverRevision !== 'invalid' && coverBaseValid;
    $('#cover').disabled = !coverEditable;
    $('#save-cover-mode').disabled = !coverEditable;
    $('#reset-cover-mode').disabled = coverRevision === null || !coverBaseValid;
    renderInputSettings(data);
    installPlaybackControls();
    let box = $('#settings-readonly');
    if (!box) {
      box = document.createElement('div'); box.id = 'settings-readonly';
      $('#system').appendChild(box);
    }
    box.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = tr('web.settings.overview'); box.appendChild(heading);
    const warning = document.createElement('p'); warning.className = 'help';
    warning.textContent = tr('web.settings.runtime_unverified'); box.appendChild(warning);
    if (Array.isArray(data.invalid_keys) && data.invalid_keys.length) {
      const invalid = document.createElement('p'); invalid.className = 'help';
      invalid.textContent = tr('web.settings.invalid_notice'); box.appendChild(invalid);
    }
    for (const file of ['player.toml', 'ui.toml', 'interface.toml', 'web.json']) {
      const entries = rows.filter(item => item.file === file);
      if (!entries.length) continue;
      const section = document.createElement('details');
      section.className = 'library-detail';
      const summary = document.createElement('summary');
      summary.textContent = file + ' (' + entries.length + ')';
      section.appendChild(summary);
      for (const row of entries) {
        const entry = document.createElement('div'); entry.className = 'record';
        const content = document.createElement('div');
        const name = document.createElement('strong'); name.textContent = row.key;
        const meta = document.createElement('small');
        meta.textContent = tr('web.settings.merged') + ': ' + settingsText(row.value) +
          ' · ' + settingsOrigin(row);
        content.append(name, meta); entry.appendChild(content);
        section.appendChild(entry);
      }
      box.appendChild(section);
    }
  }
  async function loadSettings() {
    if (settingsBusy) return;
    settingsBusy = true;
    try {
      const response = await request('/api/settings');
      renderSettings(response);
      await loadPlaybackRuntime(response);
    } catch (_) {
      // Never disguise a failed configuration read as merely empty fields.
      for (const id of ['#queue', '#continue', '#buffer', '#lang', '#cover']) {
        settingsField(id, null);
      }
      languageRevision = null;
      coverRevision = null;
      $('#save-display-language').disabled = true;
      $('#save-cover-mode').disabled = true;
      $('#reset-cover-mode').disabled = true;
      renderInputSettings({input: null});
      for (const sectionId of ['audio', 'display', 'inputs']) {
        const section = $('#' + sectionId);
        let status = section.querySelector('.settings-load-error');
        if (!status) {
          status = document.createElement('p');
          status.className = 'help settings-load-error';
          status.setAttribute('role', 'alert');
          section.insertBefore(status, section.querySelector('.fieldgrid'));
        }
        status.textContent = tr('web.settings.unavailable');
      }
      let box = $('#settings-readonly');
      if (!box) { box = document.createElement('div'); box.id = 'settings-readonly'; $('#system').appendChild(box); }
      box.textContent = tr('web.settings.unavailable');
    } finally {
      settingsBusy = false;
    }
  }
  document.querySelectorAll('.navbtn').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
  document.querySelectorAll('.settings-nav button').forEach(button => button.addEventListener('click', () => showPanel(button.dataset.panel)));
  document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', () => saveTheme(button.dataset.themeChoice)));
  $('#theme-setting').addEventListener('change', event => saveTheme(event.target.value));
  $('#save-theme').addEventListener('click', () => saveTheme($('#theme-setting').value));
  $('#save-display-language').addEventListener('click', saveDisplayLanguage);
  $('#save-cover-mode').addEventListener('click', () => saveCoverMode(false));
  $('#reset-cover-mode').addEventListener('click', () => saveCoverMode(true));
  $('#input-settings-save').addEventListener('click', () => editInputSettings('save'));
  $('#input-settings-reset').addEventListener('click', () => editInputSettings('reset'));
  $('#edit-metadata').addEventListener('click', () => showPanel('metadata'));

  function formattedTime(number) {
    if (typeof number !== 'number' || !Number.isFinite(number) || number < 0) return '—';
    const seconds = Math.floor(number);
    return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
  }

  // These values are cached player status, not an invitation to run a new lookup.
  // Unknown future status codes must never be displayed as success.
  function renderMetadataSourceStatus(metadata, noDisc = false) {
    const statusKeys = {
      cdtext: {available: 'available', unavailable: 'unavailable', error: 'error',
        not_found: 'not_found', absent: 'not_found'},
      lookup: {unique: 'unique', ambiguous: 'ambiguous', not_found: 'not_found',
        unavailable: 'unavailable', offline: 'offline', error: 'error'},
      match: {toc: 'toc', cached: 'cached', manual: 'manual', gnudb: 'gnudb',
        consensus: 'consensus', majority: 'majority', unique: 'unique',
        ambiguous: 'ambiguous', none: 'not_found'}
    };
    for (const [kind, property, element] of [
      ['cdtext', 'cdtext_status', '#metadata-cdtext'],
      ['lookup', 'lookup_status', '#metadata-lookup'],
      ['match', 'match_status', '#metadata-match']
    ]) {
      const raw = !noDisc && metadata && typeof metadata[property] === 'string'
        ? metadata[property] : null;
      const name = noDisc ? 'no_disc' : (Object.prototype.hasOwnProperty.call(statusKeys[kind], raw) ? statusKeys[kind][raw] : 'unknown');
      $(element).textContent = tr('web.metadata_status.' + name);
    }
  }

  function renderStatus(state) {
    const player = state.player || {};
    const metadata = state.metadata || {};
    const mode = player.mode || 'STOPPED';
    const playing = mode === 'PLAYING';
    const paused = mode === 'PAUSED';
    const active = playing || paused || mode === 'STARTING';
    const current = Number(player.track) || 0;
    const count = Math.max(0, Math.min(99, Number(player.track_count) || 0));
    const noDisc = mode === 'NO_DISC' || state.disc?.mode === 'NO_DISC';
    renderMetadataSourceStatus(metadata, noDisc);
    const album = noDisc ? tr('web.text.no_cd_inserted') : (metadata.album || (count ? tr('web.text.audio_cd') : tr('web.text.no_cd_detected')));
    const title = noDisc ? tr('web.text.no_playback') : (metadata.track_title || (current ? tr('web.dynamic.track_number', {number: current}) : tr('web.text.no_playback')));
    const artist = noDisc ? '—' : (metadata.track_artist || metadata.artist || '—');
    $('#player-status').textContent = ({NO_DISC:tr('web.text.no_cd_2'), PLAYING:tr('web.text.playing'), PAUSED:tr('web.text.paused'), STARTING:tr('web.text.starting'), STOPPED:tr('web.text.stopped'), ERROR:tr('web.text.error')})[mode] || mode;
    $('#track-title').textContent = title;
    $('#track-artist').textContent = artist;
    $('#track-info').textContent = noDisc ? tr('web.text.no_cd_inserted') : (count ? tr('web.dynamic.track_position', {current: current || 1, count, album}) : tr('web.text.no_playable_cd'));
    $('#art-album').textContent = noDisc ? album : tr('web.dynamic.album_by_artist', {album, artist: metadata.artist || tr('web.text.unknown_artist')});
    $('#library-current').textContent = album;

    $('#delete-disc').disabled = pending || !count;

    const progress = state.progress || {};
    const elapsed = Number(progress.elapsed_seconds ?? progress.elapsed ?? progress.position);
    const duration = Number(progress.duration_seconds ?? progress.total ?? progress.duration);
    const valid = active && Number.isFinite(elapsed) && elapsed >= 0 && Number.isFinite(duration) && duration > 0;
    $('#elapsed').textContent = valid ? formattedTime(elapsed) : '—';
    $('#duration').textContent = valid ? formattedTime(duration) : '—';
    $('.progress').value = valid ? Math.round(Math.min(100, 100 * elapsed / duration)) : 0;

    const identity = [album, noDisc ? null : metadata.artist, !noDisc && state.has_cover, metadata.cover_seq || 0].join('|');
    if (coverKey !== identity) {
      coverKey = identity;
      const img = $('#cover-image');
      img.hidden = noDisc || !state.has_cover;
      img.src = !noDisc && state.has_cover ? '/api/cover?revision=' + encodeURIComponent(metadata.cover_seq || 0) : '';
      $('#art-placeholder').hidden = !noDisc && !!state.has_cover;
      img.onerror = () => {img.hidden = true; $('#art-placeholder').hidden = false;};
    }
    const rejection = state.last_rejection || {};
    if (rejection.seq && rejection.seq !== renderStatus.lastRejection) {
      renderStatus.lastRejection = rejection.seq;
      if (rejection.reason) toast(tr('web.text.playback_rejected') + rejection.reason);
    }
  }

  function renderDashboard(info, state) {
    const count = Math.max(0, Math.min(99, Number(state.player?.track_count) || 0));
    const current = Number(state.player?.track) || 0;
    const tracks = $('#tracks');
    const candidateRows = Array.isArray(info?.tracks) && info.track_count === count ? info.tracks : [];
    // Never show titles from a previous disc while the new disc is being read.
    const knownTitle = state.metadata?.track_title;
    const currentEntry = candidateRows[current - 1];
    const oldDisc = (currentEntry?.title && knownTitle && currentEntry.title !== knownTitle) ||
      (candidateRows.some(row => row.title) && !state.metadata?.album &&
       !state.metadata?.artist && !knownTitle);
    const rows = oldDisc ? [] : candidateRows;
    const signature = JSON.stringify([count, current, rows]);
    if (renderDashboard.lastTracks !== signature) {
      renderDashboard.lastTracks = signature;
      tracks.replaceChildren();
      if (!count) {
        const empty = document.createElement('div');
        empty.className = 'trackrow';
        empty.textContent = tr('web.text.no_track_list_available');
        tracks.appendChild(empty);
      } else {
        for (let number = 1; number <= count; number++) {
          const entry = rows[number - 1] || {};
          const row = document.createElement('div');
          row.className = 'trackrow' + (number === current ? ' active' : '');
          const num = document.createElement('span');
          num.className = 'num'; num.textContent = String(number).padStart(2, '0');
          const title = document.createElement('span');
          const artist = typeof entry.artist === 'string' ? entry.artist : '';
          title.textContent = (typeof entry.title === 'string' && entry.title ? entry.title : tr('web.dynamic.track_number', {number})) +
            (artist ? ` · ${artist}` : '');
          const duration = document.createElement('span');
          duration.className = 'duration'; duration.textContent = '—';
          row.append(num, title, duration);
          tracks.appendChild(row);
        }
      }
    }
    const freshTarget = !Number.isInteger(info?.target_rev) || !Number.isInteger(state.target_rev) ||
      info.target_rev === state.target_rev;
    const countSpeakers = freshTarget && Number.isInteger(info?.speaker_count) ? info.speaker_count : 0;
    const names = freshTarget && Array.isArray(info?.speaker_names) ?
      info.speaker_names.filter(name => typeof name === 'string') : [];
    const missing = Math.max(0, countSpeakers - names.length);
    const isPlaying = state.sonos?.owned === true && info?.playing_on_selection === true;
    $('#target-label').textContent = isPlaying ? tr('web.text.playing_on') : tr('web.text.selected_sonos_target');
    $('#target-name').textContent = !freshTarget ? tr('web.text.updating_target') :
      !countSpeakers ? tr('web.text.no_sonos_target_selected') :
      names.length ? names.join(' · ') + (missing ? tr('web.dynamic.more_speakers', {count: missing}) : '') :
      countSpeakers === 1 ? tr('web.text.1_speaker_selected') : tr('web.dynamic.speakers_selected', {count: countSpeakers});
  }

  // WEB-03c: upload bytes only after a matching, current-disc dashboard read.
  let coverBusy = false;
  function renderCoverTools(status) {
    const ready = editSynced && editToken && !coverBusy;
    $('#cover-upload').disabled = !ready;
    $('#cover-upload-label').classList.toggle('disabled', !ready);
    $('#cover-remove').disabled = !ready || status?.metadata?.cover_manual !== true;
    $('#cover-upload-status').textContent = status?.metadata?.cover_error ||
      (status?.metadata?.cover_manual ? tr('web.text.custom_cover_active_automatic_covers_do_not_overwrite') :
       tr('web.text.no_custom_cover_saved'));
  }
  $('#cover-upload').addEventListener('change', async event => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!editSynced || !editToken || coverBusy) return;
    if (!['image/jpeg', 'image/png'].includes(file.type) || file.size < 1 || file.size > 4 * 1024 * 1024) {
      toast(tr('web.text.please_select_a_jpeg_or_png_up_to')); return;
    }
    const token = editToken;
    coverBusy = true;
    try {
      const response = await fetch('/api/cover/upload', {
        method: 'POST', headers: {'Content-Type': 'application/octet-stream', 'X-Disc-Token': token}, body: file,
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || tr('web.text.upload_failed'));
      toast(tr('web.text.validating_and_saving_cover'));
      await refresh();
    } catch (error) { toast(tr('web.text.cover') + error.message); }
    finally { coverBusy = false; }
  });
  $('#cover-remove').addEventListener('click', async () => {
    if (!editSynced || !editToken || coverBusy || !window.confirm(tr('web.text.remove_the_custom_cover_for_this_cd'))) return;
    const token = editToken;
    coverBusy = true;
    try {
      await request('/api/cover/remove', {token});
      toast(tr('web.text.removing_custom_cover'));
      await refresh();
    } catch (error) { toast(tr('web.text.cover') + error.message); }
    finally { coverBusy = false; }
  });

  // WEB-03: Keep unsaved edits while the read-only dashboard keeps polling.
  let editToken = null;
  let editSnapshot = null;
  let editTrack = 0;
  let editDirty = false;
  let editBusy = false;
  let editSynced = false;
  let lastEditSeq = 0;
  const editInputs = ['#album', '#artist', '#title2', '#meta-track-artist'];

  function populateEditor() {
    const info = editSnapshot;
    if (!info) return;
    const tracks = info.details.tracks || [];
    const entry = tracks.find(item => item.number === editTrack) || {};
    const values = [info.details.album || '', info.details.artist || '',
      entry.title || '', entry.artist || ''];
    editInputs.forEach((selector, i) => { $(selector).value = values[i]; });
    editDirty = false;
    $('#meta-save').disabled = false;
    $('#meta-discard').disabled = true;
  }

  function renderEditor(status, details) {
    editSynced = false;
    // A transient dashboard timeout is not a CD change; keep the user's draft.
    if (!details && Number(status?.player?.track_count) > 0) {
      editInputs.forEach(selector => { $(selector).disabled = true; });
      $('#meta-track-select').disabled = true;
      $('#meta-save').disabled = true;
      $('#meta-discard').disabled = true;
      $('#meta-edit-status').textContent = tr('web.text.metadata_temporarily_unavailable');
      return;
    }
    const token = typeof details?.edit_token === 'string' ? details.edit_token : null;
    const count = Number(details?.track_count) || 0;
    const currentNumber = Number(status?.player?.track) || 0;
    const dashboardTitle = details?.tracks?.[currentNumber - 1]?.title;
    const mismatched = (details?.album && status?.metadata?.album &&
        details.album !== status.metadata.album) ||
      (dashboardTitle && status?.metadata?.track_title &&
        dashboardTitle !== status.metadata.track_title);
    if (mismatched) {
      // Status and dashboard are separate cached reads and may briefly disagree.
      // Keep the draft, but never allow an edit while the snapshots disagree.
      editInputs.forEach(selector => { $(selector).disabled = true; });
      $('#meta-track-select').disabled = true;
      $('#meta-save').disabled = true;
      $('#meta-discard').disabled = true;
      $('#meta-edit-status').textContent = tr('web.text.synchronizing_metadata');
      return;
    }
    const available = !!token && count > 0 && status?.player?.track_count === count;
    const newDisc = token !== editToken;
    if (newDisc) {
      if (editDirty) toast(tr('web.text.cd_changed_unsaved_changes_were_discarded'));
      editToken = token;
      editDirty = false;
      lastEditSeq = Number(details?.edit_seq) || 0;
      editTrack = 0;
    }
    editSnapshot = available ? {status, details} : null;
    editSynced = available;
    if (available && (Number(details.edit_seq) || 0) > lastEditSeq) {
      lastEditSeq = details.edit_seq;
      if (details.edit_error) toast(tr('web.text.save_failed_check_the_status'));
    }
    editInputs.forEach(selector => { $(selector).disabled = !available || editBusy; });
    $('#meta-track-select').disabled = !available || editBusy;
    $('#meta-save').disabled = !available || editBusy;
    $('#meta-discard').disabled = !available || editBusy || !editDirty;
    if (!available) {
      $('#meta-edit-status').textContent = tr('web.text.no_editable_cd_entry_available');
      $('#meta-source').textContent = status?.player?.mode === 'NO_DISC' || status?.disc?.mode === 'NO_DISC' ? tr('web.text.no_cd') : tr('web.text.unavailable');
      $('#album').placeholder = status?.player?.mode === 'NO_DISC' || status?.disc?.mode === 'NO_DISC' ? tr('web.text.no_cd_inserted') : tr('web.text.album_title');
      $('#meta-track-select').replaceChildren();
      editInputs.forEach(selector => { $(selector).value = ''; });
      return;
    }
    $('#album').placeholder = tr('web.text.album_title');
    if (newDisc || !editTrack || editTrack > count) {
      editTrack = Math.max(1, Math.min(count, Number(status.player?.track) || 1));
      const select = $('#meta-track-select');
      select.replaceChildren();
      for (let n = 1; n <= count; n++) {
        const option = document.createElement('option');
        option.value = String(n);
        option.textContent = `${n}. ${details.tracks?.[n - 1]?.title || tr('web.dynamic.track_number', {number: n})}`;
        select.appendChild(option);
      }
      select.value = String(editTrack);
    }
    if (!editDirty && !editBusy) populateEditor();
    const manual = Array.isArray(details.manual_fields) ? details.manual_fields : [];
    $('#meta-source').textContent = manual.length ? tr('web.text.manually_corrected') :
      (details.metadata_source || status.metadata?.match_status || 'TOC');
    $('#meta-edit-status').textContent = editDirty ? tr('web.text.unsaved_changes') :
      tr('web.dynamic.manual_fields_saved', {count: manual.length});
  }

  editInputs.forEach(selector => $(selector).addEventListener('input', () => {
    editDirty = true;
    $('#meta-discard').disabled = false;
    $('#meta-edit-status').textContent = tr('web.text.unsaved_changes');
  }));
  $('#meta-discard').addEventListener('click', () => populateEditor());
  $('#meta-track-select').addEventListener('change', event => {
    if (editDirty) toast(tr('web.text.unsaved_changes_were_discarded'));
    editTrack = Number(event.target.value);
    populateEditor();
  });
  $('#meta-save').addEventListener('click', async () => {
    if (!editSnapshot || !editToken || editBusy) return;
    const {status, details} = editSnapshot;
    const row = details.tracks?.find(item => item.number === editTrack) || {};
    const fields = [
      ['album', null, details.album || '', $('#album').value],
      ['artist', null, details.artist || '', $('#artist').value],
      ['track_title', editTrack, row.title || '', $('#title2').value],
      ['track_artist', editTrack, row.artist || '', $('#meta-track-artist').value],
    ];
    const edits = fields.filter(([field, n, before, after]) => before !== after)
      .map(([field, track, _before, text]) => ({token: editToken, field,
        ...(track ? {track} : {}), value: text.trim() || null}));
    if (!edits.length) { editDirty = false; toast(tr('web.text.no_changes_to_save')); return; }
    editBusy = true;
    $('#meta-save').disabled = true;
    $('#meta-discard').disabled = true;
    try {
      for (const edit of edits) await request('/api/metadata', edit);
      editDirty = false;
      toast(tr('web.text.changes_accepted_updating_display'));
      await refresh();
    } catch (error) {
      toast(tr('web.text.some_changes_were_not_saved') + error.message);
    } finally {
      editBusy = false;
    }
  });

  // WEB-03b: Explicit MusicBrainz lookup; never write disc/cache from JS.
  let releaseBusy = false;
  let releaseKey = '';
  let chosenRelease = null;
  let chosenToken = null;

  function resetReleaseDetail() {
    chosenRelease = null;
    chosenToken = null;
    $('#release-detail').hidden = true;
    $('#release-detail').replaceChildren();
    $('#release-confirm').hidden = true;
  }

  function renderReleases(details) {
    // Re-fetch cover after an edition switch even if album/artist text did not change.
    const coverEdition = details?.edit_token ? details.release_selected_id || null : null;
    if (coverEdition !== renderReleases.coverEdition) {
      renderReleases.coverEdition = coverEdition;
      coverKey = '';
    }
    const available = !!(editSynced && editSnapshot && editToken && details?.edit_token === editToken);
    const token = available ? editToken : null;
    if (token !== renderReleases.token) {
      renderReleases.token = token;
      releaseKey = '';
      resetReleaseDetail();
    }
    const status = available ? (details.release_status || 'idle') : 'idle';
    $('#release-search').disabled = !available || editBusy || editDirty || releaseBusy ||
      status === 'searching' || status === 'saving';
    const messages = {
      idle: tr('web.text.no_release_search_performed_yet'),
      searching: tr('web.text.searching_musicbrainz_in_the_background'),
      saving: tr('web.text.saving_release_association'),
      not_found: tr('web.text.no_matching_releases_found'),
      error: tr('web.text.release_search_failed'),
      ready: tr('web.dynamic.releases_found', {count: (details?.release_candidates || []).length}),
    };
    $('#release-status').textContent = !available ? tr('web.text.no_editable_cd_inserted') :
      (details.release_error || messages[status] || messages.idle);
    if (status === 'saving') $('#release-confirm').hidden = true;
    const candidates = available && Array.isArray(details.release_candidates) ? details.release_candidates : [];
    const signature = JSON.stringify([token, status, candidates, details?.release_selected_id]);
    if (releaseKey === signature) return;
    releaseKey = signature;
    const container = $('#release-list');
    container.replaceChildren();
    for (const entry of candidates) {
      const row = document.createElement('button');
      row.type = 'button'; row.className = 'release-choice';
      const left = document.createElement('span');
      const heading = document.createElement('strong');
      heading.textContent = entry.title || tr('web.text.untitled_release');
      const sub = document.createElement('small');
      sub.textContent = [entry.artist, entry.country, entry.date,
        Number.isInteger(entry.score) ? tr('web.dynamic.score', {score: entry.score}) : null,
        tr('web.dynamic.tracks', {count: entry.track_count})].filter(Boolean).join(' · ');
      left.append(heading, sub);
      row.appendChild(left);
      if (entry.release_id === details.release_selected_id) {
        const tag = document.createElement('span'); tag.className = 'tag';
        tag.textContent = tr('web.text.assigned'); row.appendChild(tag);
      }
      row.addEventListener('click', () => loadReleaseDetail(entry.release_id));
      container.appendChild(row);
    }
  }

  $('#release-search').addEventListener('click', async () => {
    if (!editSynced || !editSnapshot || !editToken || editBusy || editDirty || releaseBusy) return;
    releaseBusy = true;
    $('#release-search').disabled = true;
    resetReleaseDetail();
    try {
      await request('/api/releases/search', {token: editToken});
      toast(tr('web.text.release_search_started'));
      await refresh();
    } catch (error) { toast(tr('web.text.release_search') + error.message); }
    finally { releaseBusy = false; }
  });

  async function loadReleaseDetail(releaseId) {
    if (!editSynced || !editSnapshot || !editToken || editDirty || editBusy || releaseBusy) {
      toast(tr('web.text.save_or_discard_pending_changes_first'));
      return;
    }
    const token = editToken;
    try {
      const url = '/api/release-detail?token=' + encodeURIComponent(token) +
        '&release_id=' + encodeURIComponent(releaseId);
      const result = await request(url);
      if (token !== editToken || result.release_id !== releaseId) return;
      chosenRelease = result; chosenToken = token;
      $('#release-confirm').hidden = true;
      const panel = $('#release-detail'); panel.replaceChildren();
      const h = document.createElement('h4'); h.textContent = result.title || tr('web.text.release');
      const meta = document.createElement('p');
      meta.textContent = [result.artist, result.country, result.date,
        tr('web.dynamic.tracks', {count: result.track_count})].filter(Boolean).join(' · ');
      const note = document.createElement('p');
      const actual = editSnapshot.details.track_count;
      note.textContent = actual === result.track_count ?
        tr('web.text.track_structure_matches') :
        tr('web.dynamic.track_structure_mismatch', {count: result.track_count, expected: actual});
      const list = document.createElement('ol');
      for (const track of result.tracks || []) {
        const li = document.createElement('li');
        li.textContent = (track.title || tr('web.dynamic.track_number', {number: track.number})) +
          (track.artist ? ` · ${track.artist}` : '');
        list.appendChild(li);
      }
      const select = document.createElement('button');
      select.type = 'button'; select.className = 'btn';
      select.textContent = tr('web.text.assign_this_release');
      select.addEventListener('click', confirmReleaseChoice);
      panel.append(h, meta, note, list, select); panel.hidden = false;
    } catch (error) { toast(tr('web.text.release_details') + error.message); }
  }

  function confirmReleaseChoice() {
    if (!editSynced || !chosenRelease || chosenToken !== editToken || !editSnapshot || editDirty || editBusy) {
      toast(tr('web.text.check_the_current_cd_and_editor_state')); return;
    }
    const protectedFields = editSnapshot.details.manual_fields || [];
    if (!protectedFields.length) { submitRelease(true); return; }
    const trackProtected = protectedFields.some(key => key.includes(':'));
    const unsafe = trackProtected && chosenRelease.can_keep_track_edits !== true;
    $('#release-confirm-text').textContent = unsafe ?
      tr('web.text.the_existing_track_association_cannot_safely_be_transferred') :
      tr('web.dynamic.keep_corrections', {count: protectedFields.length});
    $('#release-keep').disabled = unsafe;
    $('#release-confirm').hidden = false;
  }

  async function submitRelease(keepManual) {
    if (!editSynced || !chosenRelease || chosenToken !== editToken || !editSnapshot || editDirty || editBusy || releaseBusy) return;
    releaseBusy = true; $('#release-confirm').hidden = true;
    try {
      await request('/api/releases/select', {
        token: chosenToken, release_id: chosenRelease.release_id, keep_manual: keepManual,
      });
      toast(tr('web.text.release_association_accepted_saving'));
      await refresh();
    } catch (error) { toast(tr('web.text.release_association_2') + error.message); }
    finally { releaseBusy = false; }
  }

  $('#release-keep').addEventListener('click', () => submitRelease(true));
  $('#release-drop').addEventListener('click', () => submitRelease(false));
  $('#release-cancel').addEventListener('click', () => { $('#release-confirm').hidden = true; });

  async function refresh() {
    try {
      const state = await request('/api/status');
      $('#connection-label').textContent = tr('web.text.player_connected');
      $('#device-reachable').textContent = tr('web.text.device_reachable');
      document.querySelectorAll('.chip .dot,.railfoot .dot').forEach(dot => dot.classList.remove('offline'));
      renderStatus(state);
      // Separate cached read: the compact player status stays unchanged for
      // OLED/buttons/LED; no disc access or speaker discovery on this request.
      let details = null;
      try { details = await request('/api/dashboard'); } catch (_) { /* Old player: placeholders remain. */ }
      renderDashboard(details, state);
      renderEditor(state, details);
      renderCoverTools(state);
      renderReleases(details);
    } catch (_) {
      $('#connection-label').textContent = tr('web.text.player_unavailable');
      $('#device-reachable').textContent = tr('web.text.no_connection');
      document.querySelectorAll('.chip .dot,.railfoot .dot').forEach(dot => dot.classList.add('offline'));
      $('#player-status').textContent = tr('web.text.offline');
      renderMetadataSourceStatus(null);
      $('#target-name').textContent = tr('web.text.unavailable');
      $('#track-title').textContent = tr('web.text.player_unavailable');
      $('#track-artist').textContent = '—';
      $('#track-info').textContent = tr('web.text.no_current_data');
      $('#elapsed').textContent = '—';
      $('#duration').textContent = '—';
      $('.progress').value = 0;
      $('#tracks').replaceChildren();
      renderDashboard.lastTracks = null;
      const placeholder = document.createElement('div');
      placeholder.className = 'trackrow';
      placeholder.textContent = tr('web.text.no_current_track_data');
      $('#tracks').appendChild(placeholder);
      $('#cover-image').hidden = true;
      $('#cover-image').removeAttribute('src');
      $('#art-placeholder').hidden = false;
      coverKey = '';
      $('#delete-disc').disabled = true;
      renderEditor(null, null);
      renderCoverTools(null);
      renderReleases(null);
    }
  }

  // API transport operations are intentionally not linked to this read-only
  // page. Existing /api/command endpoints remain for community integrations.
  async function command(name) {
    if (pending) return;
    pending = true;
    $('#delete-disc').disabled = true;
    try {
      await request('/api/command', {command: name});
    } catch (error) {
      toast(error.message);
    } finally {
      pending = false;
      await refresh();
    }
  }

  const dialog = $('#modal');
  let modalCommand = null;
  function closeModal() { dialog.classList.remove('open'); modalCommand = null; }
  $('#delete-disc').addEventListener('click', () => {
    modalCommand = 'reset-disc';
    $('#modal-title').textContent = tr('web.text.reset_cd_entry_2');
    $('#modal-text').textContent = tr('web.text.delete_cached_metadata_for_the_inserted_cd_and');
    $('#modal-confirm').textContent = tr('web.text.reset');
    dialog.classList.add('open');
    $('#modal-cancel').focus();
  });
  $('#modal-cancel').addEventListener('click', closeModal);
  $('#modal-confirm').addEventListener('click', () => {
    const toRun = modalCommand;
    closeModal();
    if (toRun) command(toRun);
  });
  dialog.addEventListener('click', event => { if (event.target === dialog) closeModal(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });

  // WEB-04a: server-side, paginated, read-only CD library. Never render HTML from metadata.
  let libraryBusy = false;
  let libraryOutstanding = false;
  let libraryOffset = 0;
  let libraryQuery = '';
  let libraryLastSeq = -1;
  const libraryLimit = 20;
  const librarySelected = new Set();
  let libraryMaintenanceSeq = -1;
  let libraryMaintenancePending = null;
  let libraryStats = null;
  let librarySelectionQuery = "";
  function setLibraryStatus(text) { $('#library-status').textContent = text; }
  function libraryNode(tag, value, cls) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = value || '';
    return node;
  }
  async function runLibraryQuery(query, offset) {
    if (libraryBusy || libraryOutstanding) return;
    libraryBusy = true;
    libraryQuery = query.trim();
    libraryOffset = offset;
    libraryDetail = null;
    if (librarySelectionQuery !== libraryQuery) {
      librarySelected.clear();
      librarySelectionQuery = libraryQuery;
    }
    updateLibrarySelection();
    $('#library-detail').hidden = true;
    setLibraryStatus(tr('web.text.searching_library'));
    try {
      await request('/api/library/query', {query: libraryQuery, offset: libraryOffset, limit: libraryLimit});
      libraryOutstanding = true;
    } catch (error) { setLibraryStatus(tr('web.text.search_unavailable') + error.message); }
    finally { libraryBusy = false; }
  }
  async function openLibrary() {
    await runLibraryQuery(libraryQuery, libraryOffset);
    pollLibrary();
    requestLibraryMaintenance('stats');
  }
  // WEB-04b: the library editor never acts on the currently inserted disc.
  let libraryDetail = null;
  let libraryEditTrack = 1;
  let libraryManageSeq = -1;
  let libraryPendingAction = null;

  async function changeStoredDisc(token, action, changes) {
    if (libraryBusy || libraryOutstanding) return;
    libraryBusy = true;
    const payload = {token, action};
    if (action === 'edit') payload.changes = changes;
    try {
      await request('/api/library/manage', payload);
      libraryPendingAction = {token, action};
      libraryOutstanding = true;
      setLibraryStatus(tr('web.text.saving_changes'));
    } catch (error) {
      setLibraryStatus(tr('web.text.change_rejected') + error.message);
    } finally { libraryBusy = false; }
  }

  function renderStoredEditor(entry) {
    const root = $('#library-detail');
    const active = entry.token === editToken;
    const panel = document.createElement('div');
    panel.className = 'library-edit-fields';
    const intro = libraryNode('p', active ?
      tr('web.text.this_cd_is_inserted_please_use_the_editor') :
      tr('web.text.apply_changes_with_save_corrections_empty_fields_remove'), 'help');
    panel.appendChild(intro);
    if (active) { root.appendChild(panel); return; }
    const manual = Array.isArray(entry.manual_fields) ? entry.manual_fields : [];
    const fields = [];
    function addField(caption, field, track, oldValue) {
      const wrap = document.createElement('label');
      wrap.className = 'library-edit-field';
      const marker = track ? `${track}:${field}` : field;
      wrap.appendChild(libraryNode('span', caption + (manual.includes(marker) ? tr('web.text.manual_correction') : '')));
      const input = document.createElement('input');
      input.type = 'text'; input.maxLength = 160; input.value = oldValue || '';
      input.autocomplete = 'off';
      wrap.appendChild(input); panel.appendChild(wrap);
      fields.push({field, track, before: oldValue || '', input});
    }
    addField(tr('web.text.album_title'), 'album', null, entry.album);
    addField(tr('web.text.album_artist'), 'artist', null, entry.artist);
    const tracks = Array.isArray(entry.tracks) ? entry.tracks : [];
    if (tracks.length) {
      const picker = document.createElement('label');
      picker.className = 'library-edit-field';
      picker.appendChild(libraryNode('span', tr('web.text.track_to_edit')));
      const select = document.createElement('select');
      for (const row of tracks) {
        const option = document.createElement('option');
        option.value = String(row.number);
        option.textContent = `${row.number}. ${row.title || tr('web.dynamic.track_number', {number: row.number})}`;
        select.appendChild(option);
      }
      if (!tracks.some(row => row.number === libraryEditTrack)) libraryEditTrack = tracks[0].number;
      select.value = String(libraryEditTrack);
      select.addEventListener('change', event => {
        if (fields.some(x => x.input.value !== x.before) &&
            !window.confirm(tr('web.text.discard_unsaved_corrections'))) {
          select.value = String(libraryEditTrack); return;
        }
        libraryEditTrack = Number(event.target.value);
        renderLibraryDetail(entry);
      });
      picker.appendChild(select); panel.appendChild(picker);
      const row = tracks.find(item => item.number === libraryEditTrack) || {};
      addField(tr('web.text.track_title'), 'track_title', libraryEditTrack, row.title);
      addField(tr('web.text.track_artist'), 'track_artist', libraryEditTrack, row.artist);
    }
    const save = document.createElement('button');
    save.type = 'button'; save.className = 'btn'; save.textContent = tr('web.text.save_corrections');
    save.addEventListener('click', () => {
      const changes = fields.filter(item => item.input.value.trim() !== item.before)
        .map(item => ({field: item.field, ...(item.track ? {track: item.track} : {}),
          value: item.input.value.trim() || null}));
      if (!changes.length) { toast(tr('web.text.no_changes_to_save')); return; }
      changeStoredDisc(entry.token, 'edit', changes);
    });
    panel.appendChild(save);
    panel.appendChild(libraryNode('p', tr('web.text.reset_removes_only_the_automatic_cache_entry_your'), 'help'));
    const tools = document.createElement('div'); tools.className = 'library-manage-actions';
    const actions = [
      [tr('web.text.reset_automatic_association'), 'reset', 'secondary'],
      [tr('web.text.remove_text_corrections'), 'clear-manual', 'secondary'],
      [tr('web.text.delete_cd_metadata'), 'delete', 'danger'],
    ];
    for (const [caption, action, style] of actions) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'btn ' + style;
      button.textContent = caption;
      button.addEventListener('click', () => changeStoredDisc(entry.token, action));
      tools.appendChild(button);
    }
    panel.appendChild(tools); root.appendChild(panel);
  }
  function renderLibraryDetail(entry) {
    const root = $('#library-detail');
    root.replaceChildren();
    if (!entry || typeof entry !== 'object') { root.hidden = true; libraryDetail = null; return; }
    libraryDetail = entry;
    root.hidden = false;
    root.appendChild(libraryNode('h3', entry.album || tr('web.text.unknown_album')));
    root.appendChild(libraryNode('div', (entry.artist || tr('web.text.unknown_artist')) +
      ' · ' + (entry.track_count === null ? tr('web.text.unknown_track_count') : entry.track_count + tr('web.text.tracks')), 'library-meta'));
    root.appendChild(libraryNode('div', tr('web.text.source') + (entry.match_method || tr('web.text.unknown_2')) +
      (entry.confirmed_by_user ? tr('web.text.manually_confirmed_release_association') : '') +
      (entry.manual_cover ? tr('web.text.custom_cover') : ''), 'library-meta'));
    const lines = document.createElement('ol');
    for (const row of Array.isArray(entry.tracks) ? entry.tracks.slice(0, 99) : []) {
      const item = libraryNode('li', (row.title || tr('web.dynamic.track_number', {number: row.number})) +
        (row.artist ? ' · ' + row.artist : ''));
      lines.appendChild(item);
    }
    root.appendChild(lines);
    renderStoredEditor(entry);
  }

  // WEB-04c: explicitly confirmed, bounded maintenance; every operation is
  // scheduled by the player and executed on the existing metadata worker.
  function updateLibrarySelection() {
    const count = librarySelected.size;
    $('#library-selected-count').textContent = count + tr('web.text.cds_selected');
    $('#library-delete-selected').disabled = count === 0 || !!libraryMaintenancePending;
    $('#library-selection-clear').disabled = count === 0 || !!libraryMaintenancePending;
  }
  function formatLibraryBytes(value) {
    if (!Number.isSafeInteger(value) || value < 0) return '—';
    return (value / (1024 * 1024)).toLocaleString(language === 'de' ? 'de-CH' : language, {maximumFractionDigits: 2}) + ' MiB';
  }
  function renderLibraryStats(stats) {
    if (!stats) return;
    libraryStats = stats;
    $('#library-stats').textContent = tr('web.dynamic.storage_summary', {
      discs: stats.disc_count, db: formatLibraryBytes(stats.database_bytes),
      auto: stats.auto_cover_count, manual: stats.manual_cover_count,
      covers: formatLibraryBytes(stats.cover_bytes), budget: formatLibraryBytes(stats.budget_bytes),
      unused: stats.unused_auto_count, bytes: formatLibraryBytes(stats.unused_auto_bytes),
    });
    $('#library-prune-auto').disabled = !stats.unused_auto_count || !!editToken || !!libraryMaintenancePending;
    $('#delete-all').disabled = !stats.disc_count || !!editToken || !!libraryMaintenancePending;
  }
  async function requestLibraryMaintenance(action, extra = {}) {
    if (libraryBusy || (action !== 'stats' && libraryOutstanding) || libraryMaintenancePending) return;
    libraryMaintenancePending = action;
    updateLibrarySelection();
    $('#library-maintenance-status').textContent = action === 'stats' ?
      tr('web.text.loading_storage_overview') : tr('web.text.running_storage_maintenance');
    try {
      await request('/api/library/maintenance', {action, ...extra});
    } catch (error) {
      libraryMaintenancePending = null;
      updateLibrarySelection();
      $('#library-maintenance-status').textContent = tr('web.text.storage_maintenance') + error.message;
    }
  }
  $('#library-selection-clear').addEventListener('click', () => {
    librarySelected.clear();
    document.querySelectorAll('#library-items input[type="checkbox"]').forEach(box => { box.checked = false; });
    updateLibrarySelection();
  });
  $('#library-delete-selected').addEventListener('click', () => {
    const tokens = [...librarySelected];
    if (!tokens.length || tokens.length > 40 ||
        !window.confirm(tr('web.dynamic.confirm_bulk_delete', {count: tokens.length}))) return;
    requestLibraryMaintenance('bulk-delete', {tokens});
  });
  $('#delete-all').addEventListener('click', () => {
    const phrase = 'DELETE ALL METADATA';
    const typed = window.prompt(tr('web.dynamic.confirm_clear_all', {phrase}));
    if (typed === phrase) requestLibraryMaintenance('clear-all', {confirmation: phrase});
  });
  $('#library-prune-auto').addEventListener('click', () => {
    const n = libraryStats?.unused_auto_count || 0;
    if (!n || !window.confirm(tr('web.dynamic.confirm_prune', {count: n}))) return;
    requestLibraryMaintenance('prune-auto', {confirmation: 'DELETE UNUSED AUTO COVERS'});
  });
  $('#library-refresh-stats').addEventListener('click', () => requestLibraryMaintenance('stats'));
  function renderLibrary(data) {
    const root = $('#library-items');
    root.replaceChildren();
    const entries = Array.isArray(data.entries) ? data.entries : [];
    for (const disc of entries) {
      const wrapper = document.createElement('div');
      wrapper.className = 'library-select-row';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.setAttribute('aria-label', tr('web.text.select_cd_for_deletion') + (disc.album || tr('web.text.unknown_album')));
      checkbox.disabled = disc.token === editToken;
      checkbox.checked = librarySelected.has(disc.token);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && librarySelected.size >= 40) {
          checkbox.checked = false; toast(tr('web.text.no_more_than_40_cds_per_deletion')); return;
        }
        if (checkbox.checked) librarySelected.add(disc.token);
        else librarySelected.delete(disc.token);
        updateLibrarySelection();
      });
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'library-entry';
      button.appendChild(libraryNode('strong', disc.album || tr('web.text.unknown_album')));
      const date = Number.isInteger(disc.updated_at) && disc.updated_at > 0
        ? new Date(disc.updated_at * 1000).toLocaleDateString(language === 'de' ? 'de-CH' : language) : tr('web.text.date_unknown');
      button.appendChild(libraryNode('small', (disc.artist || tr('web.text.unknown_artist')) +
        ' · ' + (disc.track_count === null ? tr('web.text.unknown_track_count') : disc.track_count + tr('web.text.tracks')) + ' · ' + date +
        (disc.manual_cover ? tr('web.text.custom_cover') : '')));
      button.addEventListener('click', async () => {
        if (libraryBusy || libraryOutstanding) return;
        libraryBusy = true;
        setLibraryStatus(tr('web.text.loading_cd_details'));
        try { await request('/api/library/detail', {token: disc.token}); libraryOutstanding = true; }
        catch (error) { setLibraryStatus(tr('web.text.details_unavailable') + error.message); }
        finally { libraryBusy = false; }
      });
      wrapper.append(checkbox, button);
      root.appendChild(wrapper);
    }
    if (!entries.length) root.appendChild(libraryNode('p', tr('web.text.no_matching_cds_stored')));
    const total = Number.isInteger(data.total) ? data.total : 0;
    const offset = Number.isInteger(data.offset) ? data.offset : 0;
    $('#library-count').textContent = total ? tr('web.dynamic.page_counter', {first: offset + 1, last: Math.min(total, offset + entries.length), total}) : tr('web.text.0_entries');
    $('#library-prev').disabled = offset <= 0;
    $('#library-next').disabled = offset + entries.length >= total || !entries.length;
  }
  async function pollLibrary() {
    try {
      const reply = await request('/api/library');
      const data = reply.library || {};

      const maintenance = data.maintenance || {};
      if (Number.isInteger(maintenance.seq) && maintenance.seq > libraryMaintenanceSeq) {
        libraryMaintenanceSeq = maintenance.seq;
        if (maintenance.stats) renderLibraryStats(maintenance.stats);
        if (libraryMaintenancePending && maintenance.action === libraryMaintenancePending &&
            (maintenance.status === 'ready' || maintenance.status === 'error')) {
          const finished = libraryMaintenancePending;
          libraryMaintenancePending = null;
          if (maintenance.status === 'ready' && finished !== 'stats') librarySelected.clear();
          updateLibrarySelection();
          $('#library-maintenance-status').textContent = maintenance.status === 'error' ?
            (maintenance.error || tr('web.text.storage_maintenance_failed')) :
            (finished === 'stats' ? tr('web.text.storage_information_refreshed') :
              tr('web.dynamic.maintenance_done', {count: maintenance.changed_count || 0}));
          if (maintenance.status === 'ready' && finished !== 'stats') {
            await runLibraryQuery(libraryQuery, 0);
            return;
          }
        }
      }
      const manage = data.manage || {};
      if (Number.isInteger(manage.seq) && manage.seq > libraryManageSeq) {
        libraryManageSeq = manage.seq;
        if (libraryPendingAction && manage.token === libraryPendingAction.token &&
            (manage.status === 'ready' || manage.status === 'error')) {
          const finished = libraryPendingAction;
          libraryPendingAction = null;
          libraryOutstanding = false;
          if (manage.status === 'error') {
            setLibraryStatus(manage.error || tr('web.text.change_failed'));
            return;
          }
          toast(tr('web.text.change_saved_refreshing_library'));
          if ((finished.action === 'edit' || (finished.action === 'clear-manual' && libraryDetail?.cached)) &&
              libraryDetail?.token === finished.token) {
            try {
              await request('/api/library/detail', {token: finished.token});
              libraryOutstanding = true;
              setLibraryStatus(tr('web.text.loading_updated_cd_details'));
            } catch (error) {
              await runLibraryQuery(libraryQuery, libraryOffset);
            }
          } else {
            await runLibraryQuery(libraryQuery, libraryOffset);
          }
          return;
        }
      }
      if (!Number.isInteger(data.seq) || data.seq <= libraryLastSeq) return;
      libraryLastSeq = data.seq;
      if (data.status === 'loading') { setLibraryStatus(tr('web.text.loading_library')); return; }
      if (data.status === 'error') { libraryOutstanding = false; setLibraryStatus(data.error || tr('web.text.cd_library_unavailable')); return; }
      if (data.status !== 'ready') return;
      libraryOutstanding = false;
      setLibraryStatus(tr('web.dynamic.stored_cds', {count: data.total || 0}));
      if (data.detail) { renderLibraryDetail(data.detail); return; }
      renderLibrary(data);
    } catch (error) { setLibraryStatus(tr('web.text.cd_library_unreachable') + error.message); }
  }
  $('#library-find').addEventListener('click', () => runLibraryQuery($('#library-search').value, 0));
  $('#library-reload').addEventListener('click', () => runLibraryQuery(libraryQuery, libraryOffset));
  $('#library-search').addEventListener('keydown', event => { if (event.key === 'Enter') runLibraryQuery(event.target.value, 0); });
  $('#library-prev').addEventListener('click', () => runLibraryQuery(libraryQuery, Math.max(0, libraryOffset - libraryLimit)));
  $('#library-next').addEventListener('click', () => runLibraryQuery(libraryQuery, libraryOffset + libraryLimit));

  async function initialize() {
    try {
      await loadLanguage();
    } catch (_) {
      // No duplicate fallback text in JS; an unavailable catalog blocks UI.
      document.documentElement.dataset.i18nError = 'true';
      return;
    }
    paintTheme();
    loadTheme();
    refresh();
    setInterval(() => { if (!document.hidden && $('#library').classList.contains('active')) pollLibrary(); }, 1400);
    setInterval(() => { if (!pending && !document.hidden) refresh(); }, 2000);
  }
  initialize();
})();

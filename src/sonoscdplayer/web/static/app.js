/* SonosCDPlayer: approved light/petrol and dark/warm UI with live, bounded JSON API. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  let theme = 'auto';
  let coverKey = '';
  let pending = false;
  const darkMedia = window.matchMedia('(prefers-color-scheme: dark)');

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
    $('#theme-example-label').textContent = 'Aktives Farbschema: ' +
      (actual === 'dark' ? 'Dunkel / Espresso & Bernstein' : 'Hell / Petrol') +
      (theme === 'auto' ? ' (automatisch)' : '');
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
      toast('Farbschema nicht gespeichert: ' + err.message);
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
    $('#pagetitle').textContent = name === 'playback' ? 'Jetzt wiedergeben' : 'Einstellungen';
    $('#eyebrow').textContent = name === 'playback' ? 'Musik hören' : 'Gerät verwalten';
  }
  function showPanel(panel) {
    document.querySelectorAll('.settings-nav button').forEach(button => button.classList.toggle('active', button.dataset.panel === panel));
    document.querySelectorAll('.subpanel').forEach(section => section.classList.toggle('active', section.id === panel));
    showPage('settings');
  }
  document.querySelectorAll('.navbtn').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
  document.querySelectorAll('.settings-nav button').forEach(button => button.addEventListener('click', () => showPanel(button.dataset.panel)));
  document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', () => saveTheme(button.dataset.themeChoice)));
  $('#theme-setting').addEventListener('change', event => saveTheme(event.target.value));
  $('#save-theme').addEventListener('click', () => saveTheme($('#theme-setting').value));
  $('#edit-metadata').addEventListener('click', () => showPanel('metadata'));

  function formattedTime(number) {
    if (typeof number !== 'number' || !Number.isFinite(number) || number < 0) return '—';
    const seconds = Math.floor(number);
    return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
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
    const album = noDisc ? 'Keine CD eingelegt' : (metadata.album || (count ? 'Audio-CD' : 'Keine CD erkannt'));
    const title = noDisc ? 'Keine Wiedergabe' : (metadata.track_title || (current ? `Track ${current}` : 'Keine Wiedergabe'));
    const artist = noDisc ? '—' : (metadata.track_artist || metadata.artist || '—');
    $('#player-status').textContent = ({NO_DISC:'KEINE CD', PLAYING:'WIEDERGABE', PAUSED:'PAUSIERT', STARTING:'WIRD GESTARTET', STOPPED:'GESTOPPT', ERROR:'FEHLER'})[mode] || mode;
    $('#track-title').textContent = title;
    $('#track-artist').textContent = artist;
    $('#track-info').textContent = noDisc ? 'Keine CD eingelegt' : (count ? `Titel ${current || 1} von ${count} · ${album}` : 'Keine abspielbare CD');
    $('#art-album').textContent = noDisc ? album : `${album} · ${metadata.artist || 'Unbekannter Interpret'}`;
    $('#library-current').textContent = album;

    $('#delete-disc').disabled = pending || !count;

    const progress = state.progress || {};
    const elapsed = Number(progress.elapsed_seconds ?? progress.elapsed ?? progress.position);
    const duration = Number(progress.duration_seconds ?? progress.total ?? progress.duration);
    const valid = active && Number.isFinite(elapsed) && elapsed >= 0 && Number.isFinite(duration) && duration > 0;
    $('#elapsed').textContent = valid ? formattedTime(elapsed) : '—';
    $('#duration').textContent = valid ? formattedTime(duration) : '—';
    $('.progress').value = valid ? Math.round(Math.min(100, 100 * elapsed / duration)) : 0;

    const identity = [album, noDisc ? null : metadata.artist, !noDisc && state.has_cover].join('|');
    if (coverKey !== identity) {
      coverKey = identity;
      const img = $('#cover-image');
      img.hidden = noDisc || !state.has_cover;
      img.src = !noDisc && state.has_cover ? '/api/cover?album=' + encodeURIComponent(album) : '';
      $('#art-placeholder').hidden = !noDisc && !!state.has_cover;
      img.onerror = () => {img.hidden = true; $('#art-placeholder').hidden = false;};
    }
    const rejection = state.last_rejection || {};
    if (rejection.seq && rejection.seq !== renderStatus.lastRejection) {
      renderStatus.lastRejection = rejection.seq;
      if (rejection.reason) toast('Wiedergabe abgelehnt: ' + rejection.reason);
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
        empty.textContent = 'Keine Titelliste verfügbar';
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
          title.textContent = (typeof entry.title === 'string' && entry.title ? entry.title : `Track ${number}`) +
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
    $('#target-label').textContent = isPlaying ? 'Wiedergabe auf' : 'Ausgewähltes Sonos-Ziel';
    $('#target-name').textContent = !freshTarget ? 'Ziel wird aktualisiert …' :
      !countSpeakers ? 'Kein Sonos-Ziel ausgewählt' :
      names.length ? names.join(' · ') + (missing ? ` · +${missing} weitere` : '') :
      countSpeakers === 1 ? '1 Lautsprecher ausgewählt' : `${countSpeakers} Lautsprecher ausgewählt`;
  }

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
      $('#meta-edit-status').textContent = 'Metadaten momentan nicht erreichbar';
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
      $('#meta-edit-status').textContent = 'Metadaten werden synchronisiert …';
      return;
    }
    const available = !!token && count > 0 && status?.player?.track_count === count;
    const newDisc = token !== editToken;
    if (newDisc) {
      if (editDirty) toast('CD wurde gewechselt – ungespeicherte Eingaben verworfen.');
      editToken = token;
      editDirty = false;
      lastEditSeq = Number(details?.edit_seq) || 0;
      editTrack = 0;
    }
    editSnapshot = available ? {status, details} : null;
    editSynced = available;
    if (available && (Number(details.edit_seq) || 0) > lastEditSeq) {
      lastEditSeq = details.edit_seq;
      if (details.edit_error) toast('Speichern fehlgeschlagen. Bitte Status prüfen.');
    }
    editInputs.forEach(selector => { $(selector).disabled = !available || editBusy; });
    $('#meta-track-select').disabled = !available || editBusy;
    $('#meta-save').disabled = !available || editBusy;
    $('#meta-discard').disabled = !available || editBusy || !editDirty;
    if (!available) {
      $('#meta-edit-status').textContent = 'Kein bearbeitbarer CD-Eintrag verfügbar';
      $('#meta-source').textContent = status?.player?.mode === 'NO_DISC' || status?.disc?.mode === 'NO_DISC' ? 'Keine CD' : 'Nicht verfügbar';
      $('#album').placeholder = status?.player?.mode === 'NO_DISC' || status?.disc?.mode === 'NO_DISC' ? 'Keine CD eingelegt' : 'Albumtitel';
      $('#meta-track-select').replaceChildren();
      editInputs.forEach(selector => { $(selector).value = ''; });
      return;
    }
    $('#album').placeholder = 'Albumtitel';
    if (newDisc || !editTrack || editTrack > count) {
      editTrack = Math.max(1, Math.min(count, Number(status.player?.track) || 1));
      const select = $('#meta-track-select');
      select.replaceChildren();
      for (let n = 1; n <= count; n++) {
        const option = document.createElement('option');
        option.value = String(n);
        option.textContent = `${n}. ${details.tracks?.[n - 1]?.title || 'Track ' + n}`;
        select.appendChild(option);
      }
      select.value = String(editTrack);
    }
    if (!editDirty && !editBusy) populateEditor();
    const manual = Array.isArray(details.manual_fields) ? details.manual_fields : [];
    $('#meta-source').textContent = manual.length ? 'Manuell korrigiert' :
      (details.metadata_source || status.metadata?.match_status || 'TOC');
    $('#meta-edit-status').textContent = editDirty ? 'Ungespeicherte Änderungen' :
      `${manual.length} manuelle Feldkorrektur(en) gespeichert`;
  }

  editInputs.forEach(selector => $(selector).addEventListener('input', () => {
    editDirty = true;
    $('#meta-discard').disabled = false;
    $('#meta-edit-status').textContent = 'Ungespeicherte Änderungen';
  }));
  $('#meta-discard').addEventListener('click', () => populateEditor());
  $('#meta-track-select').addEventListener('change', event => {
    if (editDirty) toast('Ungespeicherte Änderungen wurden verworfen.');
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
    if (!edits.length) { editDirty = false; toast('Keine Änderungen vorhanden.'); return; }
    editBusy = true;
    $('#meta-save').disabled = true;
    $('#meta-discard').disabled = true;
    try {
      for (const edit of edits) await request('/api/metadata', edit);
      editDirty = false;
      toast('Korrekturen angenommen. Anzeige wird aktualisiert …');
      await refresh();
    } catch (error) {
      toast('Korrektur nicht vollständig gespeichert: ' + error.message);
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
      idle: 'Noch keine Release-Suche durchgeführt.',
      searching: 'MusicBrainz wird im Hintergrund abgefragt …',
      saving: 'Release-Zuordnung wird gespeichert …',
      not_found: 'Keine passenden Veröffentlichungen gefunden.',
      error: 'Release-Suche fehlgeschlagen.',
      ready: `${(details?.release_candidates || []).length} Veröffentlichung(en) gefunden.`,
    };
    $('#release-status').textContent = !available ? 'Keine bearbeitbare CD eingelegt.' :
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
      heading.textContent = entry.title || 'Unbenannte Veröffentlichung';
      const sub = document.createElement('small');
      sub.textContent = [entry.artist, entry.country, entry.date,
        Number.isInteger(entry.score) ? `Vergleichswert ${entry.score}/100` : null,
        `${entry.track_count} Tracks`].filter(Boolean).join(' · ');
      left.append(heading, sub);
      row.appendChild(left);
      if (entry.release_id === details.release_selected_id) {
        const tag = document.createElement('span'); tag.className = 'tag';
        tag.textContent = 'Zugeordnet'; row.appendChild(tag);
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
      toast('Release-Suche gestartet.');
      await refresh();
    } catch (error) { toast('Release-Suche: ' + error.message); }
    finally { releaseBusy = false; }
  });

  async function loadReleaseDetail(releaseId) {
    if (!editSynced || !editSnapshot || !editToken || editDirty || editBusy || releaseBusy) {
      toast('Bitte zuerst offene Änderungen speichern oder verwerfen.');
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
      const h = document.createElement('h4'); h.textContent = result.title || 'Veröffentlichung';
      const meta = document.createElement('p');
      meta.textContent = [result.artist, result.country, result.date,
        `${result.track_count} Tracks`].filter(Boolean).join(' · ');
      const note = document.createElement('p');
      const actual = editSnapshot.details.track_count;
      note.textContent = actual === result.track_count ?
        'Trackstruktur stimmt überein.' :
        `Abweichende Trackstruktur (${result.track_count} statt ${actual}); die Übernahme erfolgt nach Tracknummer.`;
      const list = document.createElement('ol');
      for (const track of result.tracks || []) {
        const li = document.createElement('li');
        li.textContent = (track.title || `Track ${track.number}`) +
          (track.artist ? ` · ${track.artist}` : '');
        list.appendChild(li);
      }
      const select = document.createElement('button');
      select.type = 'button'; select.className = 'btn';
      select.textContent = 'Diese Veröffentlichung zuordnen';
      select.addEventListener('click', confirmReleaseChoice);
      panel.append(h, meta, note, list, select); panel.hidden = false;
    } catch (error) { toast('Release-Details: ' + error.message); }
  }

  function confirmReleaseChoice() {
    if (!editSynced || !chosenRelease || chosenToken !== editToken || !editSnapshot || editDirty || editBusy) {
      toast('Bitte aktuellen CD- und Bearbeitungsstatus prüfen.'); return;
    }
    const protectedFields = editSnapshot.details.manual_fields || [];
    if (!protectedFields.length) { submitRelease(true); return; }
    const trackProtected = protectedFields.some(key => key.includes(':'));
    const unsafe = trackProtected && chosenRelease.can_keep_track_edits !== true;
    $('#release-confirm-text').textContent = unsafe ?
      'Die bisherige Trackzuordnung lässt sich nicht sicher auf diese Veröffentlichung übertragen. Bitte Korrekturen verwerfen oder abbrechen.' :
      `${protectedFields.length} manuelle Korrektur(en) vorhanden. Möchtest du diese beim Release-Wechsel behalten oder verwerfen?`;
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
      toast('Release-Zuordnung angenommen. Speichervorgang läuft …');
      await refresh();
    } catch (error) { toast('Release-Zuordnung: ' + error.message); }
    finally { releaseBusy = false; }
  }

  $('#release-keep').addEventListener('click', () => submitRelease(true));
  $('#release-drop').addEventListener('click', () => submitRelease(false));
  $('#release-cancel').addEventListener('click', () => { $('#release-confirm').hidden = true; });

  async function refresh() {
    try {
      const state = await request('/api/status');
      $('#connection-label').textContent = 'Player verbunden';
      $('#device-reachable').textContent = 'Gerät erreichbar';
      document.querySelectorAll('.chip .dot,.railfoot .dot').forEach(dot => dot.classList.remove('offline'));
      renderStatus(state);
      // Separate cached read: the compact player status stays unchanged for
      // OLED/buttons/LED; no disc access or speaker discovery on this request.
      let details = null;
      try { details = await request('/api/dashboard'); } catch (_) { /* Old player: placeholders remain. */ }
      renderDashboard(details, state);
      renderEditor(state, details);
      renderReleases(details);
    } catch (_) {
      $('#connection-label').textContent = 'Player nicht erreichbar';
      $('#device-reachable').textContent = 'Keine Verbindung';
      document.querySelectorAll('.chip .dot,.railfoot .dot').forEach(dot => dot.classList.add('offline'));
      $('#player-status').textContent = 'OFFLINE';
      $('#target-name').textContent = 'Nicht verfügbar';
      $('#track-title').textContent = 'Player nicht erreichbar';
      $('#track-artist').textContent = '—';
      $('#track-info').textContent = 'Keine aktuellen Daten';
      $('#elapsed').textContent = '—';
      $('#duration').textContent = '—';
      $('.progress').value = 0;
      $('#tracks').replaceChildren();
      renderDashboard.lastTracks = null;
      const placeholder = document.createElement('div');
      placeholder.className = 'trackrow';
      placeholder.textContent = 'Keine aktuellen Titeldaten';
      $('#tracks').appendChild(placeholder);
      $('#cover-image').hidden = true;
      $('#cover-image').removeAttribute('src');
      $('#art-placeholder').hidden = false;
      coverKey = '';
      $('#delete-disc').disabled = true;
      renderEditor(null, null);
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
    $('#modal-title').textContent = 'CD-Eintrag zurücksetzen?';
    $('#modal-text').textContent = 'Metadaten der eingelegten CD aus dem Cache löschen und neu suchen?';
    $('#modal-confirm').textContent = 'Zurücksetzen';
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

  paintTheme();
  loadTheme();
  refresh();
  setInterval(() => { if (!pending && !document.hidden) refresh(); }, 2000);
})();

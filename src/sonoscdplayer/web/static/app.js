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
    const album = metadata.album || (count ? 'Audio-CD' : 'Keine CD erkannt');
    const title = metadata.track_title || (current ? `Track ${current}` : 'Keine Wiedergabe');
    const artist = metadata.track_artist || metadata.artist || '—';
    $('#player-status').textContent = ({PLAYING:'WIEDERGABE', PAUSED:'PAUSIERT', STARTING:'WIRD GESTARTET', STOPPED:'GESTOPPT', ERROR:'FEHLER'})[mode] || mode;
    $('#track-title').textContent = title;
    $('#track-artist').textContent = artist;
    $('#track-info').textContent = count ? `Titel ${current || 1} von ${count} · ${album}` : 'Keine abspielbare CD';
    $('#art-album').textContent = `${album} · ${metadata.artist || 'Unbekannter Interpret'}`;
    $('#library-current').textContent = album;
    $('#album').value = metadata.album || '';
    $('#artist').value = metadata.artist || '';
    $('#title2').value = metadata.track_title || '';
    $('#meta-source').textContent = metadata.match_status || 'TOC / keine Angaben';

    $('#delete-disc').disabled = pending || !count;

    const progress = state.progress || {};
    const elapsed = Number(progress.elapsed_seconds ?? progress.elapsed ?? progress.position);
    const duration = Number(progress.duration_seconds ?? progress.total ?? progress.duration);
    const valid = active && Number.isFinite(elapsed) && elapsed >= 0 && Number.isFinite(duration) && duration > 0;
    $('#elapsed').textContent = valid ? formattedTime(elapsed) : '—';
    $('#duration').textContent = valid ? formattedTime(duration) : '—';
    $('.progress').value = valid ? Math.round(Math.min(100, 100 * elapsed / duration)) : 0;

    const identity = [album, metadata.artist, state.has_cover].join('|');
    if (coverKey !== identity) {
      coverKey = identity;
      const img = $('#cover-image');
      img.hidden = !state.has_cover;
      img.src = state.has_cover ? '/api/cover?album=' + encodeURIComponent(album) : '';
      $('#art-placeholder').hidden = !!state.has_cover;
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
